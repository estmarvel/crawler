"""山西新点公开栏目、类型及接口配置。"""

from __future__ import annotations

from itertools import product


PLATFORM_NAME = "山西新点招投标交易平台"
PLATFORM_CODE = "sxxindian"
BASE_URL = "http://www.sxxindian.com"
API_URL = f"{BASE_URL}/EpointWebBuilder/searchlistAction_Custom.action"

BIDDING_CATEGORIES = {
    "plan": ("007", "招标计划"),
    "tender": ("001", "招标公告"),
    "other": ("002", "其他公告"),
    "prequalification": ("003", "资格预审公告"),
    "change": ("004", "变更公告"),
    "candidate": ("005", "中标候选人公示"),
    "award": ("006", "结果公告"),
}
PROJECT_TYPES = {
    "engineering": ("001", "工程"),
    "goods": ("002", "货物"),
    "service": ("003", "服务"),
}
PURCHASE_CATEGORIES = {
    "notice": ("001", "采购公告"),
    "change": ("002", "变更公告"),
    "award": ("003", "结果公告"),
    "contract": ("004", "合同公示"),
    "opinion": ("005", "征求意见"),
    "tender": ("006", "招标公告"),
}
PURCHASE_METHODS = (
    "公开招标",
    "竞争性谈判",
    "竞争性磋商",
    "单一来源",
    "询价",
    "其他",
)

# 招标计划没有工程/货物/服务二级类型，其余栏目均有。
BIDDING_FEEDS = ("bidding.plan.all",) + tuple(
    f"bidding.{category}.{project_type}"
    for category, project_type in product(
        tuple(x for x in BIDDING_CATEGORIES if x != "plan"), tuple(PROJECT_TYPES)
    )
)
PURCHASE_FEEDS = tuple(f"purchase.{category}.all" for category in PURCHASE_CATEGORIES)
DEFAULT_FEEDS = BIDDING_FEEDS + PURCHASE_FEEDS


def list_endpoint(module: str) -> str:
    command = "getSerachlist" if module == "bidding" else "getSerachgglxlist"
    return f"{API_URL}?cmd={command}"


def list_form(
    feed: str,
    *,
    start_date: str,
    end_date: str,
    page_size: int,
    page_index: int,
    purchase_method: str = "",
) -> dict[str, str]:
    module, category, project_type = feed.split(".", 2)
    common = {
        "startdate": start_date,
        "enddate": end_date,
        "shengfen": "",
        "shixian": "",
        "pageSize": str(page_size),
        "pageIndex": str(page_index),
    }
    if module == "bidding":
        common.update({
            "cate2": BIDDING_CATEGORIES[category][0],
            "cate3": "" if project_type == "all" else PROJECT_TYPES[project_type][0],
        })
    else:
        common.update({
            "cate2": PURCHASE_CATEGORIES[category][0],
            "cgfs": purchase_method,
        })
    return common


def feed_labels(feed: str) -> tuple[str, str, str]:
    module, category, project_type = feed.split(".", 2)
    if module == "bidding":
        return "招标信息", BIDDING_CATEGORIES[category][1], (
            "全部" if project_type == "all" else PROJECT_TYPES[project_type][1]
        )
    return "企业采购", PURCHASE_CATEGORIES[category][1], "全部"

