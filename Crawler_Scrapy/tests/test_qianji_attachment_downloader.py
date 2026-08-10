from __future__ import annotations

import pytest

from crawler_scrapy.sites.qianji.download_attachments import (
    QianjiAttachmentDownloader,
)
from crawler_scrapy.sites.sxjm.download_attachments import DownloadConfig


def test_qianji_downloader_accepts_only_verified_https_source_host(tmp_path):
    downloader = QianjiAttachmentDownloader(
        DownloadConfig(output_root=tmp_path, min_delay=0, max_delay=0)
    )

    downloader._validate_url(
        "https://www.qianjilink.com/min/test/zb/notice/example.pdf"
    )
    with pytest.raises(ValueError):
        downloader._validate_url(
            "https://www.qianjilink.com.example.com/min/test/zb/notice/example.pdf"
        )
    with pytest.raises(ValueError):
        downloader._validate_url(
            "http://www.qianjilink.com/min/test/zb/notice/example.pdf"
        )
