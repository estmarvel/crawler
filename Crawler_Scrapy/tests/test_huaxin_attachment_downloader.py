from __future__ import annotations

import pytest

from crawler_scrapy.sites.huaxin.download_attachments import (
    HuaxinAttachmentDownloader,
)
from crawler_scrapy.sites.sxjm.download_attachments import DownloadConfig


def test_huaxin_downloader_accepts_only_verified_https_attachment_hosts(tmp_path):
    downloader = HuaxinAttachmentDownloader(
        DownloadConfig(output_root=tmp_path, min_delay=0, max_delay=0)
    )

    downloader._validate_url("https://v3.cdn.ygcgpt.com/file.pdf")
    with pytest.raises(ValueError):
        downloader._validate_url("https://v3.cdn.ygcgpt.com.example.com/file.pdf")
    with pytest.raises(ValueError):
        downloader._validate_url("http://v3.cdn.ygcgpt.com/file.pdf")
