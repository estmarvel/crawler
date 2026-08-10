"""中招联合（山西）公开公告栏目配置。"""

from __future__ import annotations

from urllib.parse import urlencode

PLATFORM_NAME = "中招联合（山西）招标采购网"
PLATFORM_CODE = "trade365"
WEB_BASE_URL = "http://shanxi.365trade.com.cn"

PROJECT_TYPES = {
    "engineering": ("工程", "101"),
    "goods": ("货物", "102"),
    "service": ("服务", "103"),
}

# candidate/award 共用源站“结果公示”栏目，Spider 按列表标题分别筛选，
# 避免把候选人误存成结果公告。
CATEGORY_SECTIONS = {
    "tender": "zbgg",
    "change": "bggg",
    "candidate": "jggs",
    "award": "jggs",
}

DEFAULT_FEEDS = tuple(
    f"{category}.{project_type}"
    for category in ("tender", "change", "candidate", "award")
    for project_type in PROJECT_TYPES
)


def list_url(feed: str, page: int) -> str:
    category, project_type = feed.split(".", 1)
    section = CATEGORY_SECTIONS[category]
    type_id = PROJECT_TYPES[project_type][1]
    page_name = "index.jhtml" if page <= 1 else f"index_{page}.jhtml"
    return f"{WEB_BASE_URL}/{section}/{page_name}?{urlencode({'typeId': type_id})}"


def absolute_url(path: str) -> str:
    value = str(path or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("//"):
        return f"http:{value}"
    return f"{WEB_BASE_URL}/{value.lstrip('/')}"
