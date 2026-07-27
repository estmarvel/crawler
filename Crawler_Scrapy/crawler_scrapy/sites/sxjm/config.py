"""山西焦煤电子招采平台公开公告接口配置。"""

from __future__ import annotations

PLATFORM_NAME = "山西焦煤电子招采平台"
PLATFORM_CODE = "sxjm"
WEB_BASE_URL = "https://www.sxccdzzcpt.cn"
API_BASE_URL = f"{WEB_BASE_URL}/api/portal/v1/announcement"
LIST_URL = f"{API_BASE_URL}/index"
DETAIL_URL = f"{API_BASE_URL}/details/{{notice_id}}"

# 前端首页四个频道及其栏目。一个栏目可对应多个公告类型，例如依法项目的
# “招标（预审）公告”也包含延期、变更等其他公告（announcement_type=8）。
CHANNELS = {
    "yfxm": {
        "label": "依法项目",
        "category": 1,
        "sections": {
            "zbjh": {"label": "招标计划", "types": ("19",)},
            "zbgg": {"label": "招标（预审）公告", "types": ("1", "8")},
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


def detail_page_url(notice_id: str | int) -> str:
    return f"{WEB_BASE_URL}/home/detail?id={notice_id}"
