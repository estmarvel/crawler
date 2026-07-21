"""玖邦招标采购电子交易平台公开招投标接口配置。

配置来自 ``crawler_scrapy/docs/jiubang`` 中的生产前端 JS。公开列表、详情和
附件查询均未要求登录 Token；浏览器即使没有 ``jbcookie`` 也会请求这些接口。
"""

from __future__ import annotations

from typing import Final


PLATFORM_NAME: Final[str] = "玖邦招标采购电子交易平台"
PLATFORM_CODE: Final[str] = "jiubang"

WEB_BASE_URL: Final[str] = "https://www.bjjbkj.cn"
API_ORIGIN: Final[str] = "https://www.bjjbkj.cn:9998"
API_BASE: Final[str] = f"{API_ORIGIN}/bidding"

ANNOUNCEMENT_LIST_URL: Final[str] = (
    f"{API_BASE}/bidAnnouncement/getWebAnnPage"
)
ANNOUNCEMENT_DETAIL_URL: Final[str] = (
    f"{API_BASE}/bidAnnouncement/getAnnWebByAnnId"
)
INPUT_ANNOUNCEMENT_DETAIL_URL: Final[str] = (
    f"{API_ORIGIN}/web/inputAnnouncement/getInputAnn"
)
BID_PLAN_LIST_URL: Final[str] = (
    f"{API_BASE}/web/biddingPlan/biddingPlanList"
)
# 玖邦当前上传的首页 JS 已确认招标计划列表和详情路由；详情懒加载分块
# 2413.32016dd6.js 未包含在 docs 中。该接口沿用同版本 TWS 系统（华新）使用的
# 公开路径，并单独保留为配置项，若后续前端升级只需修改这里。
BID_PLAN_DETAIL_URL: Final[str] = (
    f"{API_BASE}/web/biddingPlan/getBiddingPlan"
)
BIDDING_FILE_QUERY_URL: Final[str] = f"{API_BASE}/file/query"

SECTION_CLASSIFICATIONS: Final[dict[str, tuple[str, ...]]] = {
    "zbgg_zys": ("1",),
    "hxr": ("2",),
    "gs": ("3",),
    "zbjh": ("4",),
}

DEFAULT_SECTIONS: Final[tuple[str, ...]] = tuple(SECTION_CLASSIFICATIONS)


def build_list_payload(section: str, page: int, page_size: int) -> dict[str, object]:
    """构造玖邦招标信息页 ``querydata`` 使用的请求体。"""

    return {
        "pageNum": page,
        "pageSize": page_size,
        "annClassifications": list(SECTION_CLASSIFICATIONS[section]),
        "classifications": ["A", "B", "C"],
        "purDiyCode": "",
    }


def build_bid_plan_list_payload(page: int, page_size: int) -> dict[str, object]:
    """构造玖邦首页招标计划栏目使用的请求体。"""

    return {
        "current": page,
        "size": page_size,
        "status": 6,
    }

