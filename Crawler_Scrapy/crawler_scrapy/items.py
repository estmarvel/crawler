"""Scrapy Item definitions."""

import scrapy


class NoticeItem(scrapy.Item):
    """八类公告共用的统一 Item。

    公共元数据放在顶层；不同公告类型的业务字段统一放入 data。
    HTML 原文通过 raw_html 传入，由 HtmlSnapshotPipeline 单独保存。
    """

    platform = scrapy.Field()
    platform_code = scrapy.Field()
    notice_id = scrapy.Field()
    notice_type = scrapy.Field()
    notice_subtype = scrapy.Field()
    title = scrapy.Field()
    publish_time = scrapy.Field()
    detail_url = scrapy.Field()
    crawl_time = scrapy.Field()

    data = scrapy.Field()
    missing_fields = scrapy.Field()
    field_meta = scrapy.Field()
    raw_data = scrapy.Field()

    # raw_html 推荐传 response.body，以尽量保留服务器返回的原始字节。
    # 若页面正文由 JSON 接口中的 HTML 字段提供，也可以传该 HTML 字符串。
    raw_html = scrapy.Field()
    raw_text = scrapy.Field()
    parse_status = scrapy.Field()
    fingerprint = scrapy.Field()

    extraction_model = scrapy.Field()
    extraction_version = scrapy.Field()
    is_verified = scrapy.Field()

    snapshot_path = scrapy.Field()
    snapshot_sha256 = scrapy.Field()

    attachments = scrapy.Field()
    file_urls = scrapy.Field()
    files = scrapy.Field()
