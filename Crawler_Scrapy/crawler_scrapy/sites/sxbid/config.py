"""山西省招标投标公共服务平台公开公告栏目配置。"""

from __future__ import annotations

from urllib.parse import urlencode

PLATFORM_NAME = "山西省招标投标公共服务平台"
PLATFORM_CODE = "sxbid"
WEB_BASE_URL = "https://www.sxbid.com.cn"

# 该页面的八个栏目与统一公告 Schema 一一对应。
CATEGORIES = {
    "plan": {"code": "16", "label": "招标计划", "path_type": "0"},
    "prequalification": {"code": "10", "label": "资格预审公告", "path_type": "1"},
    "tender": {"code": "11", "label": "招标公告", "path_type": "1"},
    "candidate": {"code": "12", "label": "中标候选人公示", "path_type": "2"},
    "final_candidate": {"code": "15", "label": "定标候选人公示", "path_type": "4"},
    "award": {"code": "13", "label": "中标结果公示", "path_type": "3"},
    "correction": {"code": "14", "label": "更正结果公示", "path_type": "1"},
    "contract": {"code": "17", "label": "合同与履约", "path_type": "9"},
}

DEFAULT_CATEGORIES = tuple(CATEGORIES)


def list_url(category: str) -> str:
    return f"{WEB_BASE_URL}/f/new/notice/list/{CATEGORIES[category]['code']}"


def list_form(page: int, page_size: int) -> dict[str, str]:
    return {
        "pageNo": str(page),
        "pageSize": str(min(max(int(page_size), 1), 100)),
        "title": "",
        "recentType": "",
    }


def detail_url(path_type: str, notice_id: str) -> str:
    return f"{WEB_BASE_URL}/f/new/notice/{path_type}/{notice_id}"


def download_url(file_id: str, *, file_type: str = "3", origin_name: bool = False) -> str:
    values = {"type": file_type, "fname": file_id}
    if origin_name:
        values["originName"] = "1"
    return f"{WEB_BASE_URL}/f/downloadByFileName?{urlencode(values)}"


def absolute_url(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("//"):
        return f"https:{text}"
    return f"{WEB_BASE_URL}/{text.lstrip('/')}"
