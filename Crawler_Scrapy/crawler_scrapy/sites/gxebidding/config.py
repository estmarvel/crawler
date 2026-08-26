"""国信 e 采（山西）公开公告入口和类型配置。"""

from __future__ import annotations

from urllib.parse import urlencode


PLATFORM_NAME = "国信e采（山西）交易平台"
PLATFORM_CODE = "gxebidding"
WEB_BASE_URL = "https://gx.e-bidding.org"
IFRAME_PATH = "/sxyczscms/category/iframe.html"
PDF_PATH = (
    "/bidprocurement/datacenter-cebpubserver/cebpubserver/"
    "dataCeboubServerCommonController/openFileById"
)


CHANNELS = {
    "lawful": {
        "method": "01",
        "label": "依法招标",
        "project_nature": "依法必须招标",
    },
    "nonlawful": {
        "method": "02",
        "label": "非依法招标",
        "project_nature": "非依法招标",
    },
    "purchase": {
        "method": "00",
        "label": "非招标采购",
        "project_nature": "非招标采购",
    },
}
DEFAULT_CHANNELS = tuple(CHANNELS)


CATEGORIES = {
    "tender": {
        "category_id": "2",
        "schema": "招标公告",
        "tab": "招标公告",
        "path_family": "bulletin",
        "file_type": "2",
    },
    "change": {
        "category_id": "3",
        "schema": "更正结果公示",
        "tab": "变更公告",
        "path_family": "change",
        "file_type": "3",
    },
    "candidate": {
        "category_id": "5",
        "schema": "中标候选人公示",
        "tab": "中标候选人公示",
        "path_family": "candidate",
        "file_type": "5",
    },
    "award": {
        "category_id": "4",
        "schema": "中标结果公示",
        "tab": "采购结果公示",
        "path_family": "result",
        "file_type": "4",
    },
    "termination": {
        "category_id": "6",
        "schema": "更正结果公示",
        "tab": "废标公告",
        "path_family": "fail",
        "file_type": "6",
    },
}
DEFAULT_CATEGORIES = tuple(CATEGORIES)


def source_label(channel: str, category: str) -> str:
    if channel == "purchase":
        return {
            "tender": "采购公告",
            "change": "采购变更公告",
            "candidate": "成交候选人公示",
            "award": "成交结果公示",
            "termination": "采购终止公告",
        }[category]
    return {
        "tender": "招标公告",
        "change": "变更/二次公告",
        "candidate": "中标候选人公示",
        "award": "中标结果公示",
        "termination": "终止公告",
    }[category]


def list_url(
    channel: str,
    category: str,
    page: int,
    *,
    keyword: str = "",
) -> str:
    channel_definition = CHANNELS[channel]
    category_definition = CATEGORIES[category]
    values = {
        # 源站把 300 当成“全部”，其前端实现实际回溯 300 个月。
        "dates": "300",
        "word": keyword,
        "categoryId": category_definition["category_id"],
        "tenderMethod": channel_definition["method"],
        "tabName": category_definition["tab"],
        "status": "",
        "page": max(int(page), 1),
    }
    return f"{WEB_BASE_URL}{IFRAME_PATH}?{urlencode(values)}"


def source_notice_id(path_family: str, cms_id: str | int) -> str:
    value = str(cms_id).strip()
    if not value or not path_family:
        raise ValueError("国信e采公告身份缺少路径族或CMS编号")
    return f"{path_family}:{value}"


def pdf_url(file_type: str | int, file_id: str) -> str:
    values = {"fileType": str(file_type), "id": str(file_id).strip()}
    return f"{WEB_BASE_URL}{PDF_PATH}?{urlencode(values)}"
