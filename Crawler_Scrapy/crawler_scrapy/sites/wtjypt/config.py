"""伟拓招标采购交易平台公开接口配置。"""

from __future__ import annotations

from typing import Any

PLATFORM_NAME = "伟拓招标采购交易平台"
PLATFORM_CODE = "wtjypt"
BASE_URL = "http://www.wtjypt.com"

PROJECT_TYPES = {
    "engineering": ("0", "工程", "A"),
    "goods": ("1", "货物", "B"),
    "service": ("2", "服务", "C"),
}

BIDDING_CATEGORIES = {
    "plan": ("plan", "招标计划"),
    "tender": ("0", "招标公告"),
    "candidate": ("1", "评标结果"),
    "award": ("2", "中标结果"),
}

PURCHASE_CATEGORIES = {
    "notice": ("0", "采购公告"),
    "candidate": ("1", "成交公示"),
    "award": ("2", "成交结果"),
}

# 仅采集招标项目。每个公告栏目直接请求前端“全部”分类；工程/货物/服务
# 仍保留在每条公告的 tenderNature/classificationName 字段中。
DEFAULT_FEEDS = tuple(
    ["bidding.plan.all"]
    + [f"bidding.{category}.all" for category in ("tender", "candidate", "award")]
)


def list_endpoint(module: str, category: str) -> str:
    if category == "plan":
        return f"{BASE_URL}/trade/website/page/findZBPlanInfo"
    name = "findZBInfo" if module == "bidding" else "findCGInfo"
    return f"{BASE_URL}/trade/website/page/{name}"


def detail_endpoint(module: str, category: str) -> str:
    if category == "plan":
        return f"{BASE_URL}/trade/website/page/findPlanDetail"
    name = "findInfoDetail" if module == "bidding" else "findCGInfoDetail"
    return f"{BASE_URL}/trade/website/page/{name}"


def detail_page_url(module: str, category: str, notice_id: str, info_type: str) -> str:
    if category == "plan":
        return f"{BASE_URL}/trade/website/pages/article/planarticle.html?notid={notice_id}"
    page = "ggarticle" if module == "bidding" else "caigouarticle"
    return f"{BASE_URL}/trade/website/pages/article/{page}.html?notid={notice_id}&type={info_type}"


def list_payload(feed: str, *, page: int = 1, page_size: int = 10) -> dict[str, Any]:
    module, category, project_type = feed.split(".", 2)
    info_type = BIDDING_CATEGORIES[category][0] if module == "bidding" else PURCHASE_CATEGORIES[category][0]
    return {
        "tenderNature": "" if project_type == "all" else PROJECT_TYPES[project_type][0],
        "infoType": info_type,
        "publishTimeType": "",
        "provice": "",
        "searchMsg": "",
        "pageSize": page_size,
        "pageCurrent": page,
        "publishOrderBy": "down",
        "bidOpenOrderBy": "",
    }


def feed_labels(feed: str) -> tuple[str, str, str]:
    module, category, project_type = feed.split(".", 2)
    module_label = "招标项目" if module == "bidding" else "采购项目"
    category_label = (BIDDING_CATEGORIES if module == "bidding" else PURCHASE_CATEGORIES)[category][1]
    project_label = "全部" if project_type == "all" else PROJECT_TYPES[project_type][1]
    return module_label, category_label, project_label
