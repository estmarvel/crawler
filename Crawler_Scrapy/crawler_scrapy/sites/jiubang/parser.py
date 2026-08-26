"""玖邦公告解析器。

玖邦与华新使用同版本 TWS 招投标前端，详情字段、公告分类、``annNature`` 和
附件 ``fileId`` 语义一致，因此复用已经过真实数据修正的华新规则，只覆盖平台
身份与用户可访问的详情页域名。
"""

from crawler_scrapy.sites.huaxin.parser import HuaxinParser
from crawler_scrapy.sites.jiubang.config import PLATFORM_NAME, WEB_BASE_URL


class JiubangParser(HuaxinParser):
    """把玖邦 TWS 详情 JSON 转换为框架统一的八类公告字段。"""

    parser_version = "jiubang-v10-correction-title-routing"
    platform_name = PLATFORM_NAME
    web_base_url = WEB_BASE_URL

    @classmethod
    def detect_subtype(cls, section, detail):
        """玖邦把延期等修订公告混放在招标公告栏目，需按标题纠正。"""

        title = str(detail.get("annTitle") or detail.get("annLastTitle") or "")
        correction_words = (
            "变更公告",
            "更正公告",
            "延期公告",
            "澄清公告",
            "终止公告",
            "撤销公告",
            "中止公告",
        )
        if section == "zbgg_zys" and any(word in title for word in correction_words):
            return "gzjg"
        return super().detect_subtype(section, detail)
