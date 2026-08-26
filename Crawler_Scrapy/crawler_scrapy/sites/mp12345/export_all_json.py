"""Crawl public mp12345 bidding notices into JSON/JSONL files.

The public list API exposes notice metadata. Public detail pages are Vue routes
that call a detail JSON API and then render a PDF by file id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


PLATFORM_NAME = "煤婆网"
PLATFORM_CODE = "mp12345"

LIST_API = "https://www.mp12345.com/get/local/bidding/info.json"
LIST_PAGE = "https://www.mp12345.com/infomercial/newInviteTendersNotice.jsp"
DETAIL_BASE = "https://bidtest.mp12345.com/bids/exchange"
DETAIL_API = f"{DETAIL_BASE}/noticedetail/getBidNotice.htm"
PDF_API = f"{DETAIL_BASE}/noticedetail/viewBidNoticePdf.htm"
DETAIL_PAGE_BASE = "https://bidtest.mp12345.com/bidweb/#/notice/view"

NOTICE_ROUTE_TYPE = {
    "1": "2",
    "2": "2",
    "3": "1",
    "4": "3",
    "5": "3",
    "6": "4",
    "7": "5",
}

NOTICE_TYPE_LABEL = {
    "1": "招标公告",
    "2": "招标公告",
    "3": "资格预审公告",
    "4": "澄清/终止公告",
    "5": "澄清/终止公告",
    "6": "中标候选人公示",
    "7": "中标结果公告",
}

NOTICE_TYPE_CODE = {
    "1": "TENDER",
    "2": "TENDER",
    "3": "PREQUALIFICATION",
    "4": "CORRECTION",
    "5": "CORRECTION",
    "6": "CANDIDATE",
    "7": "AWARD",
}

NOTICE_SUBTYPE_SUFFIX = {
    "1": "zbgg",
    "2": "zbgg",
    "3": "zbys",
    "4": "gzjg",
    "5": "gzjg",
    "6": "hxr",
    "7": "zbjg",
}

BIZ_TYPE_LABEL = {
    "1": "货物",
    "2": "工程",
    "3": "服务",
}


def now_iso() -> str:
    return datetime.now().isoformat()


def crawler_time() -> str:
    now = datetime.now()
    return f"{now:%Y-%m-%d %H:%M:%S}.{now.microsecond // 1000:03d}"


def request_bytes(url: str, *, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/html,application/pdf,*/*",
            "Referer": LIST_PAGE,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def request_json(url: str, *, timeout: int = 30) -> dict[str, Any]:
    raw = request_bytes(url, timeout=timeout)
    return json.loads(raw.decode("utf-8", errors="strict"))


def list_url(page: int, page_size: int) -> str:
    params = {
        "pageNumber": page,
        "pageSize": page_size,
        "title": "",
        "bidMethodType": "",
        "mustBiddingCode": "",
        "bizTypeCode": "",
        "method": "",
    }
    return f"{LIST_API}?{urllib.parse.urlencode(params)}"


def detail_url(notice_id: str, route_type: str) -> str:
    return f"{DETAIL_API}?{urllib.parse.urlencode({'noticeId': notice_id, 'noticeType': route_type})}"


def pdf_url(file_id: str) -> str:
    return f"{PDF_API}?{urllib.parse.urlencode({'fileId': file_id})}"


def stable_payload_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def safe_name(value: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "", value).strip()
    return text or "未分类"


def collapse_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def compact_pdf_text(value: str) -> str:
    lines = [collapse_text(line) for line in (value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def find_label(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = r"\s*".join(map(re.escape, label))
        match = re.search(rf"{pattern}\s*[:：]\s*([^\n\r]+)", text)
        if match:
            return collapse_text(match.group(1))
    return ""


def clean_identifier(value: str) -> str:
    text = collapse_text(value).strip(" ：:")
    if not text:
        return ""
    text = re.split(r"(?:现|，|,|。|；|;|、|，|（采购|采购人|招标人|\s{2,})", text, maxsplit=1)[0]
    text = text.strip(" ：:，,。；;、")
    if not re.search(r"\d", text):
        return ""
    if len(text) > 80:
        text = text[:80]
    return text


def find_amount(text: str) -> str:
    match = re.search(
        r"(?:成交金额|中标价|中标金额|报价|投标报价|采购预算|预算金额)\s*[:：]\s*([^\n\r]+)",
        text,
    )
    return collapse_text(match.group(1)) if match else ""


def extract_structured_fields(record: dict[str, Any], text: str) -> dict[str, Any]:
    normalized = compact_pdf_text(text)
    project_code = clean_identifier(find_label(
        normalized,
        ("项目编号", "项目编码", "招标编号", "采购编号"),
    ))
    owner = find_label(normalized, ("采购人", "招标人", "采购单位", "招 标 人"))
    agency = find_label(normalized, ("招标代理机构", "采购代理机构", "代理机构"))
    winner = find_label(normalized, ("成交供应商", "中标人", "供应商名称"))
    fields = {
        "项目名称": record.get("公告标题") or "",
        "项目编号": project_code,
        "项目编号/招标编号": project_code,
        "招标编号/项目编号": project_code,
        "招标人/采购人名称": owner,
        "招标人/采购人": owner,
        "采购人/招标人地址": find_label(normalized, ("地址", "地 址")),
        "采购人/招标人联系人": find_label(normalized, ("联系人", "联 系 人")),
        "采购人/招标人联系电话": find_label(normalized, ("联系电话", "电话", "电 话")),
        "招标代理机构": agency,
        "中标人名称": winner,
        "成交供应商/中标人": winner,
        "中标价": find_amount(normalized),
        "成交金额/中标价": find_amount(normalized),
    }
    return {key: value for key, value in fields.items() if value}


def normalize_list_record(row: dict[str, Any]) -> dict[str, Any]:
    notice_id = str(row.get("notice_id") or "").strip()
    notice_type = str(row.get("notice_type") or "").strip()
    route_type = NOTICE_ROUTE_TYPE.get(notice_type, notice_type)
    biz_type = str(row.get("biz_type_code") or "").strip()
    return {
        "平台名称": PLATFORM_NAME,
        "平台代码": PLATFORM_CODE,
        "公告ID": notice_id,
        "公告类型": NOTICE_TYPE_CODE.get(notice_type, "TENDER"),
        "公告子类型": f"mp12345.notice_type_{notice_type}.{NOTICE_SUBTYPE_SUFFIX.get(notice_type, 'qt')}",
        "源站公告类型": NOTICE_TYPE_LABEL.get(notice_type, ""),
        "源站ID": str(row.get("id") or ""),
        "源站公告类型代码": notice_type,
        "详情路由类型": route_type,
        "业务类型代码": biz_type,
        "业务类型": BIZ_TYPE_LABEL.get(biz_type, ""),
        "招采方式代码": str(row.get("bid_method_code") or "").strip(),
        "是否依法必招代码": str(row.get("must_bidding_code") or "").strip(),
        "公告标题": str(row.get("title") or "").strip(),
        "发布时间": str(row.get("public_time") or "").strip(),
        "是否展示代码": str(row.get("is_show") or "").strip(),
        "公告页面URL": f"{DETAIL_PAGE_BASE}/{notice_id}/{route_type}",
        "源站列表记录": row,
    }


def extract_pdf_text(pdf_bytes: bytes) -> tuple[str, str]:
    if not pdf_bytes.startswith(b"%PDF"):
        return "", "not_pdf"
    if not shutil.which("pdftotext"):
        return "", "pdftotext_missing"
    with tempfile.TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / "notice.pdf"
        pdf_path.write_bytes(pdf_bytes)
        try:
            output = subprocess.check_output(
                ["pdftotext", str(pdf_path), "-"],
                stderr=subprocess.STDOUT,
                timeout=45,
            )
        except subprocess.TimeoutExpired:
            return "", "pdftotext_timeout"
        except subprocess.CalledProcessError as exc:
            message = exc.output.decode("utf-8", errors="replace")[:200]
            return "", f"pdftotext_error:{message}"
    return output.decode("utf-8", errors="replace").strip(), "ok"


def fetch_detail(record: dict[str, Any], include_pdf_text: bool, text_dir: Path) -> dict[str, Any]:
    notice_id = record["公告ID"]
    route_type = record["详情路由类型"]
    result: dict[str, Any] = {
        "详情接口": detail_url(notice_id, route_type),
        "详情抓取时间": crawler_time(),
        "详情状态": "pending",
    }
    try:
        payload = request_json(result["详情接口"])
    except Exception as exc:
        result.update({"详情状态": "detail_error", "详情错误": repr(exc)})
        return result
    result["详情原始响应"] = payload
    if payload.get("code") != 0:
        result.update({"详情状态": "detail_code_error"})
        return result
    notice = (payload.get("data") or {}).get("notice") or {}
    if not isinstance(notice, dict):
        result.update({"详情状态": "detail_missing_notice"})
        return result
    file_id = str(notice.get("noticeFileId") or "").strip()
    result.update(
        {
            "详情状态": "ok",
            "公告文件ID": file_id,
            "公告文件URL": pdf_url(file_id) if file_id else "",
            "详情公告记录": notice,
        }
    )
    if not file_id or not include_pdf_text:
        return result
    try:
        pdf_bytes = request_bytes(result["公告文件URL"], timeout=60)
    except Exception as exc:
        result.update({"PDF文本状态": "pdf_fetch_error", "PDF错误": repr(exc)})
        return result
    text, status = extract_pdf_text(pdf_bytes)
    text_file = ""
    if text:
        text_dir.mkdir(parents=True, exist_ok=True)
        text_file_path = text_dir / f"{stable_payload_name(notice_id)}.txt"
        text_file_path.write_text(text, encoding="utf-8")
        text_file = str(text_file_path)
    structured = extract_structured_fields(record, text)
    result.update(
        {
            "PDF文本状态": status,
            "PDF字节数": len(pdf_bytes),
            "公告正文文本文件": text_file,
            "公告内容": text,
            **structured,
        }
    )
    return result


def load_done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            notice_id = item.get("公告ID")
            if notice_id:
                done.add(str(notice_id))
    return done


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        file.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                records.append(json.loads(line))
    return records


def grouped_json_paths(json_dir: Path, records: list[dict[str, Any]]) -> dict[str, str]:
    for old in json_dir.glob("*.json"):
        old.unlink()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        notice_type = str(record.get("源站公告类型") or record.get("公告类型") or "未分类")
        grouped.setdefault(notice_type, []).append(record)
    paths = {}
    for notice_type, items in sorted(grouped.items()):
        path = json_dir / f"{PLATFORM_NAME}_{safe_name(notice_type)}.json"
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[notice_type] = str(path)
    return paths


def finalize_record(record: dict[str, Any]) -> dict[str, Any]:
    record.pop("详情状态", None)
    record.pop("详情原始响应", None)
    list_payload = record.pop("源站列表记录", None)
    detail_payload = record.pop("详情公告记录", None)
    raw_text = record.get("公告内容") or ""
    fingerprint = hashlib.sha256(json.dumps({
        "id": record.get("公告ID"),
        "title": record.get("公告标题"),
        "publish": record.get("发布时间"),
        "detail": detail_payload,
        "text": raw_text,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    record.setdefault("项目名称", record.get("公告标题") or "")
    record["缺失字段"] = [
        field for field in ("项目名称", "项目编号", "招标人/采购人名称", "中标人名称", "中标价")
        if not record.get(field)
    ]
    record["解析状态"] = "PARSED"
    record["内容指纹"] = fingerprint
    record["抽取方式"] = "mp12345-public-json-pdf-rule-parser"
    record["抽取版本"] = "mp12345-v1-db-compatible"
    record["是否已合并"] = False
    record["爬取时间"] = crawler_time()
    record["_trace"] = {
        "schemaVersion": "1.0",
        "noticeSchemaVersion": "mp12345-v1-db-compatible",
        "payload": {
            "list": list_payload,
            "detail": detail_payload,
            "pdfUrl": record.get("公告文件URL"),
            "pdfTextFile": record.get("公告正文文本文件"),
        },
        "rawText": raw_text,
        "crawlerVersion": "mp12345-export-all-json-v1",
        "extractionVersion": "mp12345-v1-db-compatible",
        "responseMetadata": {
            "listApi": LIST_API,
            "detailApi": record.get("详情接口"),
            "pdfApi": record.get("公告文件URL"),
        },
        "fieldMeta": {
            "evidence": ["list_json", "detail_json", "pdf_text"],
        },
    }
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="new_output")
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--detail-delay", type=float, default=0.5)
    parser.add_argument("--include-details", action="store_true")
    parser.add_argument("--include-pdf-text", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.output_root) / PLATFORM_CODE
    json_dir = root / "json"
    payload_dir = root / "payloads"
    log_dir = root / "logs"
    state_dir = root / "state"
    text_dir = root / "texts"
    for directory in (json_dir, payload_dir, log_dir, state_dir, text_dir):
        directory.mkdir(parents=True, exist_ok=True)

    records_jsonl = json_dir / "mp12345_records.jsonl"
    summary_json = state_dir / "summary.json"
    if args.refresh:
        for path in (records_jsonl, summary_json):
            if path.exists():
                path.unlink()
        for path in json_dir.glob("*.json"):
            path.unlink()

    done = load_done_ids(records_jsonl)
    first_payload = request_json(list_url(1, args.page_size))
    data = first_payload.get("data") or {}
    total_count = int(data.get("totalCount") or 0)
    page_count = int(data.get("pageCount") or math.ceil(total_count / args.page_size) or 1)
    if args.max_pages:
        page_count = min(page_count, args.max_pages)

    summary: dict[str, Any] = {
        "platform_name": PLATFORM_NAME,
        "platform_code": PLATFORM_CODE,
        "started_at": now_iso(),
        "list_api": LIST_API,
        "detail_api": DETAIL_API,
        "pdf_api": PDF_API,
        "reported_total_count": total_count,
        "reported_page_count": int(data.get("pageCount") or 0),
        "configured_page_count": page_count,
        "page_size": args.page_size,
        "include_details": args.include_details,
        "include_pdf_text": args.include_pdf_text,
        "records_written": 0,
        "records_skipped_existing": len(done),
        "errors": [],
    }

    written = 0
    stop = False
    for page in range(1, page_count + 1):
        if page == 1:
            payload = first_payload
        else:
            try:
                payload = request_json(list_url(page, args.page_size))
            except Exception as exc:
                summary["errors"].append({"page": page, "error": repr(exc)})
                continue
        payload_path = payload_dir / f"list_page_{page:05d}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        rows = ((payload.get("data") or {}).get("data") or [])
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            record = normalize_list_record(row)
            if not record["公告ID"] or record["公告ID"] in done:
                continue
            if args.include_details:
                record.update(fetch_detail(record, args.include_pdf_text, text_dir))
                time.sleep(args.detail_delay)
            record = finalize_record(record)
            append_jsonl(records_jsonl, record)
            done.add(record["公告ID"])
            written += 1
            if args.max_records and written >= args.max_records:
                stop = True
                break
        print(f"page={page}/{page_count} rows={len(rows)} written={written}", flush=True)
        summary_json.write_text(json.dumps(summary | {"records_written": written}, ensure_ascii=False, indent=2), encoding="utf-8")
        if stop:
            break
        time.sleep(args.delay)

    records = read_jsonl(records_jsonl) if records_jsonl.exists() else []
    by_type_paths = grouped_json_paths(json_dir, records)
    summary.update(
        {
            "finished_at": now_iso(),
            "records_written": written,
            "records_total_in_jsonl": len(records),
            "jsonl_path": str(records_jsonl),
            "by_type_paths": by_type_paths,
        }
    )
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
