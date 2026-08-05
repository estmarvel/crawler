"""山西焦煤电子招采平台公开公告接口配置。"""

from __future__ import annotations

PLATFORM_NAME = "山西焦煤电子招采平台"
PLATFORM_CODE = "sxjm"
WEB_BASE_URL = "https://www.sxccdzzcpt.cn"
API_BASE_URL = f"{WEB_BASE_URL}/api/portal/v1/announcement"
LIST_URL = f"{API_BASE_URL}/index"
DETAIL_URL = f"{API_BASE_URL}/details/{{notice_id}}"

# 前端首页四个频道及其栏目。2026-08-04 实抓确认依法项目的
# “招标（预审）公告”全部由 announcement_type=8 返回（包含普通、二次、
# 延期、变更等公告），category=1 + announcement_type=1 当前 total=0。
CHANNELS = {
    "yfxm": {
        "label": "依法项目",
        "category": 1,
        "sections": {
            "zbjh": {"label": "招标计划", "types": ("19",)},
            "zbgg": {"label": "招标（预审）公告", "types": ("8",)},
            "hxr": {"label": "中标候选人公示", "types": ("2",)},
            "zbjg": {"label": "结果公告", "types": ("10",)},
            "zzgg": {"label": "终止公告", "types": ("4",)},
        },
    },
    "zbxm": {
        "label": "招标项目",
        "category": 3,
        "sections": {
            "zbgg": {"label": "招标（预审）公告", "types": ("1",)},
            "hxr": {"label": "中标候选人公示", "types": ("2",)},
            "zbjg": {"label": "中标公告", "types": ("3",)},
            "zzgg": {"label": "终止公告", "types": ("4",)},
        },
    },
    "fzxm": {
        "label": "非招项目",
        "category": 2,
        "sections": {
            "cggg": {"label": "采购（预审）公告", "types": ("5",)},
            "cjhxr": {"label": "成交候选人公示", "types": ("6",)},
            "cjgg": {"label": "成交公告", "types": ("7",)},
            "zzgg": {"label": "终止公告", "types": ("4",)},
        },
    },
    "jycg": {
        "label": "简易采购限额以下",
        "category": 4,
        "sections": {
            "cggg": {"label": "采购公告", "types": ("5",)},
            "zzgg": {"label": "终止公告", "types": ("4",)},
            "cjgg": {"label": "成交公告", "types": ("7",)},
        },
    },
}

DEFAULT_CHANNELS = tuple(CHANNELS)
AES_KEY = b"1234567890123456"
AES_IV = b"1234567890123456"

# SXJM 的“源站公告类型”和公共框架的“字段 Schema”是两个维度：采购公告、
# 成交候选人公示、成交公告分别复用招标公告、候选人、结果公告的字段形状，
# 但不能因此丢失源站类型。数据库导入器会依据 notice_subtype 的末段保存这里
# 定义的中文类型，公共 NoticeSchemaPipeline 则依据 schema_type 做字段校验。
SECTION_TYPES = {
    "zbjh": {"source_type": "招标计划", "schema_type": "招标计划"},
    "zbgg": {"source_type": "招标公告", "schema_type": "招标公告"},
    "cggg": {"source_type": "采购公告", "schema_type": "招标公告"},
    "hxr": {"source_type": "中标候选人公示", "schema_type": "中标候选人公示"},
    "cjhxr": {"source_type": "成交候选人公示", "schema_type": "中标候选人公示"},
    "zbjg": {"source_type": "中标结果公示", "schema_type": "中标结果公示"},
    "cjgg": {"source_type": "成交公告", "schema_type": "中标结果公示"},
    # 公共八类 Schema 没有终止公告字段形状；仅复用招标公告字段，不改变源站类型。
    "zzgg": {"source_type": "终止公告", "schema_type": "招标公告"},
}

ANNOUNCEMENT_TYPE_LABELS = {
    "1": "招标公告",
    "2": "中标候选人公示",
    "3": "中标公告",
    "4": "终止公告",
    "5": "采购公告",
    "6": "成交候选人公示",
    "7": "成交公告",
    "8": "依法项目招标（预审）及其他公告",
    "10": "结果公告",
    "19": "招标计划",
}


def feeds(channels: tuple[str, ...], sections: tuple[str, ...] | None = None):
    """展开所选频道为 (频道, 栏目, 公告类型) 请求单元。"""

    for channel in channels:
        for section, definition in CHANNELS[channel]["sections"].items():
            if sections is not None and section not in sections:
                continue
            for announcement_type in definition["types"]:
                yield channel, section, announcement_type


def list_params(
    channel: str, announcement_type: str, page: int, page_size: int
) -> dict[str, str | int]:
    return {
        "page": page,
        "per_page": page_size,
        "announcement_type": announcement_type,
        "project_type": "",
        "category": CHANNELS[channel]["category"],
    }


def channel_label(channel: str) -> str:
    return str(CHANNELS[channel]["label"])


def section_label(channel: str, section: str) -> str:
    return str(CHANNELS[channel]["sections"][section]["label"])


def source_notice_type(section: str) -> str:
    """返回数据库应保留的源站中文公告类型。"""

    return str(SECTION_TYPES[section]["source_type"])


def schema_notice_type(section: str) -> str:
    """返回公共字段框架用于校验、规范化的 Schema 类型。"""

    return str(SECTION_TYPES[section]["schema_type"])


def announcement_type_label(announcement_type: str | int) -> str:
    """返回接口 announcement_type 数字编码对应的源站名称。"""

    value = str(announcement_type)
    return ANNOUNCEMENT_TYPE_LABELS.get(value, value)


def detail_page_url(notice_id: str | int) -> str:
    return f"{WEB_BASE_URL}/home/detail?id={notice_id}"
