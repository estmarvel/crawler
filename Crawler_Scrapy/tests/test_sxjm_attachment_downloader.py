from __future__ import annotations

import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from crawler_scrapy.sites.sxjm.download_attachments import (
    AccessBlocked,
    AttachmentDownloader,
    DownloadConfig,
    attachment_storage_path,
)


def _record():
    attachment = {
        "source_file_id": "51787",
        "file_name": "中标候选人公示.pdf",
        "file_url": (
            "https://www.sxccdzzcpt.cn/zcpt/2026-06-12/"
            "d755b0a6127fad15889108e2ce07b9916.pdf"
        ),
        "storage_path": None,
        "file_hash": None,
        "file_size_bytes": None,
        "file_type": "pdf",
        "parse_status": "PENDING",
    }
    return {
        "平台代码": "sxjm",
        "公告ID": "43117",
        "公告类型": "CANDIDATE",
        "公告子类型": "yfxm.hxr",
        "详情页链接": "https://www.sxccdzzcpt.cn/home/detail?id=43117",
        "附件": [attachment],
        "_trace": {"exportMetadata": {"attachments": [dict(attachment)]}},
    }


class _NoNetworkSession:
    headers = {}

    def get(self, *args, **kwargs):
        raise AssertionError("已存在附件不应再次发起网络请求")


def test_existing_attachment_is_skipped_and_synced_to_json_csv(tmp_path):
    output_root = tmp_path / "new_output"
    json_dir = output_root / "sxjm" / "json"
    csv_dir = output_root / "sxjm" / "csv"
    json_dir.mkdir(parents=True)
    csv_dir.mkdir(parents=True)
    row = _record()
    json_path = json_dir / "依法项目_中标候选人公示.json"
    json_path.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")
    csv_path = csv_dir / "依法项目_中标候选人公示.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file_object:
        writer = csv.DictWriter(file_object, fieldnames=["公告ID", "附件"])
        writer.writeheader()
        writer.writerow({"公告ID": "43117", "附件": json.dumps(row["附件"], ensure_ascii=False)})

    relative_path = attachment_storage_path(row, row["附件"][0])
    attachment_path = output_root / relative_path
    attachment_path.parent.mkdir(parents=True)
    attachment_path.write_bytes(b"complete-pdf")

    downloader = AttachmentDownloader(
        DownloadConfig(output_root=output_root, min_delay=0, max_delay=0),
        session=_NoNetworkSession(),
    )
    assert downloader.run() == 0

    saved = json.loads(json_path.read_text(encoding="utf-8"))[0]
    current = saved["附件"][0]
    traced = saved["_trace"]["exportMetadata"]["attachments"][0]
    assert current["storage_path"] == relative_path
    assert current["file_size_bytes"] == len(b"complete-pdf")
    assert len(current["file_hash"]) == 32
    assert current["parse_status"] == "CACHED_NO_OCR"
    assert traced == current
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file_object:
        csv_attachment = json.loads(next(csv.DictReader(file_object))["附件"])[0]
    assert csv_attachment == current


def test_interrupted_part_file_is_resumed_with_range(tmp_path):
    content = b"0123456789-attachment-content"

    class Handler(BaseHTTPRequestHandler):
        range_header = ""

        def do_GET(self):
            type(self).range_header = self.headers.get("Range", "")
            start = int(type(self).range_header.removeprefix("bytes=").removesuffix("-"))
            body = content[start:]
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_root = tmp_path / "new_output"
        final_path = output_root / "sxjm" / "attachments" / "file.pdf"
        final_path.parent.mkdir(parents=True)
        part_path = final_path.with_name("file.pdf.part")
        part_path.write_bytes(content[:7])
        downloader = AttachmentDownloader(
            DownloadConfig(output_root=output_root, min_delay=0, max_delay=0)
        )
        downloader._validate_url = lambda url: None
        response, size, _ = downloader._download_once(
            f"http://127.0.0.1:{server.server_port}/file.pdf",
            final_path,
            "",
        )
        response.close()
    finally:
        server.shutdown()
        server.server_close()

    assert Handler.range_header == "bytes=7-"
    assert size == len(content)
    assert final_path.read_bytes() == content
    assert not part_path.exists()


def test_storage_path_matches_notice_files_pipeline_layout():
    row = _record()
    assert attachment_storage_path(row, row["附件"][0]) == (
        "sxjm/attachments/CANDIDATE/43117/51787_中标候选人公示.pdf"
    )


def test_403_stops_immediately_without_retry(tmp_path):
    class BlockedSession:
        headers = {}
        calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            response = requests.Response()
            response.status_code = 403
            response.url = url
            response._content = b""
            response._content_consumed = True
            return response

    session = BlockedSession()
    downloader = AttachmentDownloader(
        DownloadConfig(
            output_root=tmp_path,
            retries=4,
            retry_base_delay=0.01,
            retry_max_delay=0.01,
            min_delay=0,
            max_delay=0,
        ),
        session=session,
    )
    row = _record()
    with pytest.raises(AccessBlocked):
        downloader._download(
            row,
            row["附件"][0],
            tmp_path / attachment_storage_path(row, row["附件"][0]),
        )
    assert session.calls == 1
