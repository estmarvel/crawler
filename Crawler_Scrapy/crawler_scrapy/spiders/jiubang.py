"""玖邦招标采购电子交易平台 Scrapy Spider。

示例：
    scrapy crawl jiubang -a max_records=20
    scrapy crawl jiubang -a sections=zbgg_zys,hxr,gs,zbjh

本 Spider 仅采集招投标模块；独立采购、竞价和零散采购模块不在当前范围内。
"""

from crawler_scrapy.sites.jiubang import config
from crawler_scrapy.sites.jiubang.parser import JiubangParser
from crawler_scrapy.spiders.huaxin import HuaxinSpider


class JiubangSpider(HuaxinSpider):
    """复用 TWS 通用请求流程和字段规则采集玖邦招投标公告。"""

    name = "jiubang"
    site_config = config
    parser_class = JiubangParser
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.bjjbkj.cn"]
    parser_version = parser_class.parser_version
    extraction_model_name = "jiubang-rule-parser"
