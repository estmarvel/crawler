"""独立下载玖邦 JSON 中已解析出真实 URL 的附件。"""

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class JiubangAttachmentDownloader(AttachmentDownloader):
    site_code = "jiubang"
    site_label = "JIUBANG"
    allowed_attachment_hosts = {"www.bjjbkj.cn"}


def main(argv: list[str] | None = None) -> int:
    return run_downloader(JiubangAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
