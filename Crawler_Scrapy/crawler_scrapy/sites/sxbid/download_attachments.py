"""独立、可恢复地下载山西招投标网公开附件和公告正文 PDF。"""

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class SxbidAttachmentDownloader(AttachmentDownloader):
    site_code = "sxbid"
    site_label = "SXBID"
    allowed_attachment_hosts = {"www.sxbid.com.cn", "sxbid.com.cn"}
    allowed_attachment_schemes = {"https", "http"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(SxbidAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
