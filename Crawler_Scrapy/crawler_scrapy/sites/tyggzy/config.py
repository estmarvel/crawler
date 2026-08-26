"""太原公共资源交易中心前端公开接口配置。"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping
from urllib.parse import urlencode

PLATFORM_NAME = "太原市公共资源交易中心"
PLATFORM_CODE = "tyggzy"
BASE_URL = "https://ggzy.xzspglj.taiyuan.gov.cn"
API_BASE = f"{BASE_URL}/tyggfwpt-api-home-web"
LIST_URL = f"{API_BASE}/apiJyxx/list"
DETAIL_URL = f"{API_BASE}/apiJyxxDetail/list"
MANAGER_DETAIL_URL = f"{API_BASE}/apiJyxxDetail/gcjsGGDetail"

MODULES = {"engineering": ("1", "工程建设"), "comprehensive": ("3", "综合交易类")}
CATEGORIES = {
    "tender": ("1", "招标公告"), "clarification": ("2", "澄清修改"),
    "control_price": ("3", "控制价公示"), "award": ("4", "中标结果公示"),
    "candidate": ("5", "中标候选人"), "manager_change": ("6", "项目经理(总监)变更"),
    "other": ("8", "其他公告"), "contract": ("9", "合同公示"),
}
DEFAULT_FEEDS = tuple(f"{module}.{category}" for module in MODULES for category in CATEGORIES)


def md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def business_hash(values: Mapping[str, Any]) -> str:
    return md5("".join(f"{key}={values[key]}" for key in sorted(values)) + "abcdefg")


def header_sign(values: Mapping[str, Any]) -> str:
    return md5("".join(f"{key}{values[key]}" for key in sorted(values) if values[key]) + "fe716cbb-a990-40d4-b7c7-7b9db338c6c8")


def signed_form(values: Mapping[str, Any], business_keys: tuple[str, ...] | None = None) -> tuple[bytes, str]:
    base = {key: str(value or "") for key, value in values.items()}
    signed_business = {key: base[key] for key in (business_keys or tuple(base))}
    params = {"hashSign": business_hash(signed_business), **base}
    return urlencode(params).encode("utf-8"), header_sign(params)


def list_values(feed: str, page: int, page_size: int) -> dict[str, str]:
    module, category = feed.split(".", 1)
    return {
        "currentPage": str(page), "pageSize": str(page_size),
        "typeOne": MODULES[module][0], "typeTwo": CATEGORIES[category][0],
        "secondArea": "", "industriesTypeCode": "", "hangYe": "",
        "title": "", "projectCode": "",
    }


def detail_values(feed: str, guid: str) -> dict[str, str]:
    module, category = feed.split(".", 1)
    return {"guid": guid, "typeOne": MODULES[module][0], "typeTwo": CATEGORIES[category][0]}


def labels(feed: str) -> tuple[str, str]:
    module, category = feed.split(".", 1)
    return MODULES[module][1], CATEGORIES[category][1]


def detail_page(feed: str, guid: str) -> str:
    module, category = labels(feed)
    return f"{BASE_URL}/#/transaction-information?name={module}&type={module}-{category}&guid={guid}"
