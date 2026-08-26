"""独立、可恢复地下载 SXZWFW 公告 JSON 中已解析出的附件。"""

from __future__ import annotations

from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    run_downloader,
)


class SxzwfwAttachmentDownloader(AttachmentDownloader):
    site_code = "sxzwfw"
    site_label = "SXZWFW"
    allowed_attachment_hosts = {
        "prec.sxzwfw.gov.cn",
        # 山西公共资源省级页面会嵌入运城交易中心的公告正文 PDF。
        "ggzyjyzx.yuncheng.gov.cn",
        # 政府采购公告引用的山西政采云官方对象存储域名。
        "sx2gov2open2doc.uos.sxzfcg.zcygov.cn",
    }


def main(argv: list[str] | None = None) -> int:
    return run_downloader(SxzwfwAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
