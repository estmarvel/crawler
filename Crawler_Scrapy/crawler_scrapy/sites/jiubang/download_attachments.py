"""独立下载玖邦 JSON 中已解析出真实 URL 的附件。"""

from crawler_scrapy.sites.jiubang import config
from crawler_scrapy.sites.sxjm.download_attachments import run_downloader
from crawler_scrapy.sites.tws_attachment_downloader import TwsAttachmentDownloader


class JiubangAttachmentDownloader(TwsAttachmentDownloader):
    site_code = "jiubang"
    site_label = "JIUBANG"
    site_config = config
    # 玖邦的 file/query 接口返回带时效签名的 CDN URL。严格列出前端及
    # 生产数据实际使用的主机，避免放宽为可被相似域名绕过的后缀匹配。
    allowed_attachment_hosts = {
        "www.bjjbkj.cn",
        "cdn.v3.bjjbkj.cn",
        "public.cdn.bjjbkj.cn",
    }


def main(argv: list[str] | None = None) -> int:
    return run_downloader(JiubangAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
