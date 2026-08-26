"""独立、可恢复地下载山西新点公告公开附件。"""

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class SxxindianAttachmentDownloader(AttachmentDownloader):
    site_code = "sxxindian"
    site_label = "SXXINDIAN"
    allowed_attachment_hosts = {"www.sxxindian.com", "sxxindian.com"}
    allowed_attachment_schemes = {"http", "https"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(SxxindianAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
