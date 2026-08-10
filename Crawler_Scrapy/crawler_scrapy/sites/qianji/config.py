"""千极链公开招采栏目与接口配置。"""

from __future__ import annotations

from urllib.parse import urlencode

PLATFORM_NAME = "千极数采电子交易平台"
PLATFORM_CODE = "qianji"
WEB_BASE_URL = "https://www.qianjilink.com"
API_BASE_URL = f"{WEB_BASE_URL}/dev-api/qjsc"
LIST_URL = f"{API_BASE_URL}/gcjsWebContent/list"
DETAIL_URL = f"{API_BASE_URL}/gcjsWebContent/detail"

# 招标计划在源站只有“全部”；其余四种公告均有工程、货物、服务。
FEEDS = {
    "plan.all": ("招标计划", "全部", "fdb21955c0ec41c68f359000b8227b79"),
    "tender.engineering": ("招标公告", "工程", "d3bcf4c4ac3d421f9b72adf7d34485ee"),
    "tender.goods": ("招标公告", "货物", "35194a19bbed418e915de46d72303aa8"),
    "tender.service": ("招标公告", "服务", "d2ada2cac1a04f07a9720774808806ab"),
    "change.engineering": ("变更公告", "工程", "1c0b30757a7d4bf5a7ffde62207daca7"),
    "change.goods": ("变更公告", "货物", "2a54c5a7379e4d439cb04c02feaf5d19"),
    "change.service": ("变更公告", "服务", "465c254b77de451c9800bc731d744161"),
    "candidate.engineering": ("中标候选人公示", "工程", "cdbf04eb0505400d930b3bbe8a39f9eb"),
    "candidate.goods": ("中标候选人公示", "货物", "0e38a901c14f4217bbb04c93006f1ba0"),
    "candidate.service": ("中标候选人公示", "服务", "e4c84d458c484fb9b53d0642e6f00733"),
    "award.engineering": ("结果公告", "工程", "6b6525f9c3f34b87b1428e3e9ed5c978"),
    "award.goods": ("结果公告", "货物", "8d8f81b9e93946dbb744fa7328ad3580"),
    "award.service": ("结果公告", "服务", "ba434b90c13f4765bc364254d8e0bd9f"),
}
DEFAULT_FEEDS = tuple(FEEDS)


def list_url(feed: str, page: int, page_size: int) -> str:
    return f"{LIST_URL}?{urlencode({'typeId': FEEDS[feed][2], 'state': 100, 'pageNum': page, 'pageSize': page_size, 'zbUnitName': '', 'isAll': 1})}"


def detail_api_url(notice_id: str) -> str:
    return f"{DETAIL_URL}?{urlencode({'id': notice_id})}"


def detail_page_url(notice_id: str) -> str:
    return f"{WEB_BASE_URL}/detail.html?{urlencode({'id': notice_id})}"
