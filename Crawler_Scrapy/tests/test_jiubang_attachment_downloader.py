from __future__ import annotations

import pytest
import requests

from crawler_scrapy.sites.jiubang.download_attachments import (
    JiubangAttachmentDownloader,
)
from crawler_scrapy.sites.sxjm.download_attachments import DownloadConfig


class _MetadataSession:
    headers = {}
    trust_env = False

    def __init__(self):
        self.requested_url = ""

    def get(self, url, **kwargs):
        self.requested_url = url
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = (
            b'{"code":200,"data":{"url":"//cdn.v3.bjjbkj.cn/new.pdf",'
            b'"fileName":"attachment.pdf"}}'
        )
        response._content_consumed = True
        return response


def test_jiubang_downloader_accepts_only_verified_https_attachment_hosts(tmp_path):
    downloader = JiubangAttachmentDownloader(
        DownloadConfig(output_root=tmp_path, min_delay=0, max_delay=0)
    )

    downloader._validate_url("https://cdn.v3.bjjbkj.cn/file.pdf")
    downloader._validate_url("https://public.cdn.bjjbkj.cn/file.pdf")
    with pytest.raises(ValueError):
        downloader._validate_url("https://cdn.v3.bjjbkj.cn.example.com/file.pdf")
    with pytest.raises(ValueError):
        downloader._validate_url("http://cdn.v3.bjjbkj.cn/file.pdf")


def test_jiubang_downloader_refreshes_expired_signed_url(tmp_path):
    session = _MetadataSession()
    downloader = JiubangAttachmentDownloader(
        DownloadConfig(output_root=tmp_path, min_delay=0, max_delay=0),
        session=session,
    )
    attachment = {
        "source_file_id": "file-123",
        "file_name": None,
        "file_url": "https://cdn.v3.bjjbkj.cn/expired.pdf?Expires=1",
    }

    assert downloader._refresh_attachment_url(attachment) == (
        "https://cdn.v3.bjjbkj.cn/new.pdf"
    )
    assert session.requested_url.endswith("/bidding/file/query/file-123")
    assert attachment["file_url"] == "https://cdn.v3.bjjbkj.cn/new.pdf"
    assert attachment["file_name"] == "attachment.pdf"
