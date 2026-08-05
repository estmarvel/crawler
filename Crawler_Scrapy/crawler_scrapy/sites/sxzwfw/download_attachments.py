"""独立、可恢复地下载 SXZWFW 公告 JSON 中已解析出的附件。"""

from __future__ import annotations

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class SxzwfwAttachmentDownloader(AttachmentDownloader):
    site_code = "sxzwfw"
    site_label = "SXZWFW"
    allowed_attachment_hosts = {"prec.sxzwfw.gov.cn"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(SxzwfwAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
