"""玖邦公告解析器。

玖邦与华新使用同版本 TWS 招投标前端，详情字段、公告分类、``annNature`` 和
附件 ``fileId`` 语义一致，因此复用已经过真实数据修正的华新规则，只覆盖平台
身份与用户可访问的详情页域名。
"""

from crawler_scrapy.sites.huaxin.parser import HuaxinParser
from crawler_scrapy.sites.jiubang.config import PLATFORM_NAME, WEB_BASE_URL


class JiubangParser(HuaxinParser):
    """把玖邦 TWS 详情 JSON 转换为框架统一的八类公告字段。"""

    platform_name = PLATFORM_NAME
    web_base_url = WEB_BASE_URL

