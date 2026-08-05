from __future__ import annotations

import pytest

from crawler_scrapy.sites.sxjm.download_attachments import DownloadConfig
from crawler_scrapy.sites.sxzwfw.download_attachments import (
    SxzwfwAttachmentDownloader,
)


def test_sxzwfw_downloader_reads_only_site_json_and_accepts_only_source_https(tmp_path):
    downloader = SxzwfwAttachmentDownloader(
        DownloadConfig(output_root=tmp_path, min_delay=0, max_delay=0)
    )

    assert downloader.json_dir == tmp_path / "sxzwfw" / "json"
    downloader._validate_url(
        "https://prec.sxzwfw.gov.cn/attachment.jspx?cid=123&i=0"
    )
    with pytest.raises(ValueError):
        downloader._validate_url(
            "https://www.sxccdzzcpt.cn/files/not-sxzwfw.pdf"
        )
    with pytest.raises(ValueError):
        downloader._validate_url(
            "http://prec.sxzwfw.gov.cn/attachment.jspx?cid=123&i=0"
        )
