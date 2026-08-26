"""使用已下载 PDF 文字层原地回填国信 e 采 JSON/CSV。"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from crawler_scrapy.pipelines import _to_json_compatible
from crawler_scrapy.schemas.notice_fields import (
    canonicalize_attachment_list,
    canonicalize_notice_data,
    coerce_datetime,
    get_missing_fields,
    get_notice_fields,
)
from crawler_scrapy.sites.gxebidding.parser import (
    DetailDocument,
    GxebiddingParser,
    extract_pdf_text,
)
from crawler_scrapy.sites.runshihua.reparse_output import (
    _atomic_json,
    _safe_pdf_path,
    _select_pdf,
    _sync_csv,
    _usable_pdf_text,
)
from crawler_scrapy.storage.source_snapshots import load_record_payload


def _payload_parts(
    row: Mapping[str, Any], output_root: Path
) -> tuple[str, str, dict[str, Any], DetailDocument]:
    payload = load_record_payload(row, output_root)
    if not isinstance(payload, Mapping):
        raise ValueError(f"公告 {row.get('公告ID')} 缺少payload快照")
    channel = str(payload.get("sourceChannel") or "").strip()
    category = str(payload.get("sourceCategory") or "").strip()
    list_record = payload.get("list")
    detail = payload.get("detail")
    if (
        not channel
        or not category
        or not isinstance(list_record, Mapping)
        or not isinstance(detail, Mapping)
    ):
        raise ValueError(f"公告 {row.get('公告ID')} 的payload结构不完整")
    return channel, category, dict(list_record), DetailDocument(**dict(detail))


def _update_row(
    row: dict[str, Any], output_root: Path
) -> tuple[bool, str]:
    attachment, pdf_path = _select_pdf(row, output_root)
    if attachment is None or pdf_path is None:
        return False, "NO_DOWNLOADED_PDF"
    pdf_text = extract_pdf_text(pdf_path.read_bytes(), timeout=180, mode="layout")
    usable = _usable_pdf_text(pdf_text)
    table_text = (
        extract_pdf_text(pdf_path.read_bytes(), timeout=180, mode="raw")
        if usable else ""
    )
    status = "TEXT_EXTRACTED" if usable else "PDF_TEXT_LAYER_UNUSABLE"
    channel, category, list_record, detail = _payload_parts(row, output_root)
    parsed = GxebiddingParser.parse(
        channel,
        category,
        list_record,
        detail,
        pdf_text=pdf_text if usable else "",
        table_text=table_text,
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    before = {
        field: row.get(field) for field in get_notice_fields(parsed.notice_type)
    }
    for field in get_notice_fields(parsed.notice_type):
        row[field] = data.get(field, "")
    attachment["parse_status"] = (
        "TEXT_EXTRACTED" if usable else "DOWNLOADED_NO_OCR"
    )
    row["附件"] = canonicalize_attachment_list(row.get("附件"))
    row["公告标题"] = parsed.title
    row["发布时间"] = parsed.publish_time or row.get("发布时间")
    row["公告正文"] = parsed.raw_text
    row["缺失字段"] = get_missing_fields(parsed.notice_type, data)
    row["解析状态"] = (
        "PARSED"
        if parsed.title and data.get("项目名称") and parsed.raw_text
        else "PARTIAL"
    )
    row["抽取版本"] = GxebiddingParser.parser_version
    row["内容指纹"] = hashlib.sha256(parsed.raw_text.encode()).hexdigest()
    # 旧验证输出可能仍带毫秒；重新解析时一并迁移到当前的秒级传输格式。
    crawl_time = coerce_datetime(row.get("爬虫时间"))
    if crawl_time is not None:
        row["爬虫时间"] = crawl_time.replace(microsecond=0)

    trace = row.get("_trace")
    if isinstance(trace, dict):
        trace["crawlerVersion"] = GxebiddingParser.parser_version
        trace["extractionVersion"] = GxebiddingParser.parser_version
        field_meta = trace.setdefault("fieldMeta", {})
        if isinstance(field_meta, dict):
            field_meta.update({
                "body_format": "pdf" if usable else "pdf_pending_ocr",
                "offlinePdfReparse": True,
                "pdfTextLength": len(pdf_text if usable else ""),
                "validation_warnings": list(parsed.validation_warnings),
            })
        response_meta = trace.setdefault("responseMetadata", {})
        if isinstance(response_meta, dict):
            response_meta["bodyPdf"] = {
                "requestKind": "downloaded_notice_pdf",
                "storagePath": attachment.get("storage_path"),
                "fileHash": attachment.get("file_hash"),
                "fileSizeBytes": attachment.get("file_size_bytes"),
                "textLength": len(pdf_text if usable else ""),
                "extractor": "pdftotext-layout" if usable else "no-usable-text-layer",
            }
    after = {
        field: _to_json_compatible(data).get(field) for field in before
    }
    return before != after or not usable, status


def reparse(output_root: Path) -> int:
    site_dir = output_root / "gxebidding"
    paths = sorted((site_dir / "json").glob("*.json"))
    if not paths:
        print(f"没有找到JSON：{site_dir / 'json'}", file=sys.stderr)
        return 2
    total = changed = extracted = unusable = skipped = 0
    for path in paths:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"JSON不是数组：{path}")
        dirty = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            total += 1
            row_changed, status = _update_row(row, output_root)
            dirty = dirty or row_changed
            changed += int(row_changed and status == "TEXT_EXTRACTED")
            extracted += int(status == "TEXT_EXTRACTED")
            unusable += int(status == "PDF_TEXT_LAYER_UNUSABLE")
            skipped += int(status == "NO_DOWNLOADED_PDF")
        if dirty:
            _atomic_json(path, rows)
            _sync_csv(site_dir / "csv" / f"{path.stem}.csv", rows)
        print(f"[PDF回填] {path.name}: {len(rows)}条", flush=True)
    print(
        f"[PDF回填完成] 总数={total} 已提取={extracted} "
        f"字段有变化={changed} 无文字层={unusable} 待下载={skipped}",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从已下载PDF重算国信e采结果")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    args = parser.parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    lock_path = output_root / "gxebidding" / "state" / "resumable.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("gxebidding 正在运行，拒绝回填JSON/CSV", file=sys.stderr)
            return 5
        return reparse(output_root)


if __name__ == "__main__":
    raise SystemExit(main())
