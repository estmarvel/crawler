"""临汾市公共资源交易平台公开公告配置。"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlencode


PLATFORM_NAME: Final[str] = "全国公共资源交易平台（山西省·临汾市）"
PLATFORM_CODE: Final[str] = "lfggzyjy"

WEB_BASE_URL: Final[str] = "http://lfggzyjy.linfen.gov.cn"
INDEX_URL: Final[str] = f"{WEB_BASE_URL}/cmsController.do?goPage&page=index"
NOTICE_LIST_URL: Final[str] = f"{WEB_BASE_URL}/moreInfoController.do?getMoreNoticeInfo"

PAGE_SIZE: Final[int] = 20

# 只默认采集工程建设公开公告。政府采购、土地、矿业权、产权等公开入口先不纳入，
# CA/登录后台也不纳入。
TABLE_NOTICE_TYPES: Final[dict[str, tuple[str, str, str]]] = {
    "gcjs_tender_plan": ("plan", "招标计划", "zbjh"),
    "gcjs_notice": ("tender", "招标公告", "zbgg"),
    "gcjs_zbhxrgs": ("candidate", "中标候选人公示", "hxr"),
    "gcjs_result_notice": ("award", "中标结果公示", "zbjg"),
}

DEFAULT_TABLES: Final[tuple[str, ...]] = tuple(TABLE_NOTICE_TYPES)

DETAIL_ENDPOINTS: Final[dict[str, tuple[str, str]]] = {
    "gcjs_tender_plan": (
        "ggDetailController.do?notice&tableName=gcjs_tender_plan&page=notice&noticeid=",
        "",
    ),
    "gcjs_notice": (
        "moreInfoController.do?getNoticeDetail",
        "/gcjs/gcjsNotice/form?id=",
    ),
    "gcjs_zbhxrgs": (
        "moreInfoController.do?getResultNoticeDetail",
        "/gcjs/gcjsWinNotice/form?id=",
    ),
    "gcjs_result_notice": (
        "moreInfoController.do?getResultNoticeDetail",
        "/gcjs/gcjsResultNotice/form?id=",
    ),
}


def list_url(page: int, rows: int = PAGE_SIZE) -> str:
    values = {"page": max(int(page), 1), "rows": max(int(rows), 1)}
    return f"{NOTICE_LIST_URL}&{urlencode(values)}"


def detail_url(table_name: str, notice_id: str, source_url: str = "") -> str:
    notice_id = str(notice_id or "").strip()
    table_name = str(table_name or "").strip()
    source_url = str(source_url or "").strip()
    endpoint, default_source = DETAIL_ENDPOINTS.get(
        table_name,
        ("moreInfoController.do?getNoticeDetail", source_url),
    )
    if endpoint.startswith("ggDetailController.do"):
        return f"{WEB_BASE_URL}/{endpoint}{notice_id}"
    source = source_url or default_source
    values = {"url": source, "id": notice_id}
    separator = "&" if "?" in endpoint else "?"
    return f"{WEB_BASE_URL}/{endpoint}{separator}{urlencode(values)}"

