"""比比网公开招采信息接口配置。"""

from __future__ import annotations

from urllib.parse import urlencode


PLATFORM_NAME = "比比网电子招投标交易平台"
PLATFORM_CODE = "bitbid"
WEB_BASE_URL = "http://www.bitbid.cn"
API_BASE_URL = f"{WEB_BASE_URL}/api/home"
# 官网当前详情页（2026-08-06 前端包）将三类签章 PDF 请求发往
# 当前站点的 /auth 路由；旧 zb.bitbid.cn 会跳转到失效页面。
PDF_BASE_URL = WEB_BASE_URL
PLAN_FILE_BASE_URL = "http://xzb.bitbid.cn"
LIST_URL = f"{API_BASE_URL}/bbzbMoreList"

CATEGORIES = {
    "plan": {"label": "招标计划", "gg_type": 4, "detail_api": "zbjhInfo", "query_type": 2},
    "tender": {"label": "招标公告", "gg_type": 1, "detail_api": "ggInfo", "query_type": 0},
    "candidate": {"label": "中标候选人公示", "gg_type": 2, "detail_api": "hxrInfo", "query_type": 1},
    "award": {"label": "中标结果公示", "gg_type": 3, "detail_api": "zbjgInfo", "query_type": 2},
}
DEFAULT_CATEGORIES = tuple(CATEGORIES)


def source_notice_id(category: str, notice_id: str | int) -> str:
    """比比网各公告表的数字主键会重复，数据库身份必须带栏目命名空间。"""

    value = str(notice_id).strip()
    if category not in CATEGORIES or not value:
        raise ValueError(f"无效的 Bitbid 公告身份：category={category!r}, id={value!r}")
    return f"{category}:{value}"


def list_url(category: str, page: int, page_size: int, **filters: str) -> str:
    params = {
        "pageNum": page,
        "pageSize": page_size,
        "ggType": CATEGORIES[category]["gg_type"],
        "type": 0,
        "faBuTimeType": filters.get("fa_bu_time_type", 0),
        # 前端默认 radio="1"；缺少该参数时接口会返回旧版升序数据。
        "timeType": filters.get("time_type", "1"),
        "beginTime": filters.get("begin_time", ""),
        "endTime": filters.get("end_time", ""),
        "qyType": filters.get("region", 0),
        "name": filters.get("keyword", ""),
    }
    return f"{LIST_URL}?{urlencode(params)}"


def detail_api_url(category: str, notice_id: str | int) -> str:
    endpoint = CATEGORIES[category]["detail_api"]
    return f"{API_BASE_URL}/{endpoint}/{notice_id}"


def detail_page_url(category: str, notice_id: str | int) -> str:
    definition = CATEGORIES[category]
    page = "details" if category == "plan" else "detail"
    return (
        f"{WEB_BASE_URL}/{page}?id={notice_id}&type={definition['query_type']}"
        "&flag=noflag&signFileNameServer=null&gongGaoSou=undefined"
    )


def pdf_url(category: str, notice_id: str | int) -> str:
    if category == "tender":
        path = (
            "/auth/ggWeb/detailGG/ggBack!readGGSignFile.action"
            f"?zbGongGao.id={notice_id}"
        )
    elif category == "candidate":
        path = (
            "/auth/ggWeb/gongShiDetail/DingBiao!readGSSignFile.action"
            f"?dbZhongBiaoGongShi.id={notice_id}"
        )
    elif category == "award":
        path = (
            "/auth/DingBiao!readGGSignFile.action"
            f"?dbZhongBiaoJieGuoGongGao.id={notice_id}"
        )
    else:
        return ""
    return f"{PDF_BASE_URL}{path}"
