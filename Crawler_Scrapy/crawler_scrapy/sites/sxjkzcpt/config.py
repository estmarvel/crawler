"""山西交控公开交易信息栏目和服务端 HTML 接口。"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlencode


PLATFORM_NAME: Final[str] = "山西交控招投标采购服务平台"
PLATFORM_CODE: Final[str] = "sxjkzcpt"
WEB_BASE_URL: Final[str] = "https://www.sxjkzcpt.com.cn"
BOOTSTRAP_URL: Final[str] = f"{WEB_BASE_URL}/pub/jyxx_pages.html?menuCode=zbcg"
LIST_URL: Final[str] = f"{WEB_BASE_URL}/pub/JYXX_pages.htm"
DETAIL_POST_URL: Final[str] = f"{WEB_BASE_URL}/pub/detail_pages.htm"
FILE_CHECK_BASE_URL: Final[str] = f"{WEB_BASE_URL}/pub/checkFile"
FILE_DOWNLOAD_BASE_URL: Final[str] = f"{WEB_BASE_URL}/fileInfo/downloadFile"

# 本次只接入用户指定的前两栏。NBCG 即使能列出部分公开标题也不在授权范围，
# FZBCG 留待后续独立验证，避免把非招标采购机械映射为招标公告。
CHANNELS: Final[dict[str, tuple[str, str]]] = {
    "zbcg": ("ZBCG", "依法必须招标项目"),
    "qzbcg": ("QZBCG", "其他必须招标项目"),
}

CATEGORIES: Final[dict[str, tuple[str, str]]] = {
    "plan": ("CGJHFB", "采购计划"),
    "tender": ("CGGG", "招标公告"),
    "change": ("GZGG", "变更公告"),
    "candidate": ("ZBHXRGS", "中标候选人公示"),
    "award": ("JGGG", "结果公告"),
    "contract": ("HTBA", "合同订立信息"),
}

FEEDS: Final[dict[str, tuple[str, str, str, str]]] = {
    "zbcg.plan": ("ZBCG", "CGJHFB", "依法必须招标项目", "采购计划"),
    "zbcg.tender": ("ZBCG", "CGGG", "依法必须招标项目", "招标公告"),
    "zbcg.change": ("ZBCG", "GZGG", "依法必须招标项目", "变更公告"),
    "zbcg.candidate": ("ZBCG", "ZBHXRGS", "依法必须招标项目", "中标候选人公示"),
    "zbcg.award": ("ZBCG", "JGGG", "依法必须招标项目", "结果公告"),
    "zbcg.contract": ("ZBCG", "HTBA", "依法必须招标项目", "合同订立信息"),
    "qzbcg.tender": ("QZBCG", "CGGG", "其他必须招标项目", "招标公告"),
    "qzbcg.change": ("QZBCG", "GZGG", "其他必须招标项目", "变更公告"),
    "qzbcg.candidate": ("QZBCG", "ZBHXRGS", "其他必须招标项目", "中标候选人公示"),
    "qzbcg.award": ("QZBCG", "JGGG", "其他必须招标项目", "结果公告"),
}
DEFAULT_CHANNELS: Final[tuple[str, ...]] = tuple(CHANNELS)
DEFAULT_CATEGORIES: Final[tuple[str, ...]] = tuple(CATEGORIES)


def detail_page_url(notice_id: str) -> str:
    return f"{WEB_BASE_URL}/pub/detail_pages.html?{urlencode({'info': notice_id})}"


def attachment_url(file_id: str) -> str:
    return f"{FILE_DOWNLOAD_BASE_URL}/{file_id}"
