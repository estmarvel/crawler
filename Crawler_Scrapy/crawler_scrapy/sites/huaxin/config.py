"""华新阳光采购平台的站点专用配置。

当前使用的公开列表、详情 API 不需要登录 Token。
"""

from __future__ import annotations

from typing import Final


PLATFORM_NAME: Final[str] = "华新阳光采购平台"
PLATFORM_CODE: Final[str] = "huaxin"

WEB_BASE_URL: Final[str] = "https://www.ygcgpt.com"
API_ORIGIN: Final[str] = "https://www.ygcgpt.com:9998"
API_BASE: Final[str] = f"{API_ORIGIN}/bidding"

ANNOUNCEMENT_LIST_URL: Final[str] = (
    f"{API_BASE}/bidAnnouncement/getWebAnnPage"
)
ANNOUNCEMENT_DETAIL_URL: Final[str] = (
    f"{API_BASE}/bidAnnouncement/getAnnWebByAnnId"
)
INPUT_ANNOUNCEMENT_DETAIL_URL: Final[str] = (
    f"{API_BASE}/web/inputAnnouncement/getInputAnn"
)
BID_PLAN_DETAIL_URL: Final[str] = (
    f"{API_BASE}/web/biddingPlan/getBiddingPlan"
)
BID_PLAN_LIST_URL: Final[str] = (
    f"{API_BASE}/web/biddingPlan/biddingPlanList"
)
BIDDING_FILE_QUERY_URL: Final[str] = f"{API_BASE}/file/query"

# 一级栏目与前端 annClassifications 的对应关系。
SECTION_CLASSIFICATIONS: Final[dict[str, tuple[str, ...]]] = {
    "zbgg_zys": ("1",),
    "hxr": ("2",),
    "gs": ("3",),
    "zbjh": ("4",),
}

DEFAULT_SECTIONS: Final[tuple[str, ...]] = tuple(SECTION_CLASSIFICATIONS)

ANNOUNCEMENT_PAYLOAD_TEMPLATE: Final[dict[str, object]] = {
    "annAttribute": "",
    "annAttributeList": [],
    "annNum": "",
    "annTitle": "",
    "bidName": "",
    "classifications": ["A", "B", "C"],
    "createEndTime": "",
    "createStartTime": "",
    "industryList": [],
    "navId": "1597613483694370816",
    "openWay": "",
    "purDiyCodes": [],
    "purName": "",
    "regionList": [],
    "type": "",
    "typeCode": "",
}


def build_list_payload(section: str, page: int, page_size: int) -> dict[str, object]:
    """构造与华新前端一致的列表 JSON 请求体。"""

    classifications = SECTION_CLASSIFICATIONS[section]
    return {
        **ANNOUNCEMENT_PAYLOAD_TEMPLATE,
        "pageNum": page,
        "pageSize": page_size,
        "annClassifications": list(classifications),
    }


def build_bid_plan_list_payload(page: int, page_size: int) -> dict[str, object]:
    """构造前端首页实际使用的招标计划列表请求体。"""

    return {
        "current": page,
        "size": page_size,
        # 前端只展示已经发布的计划。
        "status": 6,
    }
