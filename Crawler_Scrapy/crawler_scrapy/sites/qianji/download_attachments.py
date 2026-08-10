"""独立、可恢复地下载千极数采公告附件。"""

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class QianjiAttachmentDownloader(AttachmentDownloader):
    site_code = "qianji"
    site_label = "QIANJI"
    allowed_attachment_hosts = {"www.qianjilink.com"}
    allowed_attachment_schemes = {"https"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(QianjiAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
