"""旺采网公开栏目配置。"""

PLATFORM_NAME = "旺采网"
PLATFORM_CODE = "wc5ibid"
BASE_URL = "https://www.5ibid.net"

CATEGORIES = {
    "zbgg": {"label": "招标/预审公告", "list_path": "zbggList", "detail_path": "zbggDetail"},
    "kzj": {"label": "控制价公告", "list_path": "kzjList", "detail_path": "kzjDetail"},
    "zbhxgs": {"label": "中标候选公示", "list_path": "zbhxgsList", "detail_path": "zbhxgsDetail"},
    "zbjg": {"label": "中标结果", "list_path": "zbjgList", "detail_path": "zbjgDetail"},
    "bggg": {"label": "变更公告", "list_path": "bgggList", "detail_path": "bgggDetail"},
    "fbgg": {"label": "废标公告", "list_path": "fbggList", "detail_path": "fbggDetail"},
}
DEFAULT_CATEGORIES = tuple(CATEGORIES)


def list_url(category: str, page: int) -> str:
    return f"{BASE_URL}/Liems/{CATEGORIES[category]['list_path']}/{page}.html"

