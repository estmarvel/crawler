"""使用已下载 PDF 的文字层原地补全润世和 JSON/CSV 字段。"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from crawler_scrapy.pipelines import _to_json_compatible
from crawler_scrapy.schemas.notice_fields import (
    canonicalize_attachment_list,
    canonicalize_notice_data,
    get_missing_fields,
    get_notice_fields,
)
from crawler_scrapy.sites.runshihua.parser import RunshihuaParser, extract_pdf_text
from crawler_scrapy.storage.source_snapshots import (
    load_record_html,
    load_record_payload,
)


def _atomic_json(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as file_object:
        json.dump(_to_json_compatible(rows), file_object, ensure_ascii=False, indent=2)
        file_object.write("\n")
        file_object.flush()
        os.fsync(file_object.fileno())
    temporary.replace(path)


def _serialize(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(_to_json_compatible(value), ensure_ascii=False)
    return value if value is not None else ""


def _sync_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        fieldnames = list(csv.DictReader(source).fieldnames or [])
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _serialize(row.get(name, "")) for name in fieldnames})
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


def _safe_pdf_path(output_root: Path, value: object) -> Path | None:
    relative = str(value or "").strip()
    if not relative:
        return None
    root = output_root.expanduser().resolve()
    path = (root / relative).resolve()
    if path == root or root not in path.parents:
        raise ValueError(f"附件路径越出输出目录：{relative}")
    if not path.is_file():
        return None
    with path.open("rb") as file_object:
        if file_object.read(5) != b"%PDF-":
            raise ValueError(f"已下载附件不是PDF：{path}")
    return path


def _usable_pdf_text(value: str) -> bool:
    compact = re.sub(r"\s+", "", str(value or ""))
    if len(compact) < 80:
        return False
    chinese = len(re.findall(r"[\u4e00-\u9fff]", compact))
    alphanumeric = len(re.findall(r"[A-Za-z0-9]", compact))
    return chinese >= 10 or alphanumeric >= 60


def _payload_parts(
    row: Mapping[str, Any], output_root: Path
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    payload = load_record_payload(row, output_root)
    if not isinstance(payload, Mapping):
        raise ValueError(f"公告 {row.get('公告ID')} 缺少payload快照")
    category = str(payload.get("sourceCategory") or "").strip()
    list_record = payload.get("list")
    detail = payload.get("detail")
    if not category or not isinstance(list_record, Mapping) or not isinstance(detail, Mapping):
        raise ValueError(f"公告 {row.get('公告ID')} 的payload结构不完整")
    reconstructed = dict(detail)
    raw_html = load_record_html(row, output_root)
    html_fields = payload.get("htmlFields")
    if raw_html and isinstance(html_fields, Mapping):
        for key in html_fields:
            if key in {
                "gcjsPublicityContent",
                "noticeContent",
                "publicityContent",
                "alterationContent",
            }:
                reconstructed[str(key)] = raw_html
    return category, dict(list_record), reconstructed


def _select_pdf(
    row: Mapping[str, Any], output_root: Path
) -> tuple[dict[str, Any] | None, Path | None]:
    attachments = row.get("附件")
    if not isinstance(attachments, list):
        return None, None
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        file_type = str(attachment.get("file_type") or "").lower()
        file_name = str(attachment.get("file_name") or "").lower()
        if "pdf" not in file_type and not file_name.endswith(".pdf"):
            continue
        path = _safe_pdf_path(output_root, attachment.get("storage_path"))
        if path:
            return attachment, path
    return None, None


def _update_row(
    row: dict[str, Any], output_root: Path
) -> tuple[bool, str]:
    attachment, pdf_path = _select_pdf(row, output_root)
    if attachment is None or pdf_path is None:
        return False, "NO_DOWNLOADED_PDF"
    pdf_text = extract_pdf_text(pdf_path.read_bytes(), timeout=180, mode="layout")
    text_usable = _usable_pdf_text(pdf_text)
    effective_pdf_text = pdf_text if text_usable else ""
    status = "TEXT_EXTRACTED" if text_usable else "PDF_TEXT_LAYER_UNUSABLE"

    category, list_record, detail = _payload_parts(row, output_root)
    parsed = RunshihuaParser.parse(
        category,
        detail,
        list_record=list_record,
        pdf_text=effective_pdf_text,
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    before = {field: row.get(field) for field in get_notice_fields(parsed.notice_type)}
    for field, value in data.items():
        row[field] = value
    attachment["parse_status"] = (
        "TEXT_EXTRACTED" if text_usable else "DOWNLOADED_NO_OCR"
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
    row["抽取版本"] = RunshihuaParser.parser_version
    row["内容指纹"] = hashlib.sha256(parsed.raw_text.encode("utf-8")).hexdigest()

    trace = row.get("_trace")
    if isinstance(trace, dict):
        trace["crawlerVersion"] = RunshihuaParser.parser_version
        trace["extractionVersion"] = RunshihuaParser.parser_version
        field_meta = trace.setdefault("fieldMeta", {})
        if isinstance(field_meta, dict):
            field_meta.update({
                "body_format": (
                    "html+pdf" if parsed.raw_html and text_usable
                    else "html" if parsed.raw_html
                    else "pdf" if text_usable
                    else "structured_api"
                ),
                "offlinePdfReparse": True,
                "pdfTextLength": len(effective_pdf_text),
                "validation_warnings": list(parsed.validation_warnings),
            })
        response_meta = trace.setdefault("responseMetadata", {})
        if isinstance(response_meta, dict):
            response_meta["bodyPdf"] = {
                "requestKind": "downloaded_notice_pdf",
                "storagePath": attachment.get("storage_path"),
                "fileHash": attachment.get("file_hash"),
                "fileSizeBytes": attachment.get("file_size_bytes"),
                "textLength": len(effective_pdf_text),
                "extractor": (
                    "pdftotext-layout" if text_usable else "no-usable-text-layer"
                ),
            }
    after = {field: _to_json_compatible(data).get(field) for field in before}
    # 即使 PDF 无文字层，接口/HTML仍要按最新规则重算并更新版本元数据。
    return before != after or not text_usable, status


def reparse(output_root: Path) -> int:
    site_dir = output_root / "runshihua"
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
    parser = argparse.ArgumentParser(description="从已下载PDF重算润世和结果")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    args = parser.parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    lock_path = output_root / "runshihua" / "state" / "resumable.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("runshihua 正在运行，拒绝回填JSON/CSV", file=sys.stderr)
            return 5
        return reparse(output_root)


if __name__ == "__main__":
    raise SystemExit(main())
