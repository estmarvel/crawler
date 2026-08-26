"""招采进宝电子招标投标交易平台（山西）前端公开 API 与栏目配置。"""

from __future__ import annotations

from typing import Any


PLATFORM_NAME = "招采进宝电子招标投标交易平台（山西）"
PLATFORM_CODE = "sxty_ebidding"
BASE_URL = "https://sxty.ebidding.net.cn"
SITE_ID = "744"

ENTRY_PATH = "/cms/sx/webfile/zdsx=jyxx/index.html"
LIST_API_PATH = "/cms/api/dynamicData/queryContentPage"
DETAIL_API_PATH = "/cms/api/dynamicData/queryContent"
DETAIL_PAGE_PATH = "/cms/sx/webfile/detail/index.html"

ENTRY_URL = f"{BASE_URL}{ENTRY_PATH}"
LIST_API_URL = f"{BASE_URL}{LIST_API_PATH}"
DETAIL_API_URL = f"{BASE_URL}{DETAIL_API_PATH}"


# 页面把建设工程和企业采购放在两个一级栏目中。feed 名称同时保留一级栏目
# 和公告阶段，避免两个栏目中相同中文名称互相覆盖。
FEEDS: dict[str, dict[str, str]] = {
    "engineering.plan": {
        "channel": "engineering", "channel_label": "建设工程",
        "category": "plan", "category_id": "7442510",
        "source_label": "招标计划", "schema": "招标计划",
    },
    "engineering.tender": {
        "channel": "engineering", "channel_label": "建设工程",
        "category": "tender", "category_id": "7442520",
        "source_label": "招标公告", "schema": "招标公告",
    },
    "engineering.change": {
        "channel": "engineering", "channel_label": "建设工程",
        "category": "change", "category_id": "7442530",
        "source_label": "变更公告", "schema": "更正结果公示",
    },
    "engineering.candidate": {
        "channel": "engineering", "channel_label": "建设工程",
        "category": "candidate", "category_id": "7442540",
        "source_label": "中标候选人公示", "schema": "中标候选人公示",
    },
    "engineering.award": {
        "channel": "engineering", "channel_label": "建设工程",
        "category": "award", "category_id": "7442550",
        "source_label": "中标公告", "schema": "中标结果公示",
    },
    "engineering.other": {
        "channel": "engineering", "channel_label": "建设工程",
        "category": "other", "category_id": "7442560",
        "source_label": "其他公告", "schema": "更正结果公示",
    },
    "engineering.termination": {
        "channel": "engineering", "channel_label": "建设工程",
        "category": "termination", "category_id": "7442570",
        "source_label": "暂停/终止公告", "schema": "更正结果公示",
    },
    "enterprise.plan": {
        "channel": "enterprise", "channel_label": "企业采购",
        "category": "plan", "category_id": "7442110",
        "source_label": "采购计划", "schema": "招标计划",
    },
    "enterprise.tender": {
        "channel": "enterprise", "channel_label": "企业采购",
        "category": "tender", "category_id": "7442120",
        "source_label": "采购公告", "schema": "招标公告",
    },
    "enterprise.change": {
        "channel": "enterprise", "channel_label": "企业采购",
        "category": "change", "category_id": "7442130",
        "source_label": "变更公告", "schema": "更正结果公示",
    },
    "enterprise.candidate": {
        "channel": "enterprise", "channel_label": "企业采购",
        "category": "candidate", "category_id": "7442140",
        "source_label": "成交候选人公示", "schema": "中标候选人公示",
    },
    "enterprise.award": {
        "channel": "enterprise", "channel_label": "企业采购",
        "category": "award", "category_id": "7442150",
        "source_label": "成交结果公告", "schema": "中标结果公示",
    },
    "enterprise.other": {
        "channel": "enterprise", "channel_label": "企业采购",
        "category": "other", "category_id": "7442160",
        "source_label": "其他公告", "schema": "更正结果公示",
    },
}
DEFAULT_FEEDS = tuple(FEEDS)


def detail_page_url(content_id: str | int) -> str:
    return f"{BASE_URL}{DETAIL_PAGE_PATH}?contentId={str(content_id).strip()}"


def list_payload(
    feed: str,
    page: int,
    page_size: int,
    *,
    publish_date: str = "",
    publish_end_date: str = "",
    keyword: str = "",
    city: str = "",
) -> dict[str, Any]:
    definition = FEEDS[feed]
    return {
        "pageNo": max(int(page), 1),
        "pageSize": max(1, min(int(page_size), 50)),
        "dto": {
            "siteId": SITE_ID,
            "categoryId": definition["category_id"],
            "publishDate": publish_date,
            "publishEndDate": publish_end_date,
            "title": keyword,
            "city": city,
        },
    }


def detail_payload(content_id: str | int) -> dict[str, Any]:
    return {
        "contentId": str(content_id).strip(),
        "packageId": None,
        "categoryId": None,
    }
