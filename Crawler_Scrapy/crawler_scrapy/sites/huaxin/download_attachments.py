"""独立下载华新 JSON 中已解析出真实 URL 的附件。"""

from crawler_scrapy.sites.huaxin import config
from crawler_scrapy.sites.sxjm.download_attachments import run_downloader
from crawler_scrapy.sites.tws_attachment_downloader import TwsAttachmentDownloader


class HuaxinAttachmentDownloader(TwsAttachmentDownloader):
    site_code = "huaxin"
    site_label = "HUAXIN"
    site_config = config
    allowed_attachment_hosts = {"www.ygcgpt.com", "v3.cdn.ygcgpt.com"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(HuaxinAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
