from __future__ import annotations

import json

import pytest
import requests

from crawler_scrapy.sites.bitbid import config
from crawler_scrapy.sites.bitbid.download_attachments import (
    BitbidAttachmentDownloader,
)
from crawler_scrapy.sites.sxjm.download_attachments import DownloadConfig


def test_bitbid_downloader_accepts_only_current_verified_source_hosts(tmp_path):
    downloader = BitbidAttachmentDownloader(
        DownloadConfig(output_root=tmp_path, min_delay=0, max_delay=0)
    )

    downloader._validate_url("http://www.bitbid.cn/auth/file.pdf")
    downloader._validate_url("http://xzb.bitbid.cn/plan.pdf")
    with pytest.raises(ValueError):
        downloader._validate_url("http://zb.bitbid.cn/file.pdf")
    with pytest.raises(ValueError):
        downloader._validate_url("http://www.bitbid.cn.example.com/file.pdf")


def test_bitbid_config_uses_current_frontend_attachment_hosts():
    assert config.pdf_url("tender", 123).startswith("http://www.bitbid.cn/auth/")
    assert config.PLAN_FILE_BASE_URL == "http://xzb.bitbid.cn"


def test_legacy_url_is_rewritten_and_verify_cookie_is_primed(tmp_path):
    class FakeSession:
        def __init__(self):
            self.headers = {}
            self.trust_env = True
            self.proxies = {}
            self.urls = []

        def get(self, url, **kwargs):
            self.urls.append((url, kwargs))
            response = requests.Response()
            response.url = url
            if len(self.urls) == 1:
                response.status_code = 302
                response.headers["Set-Cookie"] = "verify=test; Max-Age=3600"
                response._content = b""
            else:
                response.status_code = 200
                response.headers["Content-Length"] = "9"
                response._content = b"%PDF-test"
            response._content_consumed = True
            return response

    session = FakeSession()
    downloader = BitbidAttachmentDownloader(
        DownloadConfig(
            output_root=tmp_path,
            retries=0,
            min_delay=0,
            max_delay=0,
        ),
        session=session,
    )
    attachment = {
        "file_url": (
            "http://zb.bitbid.cn/auth/ggWeb/detailGG/"
            "ggBack!readGGSignFile.action?zbGongGao.id=123"
        ),
        "file_name": "公告.pdf",
    }
    final_path = tmp_path / "公告.pdf"
    metadata = downloader._download(
        {"详情页链接": "http://www.bitbid.cn/detail?id=123"},
        attachment,
        final_path,
    )

    assert session.urls[0][0] == "http://www.bitbid.cn/"
    assert session.urls[0][1]["allow_redirects"] is False
    assert session.urls[1][0].startswith("http://www.bitbid.cn/auth/")
    assert attachment["file_url"] == session.urls[1][0]
    assert attachment["source_file_id"] == "tender_123"
    assert metadata["file_url"] == session.urls[1][0]
    assert metadata["source_file_id"] == "tender_123"
    assert final_path.read_bytes() == b"%PDF-test"


def test_loading_legacy_json_normalizes_main_and_trace_with_stable_id(tmp_path):
    legacy_url = (
        "http://zb.bitbid.cn/auth/ggWeb/gongShiDetail/"
        "DingBiao!readGSSignFile.action?dbZhongBiaoGongShi.id=73672"
    )
    path = tmp_path / "candidate.json"
    path.write_text(
        json.dumps(
            [{
                "附件": [{"file_url": legacy_url, "source_file_id": None}],
                "_trace": {
                    "exportMetadata": {
                        "attachments": [
                            {"file_url": legacy_url, "source_file_id": None}
                        ]
                    }
                },
            }],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    row = BitbidAttachmentDownloader._load_rows(path)[0]
    attachment = row["附件"][0]
    traced = row["_trace"]["exportMetadata"]["attachments"][0]
    assert attachment["file_url"].startswith("http://www.bitbid.cn/auth/")
    assert attachment["source_file_id"] == "candidate_73672"
    assert traced == attachment
