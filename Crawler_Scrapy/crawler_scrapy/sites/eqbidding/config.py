"""云买卖公开招标信息接口配置（来自官网前端 index/detail 脚本）。"""

from urllib.parse import urlencode

PLATFORM_NAME = "云买卖电子综合交易平台"
PLATFORM_CODE = "eqbidding"
BASE_URL = "https://www.eqbidding.com"
API_BASE = f"{BASE_URL}/web-back"

CATEGORIES = {
    "tender": ("招标公告", "招标公告", "zbgg", "1"),
    "candidate": ("候选人公示", "中标候选人公示", "hxr", "2"),
    "award": ("中标公示", "中标结果公示", "zbjg", "2"),
}
DEFAULT_FEEDS = tuple(CATEGORIES)


def list_url() -> str:
    return f"{API_BASE}/nx/n/list/notice"


def detail_api_url(kid: str) -> str:
    return f"{API_BASE}/nx/n/w/{kid}"


def detail_page_url(kid: str, category: str) -> str:
    return f"{BASE_URL}/page_detailed/list.html?{urlencode({'kid': kid, 'type': CATEGORIES[category][3]})}"
