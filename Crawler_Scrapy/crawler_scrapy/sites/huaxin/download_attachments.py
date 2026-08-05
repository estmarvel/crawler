"""独立下载华新 JSON 中已解析出真实 URL 的附件。"""

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class HuaxinAttachmentDownloader(AttachmentDownloader):
    site_code = "huaxin"
    site_label = "HUAXIN"
    allowed_attachment_hosts = {"www.ygcgpt.com"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(HuaxinAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
