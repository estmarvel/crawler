import scrapy
from shanxi_crawler.columns import COLUMNS


class ShanxiProjectRowItem(scrapy.Item):
    for _field in COLUMNS:
        locals()[_field] = scrapy.Field()
