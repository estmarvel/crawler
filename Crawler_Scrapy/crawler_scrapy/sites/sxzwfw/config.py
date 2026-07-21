"""山西省公共资源交易平台公开工程建设公告配置。

列表页是服务端渲染 HTML。前端通过 POST 表单提交栏目、日期区间、交易场所和
项目类型，分页 URL 使用 ``queryContent_<页码>-jyxx.jspx``。
"""

from __future__ import annotations

from typing import Final


PLATFORM_NAME: Final[str] = "山西省公共资源交易平台"
PLATFORM_CODE: Final[str] = "sxzwfw"

WEB_BASE_URL: Final[str] = "https://prec.sxzwfw.gov.cn"
INDEX_URL: Final[str] = f"{WEB_BASE_URL}/jyxx/index.jhtml"
LIST_URL: Final[str] = f"{WEB_BASE_URL}/queryContent-jyxx.jspx"
ATTACHMENT_META_URL: Final[str] = f"{WEB_BASE_URL}/attachment_url.jspx"
ATTACHMENT_DOWNLOAD_URL: Final[str] = f"{WEB_BASE_URL}/attachment.jspx"

# 工程建设页面“信息类型”筛选的前端真实 channelId。
ENGINEERING_SECTION_CHANNELS: Final[dict[str, tuple[str, str]]] = {
    "zbjh": ("198", "招标计划"),
    "zbgg_zys": ("12", "招标/资审公告"),
    "bg": ("13", "更正公告"),
    "hxr": ("14", "中标候选人公示"),
    "gs": ("15", "中标结果公示"),
    "qt": ("16", "其他公告"),
}

# 政府采购暂时只接入更正和结果。采购公告 channelId=18 的方法已记录在文档中，
# 在字段方案确认前不加入可运行栏目，防止被误采成工程建设招标公告。
GOVERNMENT_SECTION_CHANNELS: Final[dict[str, tuple[str, str]]] = {
    "zc_gz": ("19", "政府采购更正公告"),
    "zc_jg": ("20", "政府采购中标结果公告"),
}

SECTION_CHANNELS: Final[dict[str, tuple[str, str]]] = {
    **ENGINEERING_SECTION_CHANNELS,
    **GOVERNMENT_SECTION_CHANNELS,
}

# 不改变现有历史脚本行为：未传 sections 时仍只采工程建设六个栏目。
DEFAULT_SECTIONS: Final[tuple[str, ...]] = tuple(ENGINEERING_SECTION_CHANNELS)
PAGE_SIZE: Final[int] = 10


def build_list_url(page: int) -> str:
    """构造与前端分页一致的 POST 地址。"""

    if page <= 1:
        return LIST_URL
    return LIST_URL.replace("queryContent-", f"queryContent_{page}-")


def build_list_form(
    section: str,
    *,
    start_date: str = "",
    end_date: str = "",
    days: str = "",
    title: str = "",
    origin: str = "",
    project_type: str = "",
) -> dict[str, str]:
    """构造前端工程建设筛选表单。

    显式日期区间优先于 ``inDates``。这样历史采集可以按月拆分，避免一次查询
    返回过多分页；日常增量采集仍可只传最近 N 天。
    """

    channel_id, _ = SECTION_CHANNELS[section]
    has_range = bool(start_date or end_date)
    return {
        "title": str(title or ""),
        "channelId": channel_id,
        "inDates": "" if has_range else str(days or ""),
        "beginTime": str(start_date or ""),
        "endTime": str(end_date or ""),
        "origin": str(origin or ""),
        "ext": str(project_type or ""),
    }
