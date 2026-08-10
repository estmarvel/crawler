from pathlib import Path

import pytest

from crawler_scrapy.sites.sxjkzcpt.download_attachments import (
    SxjkzcptAttachmentDownloader,
)
from crawler_scrapy.sites.sxjm.download_attachments import DownloadConfig


def test_sxjkzcpt_attachment_file_id_and_host_validation(tmp_path: Path):
    downloader = SxjkzcptAttachmentDownloader(DownloadConfig(output_root=tmp_path))
    url = "https://www.sxjkzcpt.com.cn/fileInfo/downloadFile/file_123"
    assert downloader._file_id(url) == "file_123"
    downloader._validate_url(url)
    with pytest.raises(ValueError):
        downloader._validate_url("https://example.com/fileInfo/downloadFile/file_123")
