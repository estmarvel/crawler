"""独立下载比比网 JSON 中的公告附件。"""

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class BitbidAttachmentDownloader(AttachmentDownloader):
    site_code = "bitbid"
    site_label = "BITBID"
    allowed_attachment_hosts = {"www.bitbid.cn", "zb.bitbid.cn"}
    # 比比网当前签章 PDF 源站仍只提供 HTTP；严格限定到上述两个域名。
    allowed_attachment_schemes = {"http", "https"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(BitbidAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
