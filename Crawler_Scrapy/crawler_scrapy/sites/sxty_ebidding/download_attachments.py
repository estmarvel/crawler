"""独立、可恢复地下载易招标山西公开公告附件。"""

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class SxtyEbiddingAttachmentDownloader(AttachmentDownloader):
    site_code = "sxty_ebidding"
    site_label = "SXTY_EBIDDING"
    allowed_attachment_hosts = {
        "sxty.ebidding.net.cn",
        "sxpt.zcjb.com.cn",
        "sx.zcjb.com.cn",
    }
    allowed_attachment_schemes = {"http", "https"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(SxtyEbiddingAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())

