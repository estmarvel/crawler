"""独立、可恢复地下载中招联合（山西）公告中的公开附件。"""

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class Trade365AttachmentDownloader(AttachmentDownloader):
    site_code = "trade365"
    site_label = "TRADE365"
    allowed_attachment_hosts = {
        "shanxi.365trade.com.cn",
        "www.365trade.com.cn",
        "365trade.com.cn",
    }
    allowed_attachment_schemes = {"http", "https"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(Trade365AttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
