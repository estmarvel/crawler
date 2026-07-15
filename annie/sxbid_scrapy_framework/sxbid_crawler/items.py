import scrapy
from sxbid_crawler.columns import COLUMNS


class SxbidProjectRowItem(scrapy.Item):
    for _field in COLUMNS:
        locals()[_field] = scrapy.Field()
