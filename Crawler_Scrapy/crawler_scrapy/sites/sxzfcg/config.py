"""Shanxi government procurement public announcement API config."""

from __future__ import annotations

from typing import Final
from urllib.parse import quote, urlencode


PLATFORM_NAME: Final[str] = "山西省政府采购网"
PLATFORM_CODE: Final[str] = "sxzfcg"
WEB_BASE_URL: Final[str] = "http://www.ccgp-shanxi.gov.cn"
LIST_URL: Final[str] = f"{WEB_BASE_URL}/portal/category"
DETAIL_URL: Final[str] = f"{WEB_BASE_URL}/portal/detail"
DISTRICT_CODE: Final[str] = "149900"


CATEGORIES: Final[dict[str, dict[str, str]]] = {
    "tender": {
        "code": "ZcyAnnouncement1",
        "schema": "招标公告",
        "label": "采购公告",
    },
    "award": {
        "code": "ZcyAnnouncement2",
        "schema": "中标结果公示",
        "label": "结果公告",
    },
    "change": {
        "code": "ZcyAnnouncement3",
        "schema": "更正结果公示",
        "label": "变更公告",
    },
    "contract": {
        "code": "ZcyAnnouncement4",
        "schema": "合同与履约",
        "label": "合同公告",
    },
}
DEFAULT_CATEGORIES: Final[tuple[str, ...]] = ("tender", "award", "change", "contract")


def list_payload(category: str, page: int, page_size: int, *, keyword: str = "") -> dict[str, object]:
    values: dict[str, object] = {
        "pageNo": max(int(page), 1),
        "pageSize": max(int(page_size), 1),
        "categoryCode": CATEGORIES[category]["code"],
        "districtCode": [DISTRICT_CODE],
        "isProvince": True,
    }
    if keyword:
        values["keyword"] = keyword
    return values


def detail_api_url(article_id: str) -> str:
    return f"{DETAIL_URL}?{urlencode({'articleId': str(article_id or '').strip()})}"


def detail_page_url(article_id: str) -> str:
    return f"{WEB_BASE_URL}/site/detail?articleId={quote(str(article_id or '').strip(), safe='')}"
