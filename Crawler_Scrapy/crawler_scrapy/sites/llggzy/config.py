"""吕梁市公共资源交易中心静态 CMS 栏目配置。"""

from __future__ import annotations

PLATFORM_NAME = "吕梁市公共资源交易中心"
PLATFORM_CODE = "llggzy"
BASE_URL = "http://ggzyjyzx.lvliang.gov.cn"

# 全部栏目均来自各交易大类首页的前端导航；不要从“全部”页反推分类。
MODULES = {
    "engineering": ("工程建设", {
        "tender": ("gcjsZbgg", "招标公告"), "clarification": ("gcjsBggg", "变更公告/澄清答疑"),
        "candidate": ("gcjsYzbgg", "中标候选人公示"), "award": ("gcjsZbgs", "中标公示"),
        "failure": ("gcjsFbgs", "异常公告"), "control_price": ("gcjsLbj", "招标控制价"),
        "withdrawal": ("gcjsCxgg", "撤销公告"), "contract": ("gcjsHtgk", "合同公开"),
        "plan": ("gcjsZbjh", "招标计划"),
    }),
    "water": ("水利工程", {
        "tender": ("slgcZbgg", "招标公告"), "clarification": ("slgcBggg", "变更公告"),
        "control_price": ("slgcZbkzj", "招标控制价"), "candidate": ("slgcYzbgg", "中标候选人公示"),
        "award": ("slgczhongbgg", "中标公告"), "failure": ("slgcFbgg", "废标公告"),
        "contract": ("slgcHtgk", "合同公开"), "plan": ("slgcZbjh", "招标计划"),
    }),
    "transport": ("交通运输", {
        "tender": ("jtysZbgg", "招标公告"), "clarification": ("jtysBggg", "变更公告"),
        "award": ("jtyszhongbgg", "中标公告"), "failure": ("jtysFbgg", "废标公告"),
        "withdrawal": ("jtysCxgg", "撤销公告"), "control_price": ("jtysZbkzj", "招标控制价"),
        "candidate": ("jtYzbgg", "中标候选人公示"), "contract": ("jtysgcHtgk", "合同公开"),
        "plan": ("jtysZbjh", "招标计划"),
    }),
    "property": ("产权交易", {
        "transfer": ("cqjyGpgg", "转让公告"), "clarification": ("cqjyBggg", "变更公告"),
        "award": ("cqjyCjgs", "成交公示"), "failure": ("cqjyLpgs", "流拍公示"),
    }),
    "power": ("电力工程(能源)", {
        "tender": ("dlgcZbgg", "招标公告"), "control_price": ("dlgcZbkzj", "招标控制价"),
        "clarification": ("dlggBgGG", "变更公告"), "award": ("dlgczhongbgg", "中标公告"),
        "failure": ("dlgcFbgg", "废标公告"), "withdrawal": ("dlgcCxgg", "撤销公告"),
        "candidate": ("dlgcYzbgg", "中标候选人公示"), "contract": ("dlgcHtgk", "合同公开"),
        "plan": ("dlgcZbjh", "招标计划"),
    }),
    "mining": ("土矿交易", {
        "transfer": ("tkjyGpgg", "出让公告"), "clarification": ("tkjyBggg", "变更公告"),
        "award": ("tkjyCjgs", "成交公示"),
    }),
    "agriculture": ("农业工程", {
        "tender": ("dzzhZbgg", "招标公告"), "clarification": ("dzzhBggg", "变更公告/澄清答疑"),
        "award": ("dzzhzhongbgg", "中标公告"), "failure": ("dzzhFbgg", "异常公告"),
        "withdrawal": ("dzzhCxgg", "撤销公告"), "candidate": ("dzzhYzbgg", "中标候选人公示"),
        "control_price": ("dzzhLbj", "招标控制价"), "contract": ("dzzhHtgg", "合同公开"),
        "plan": ("dzzhZbjh", "招标计划"),
    }),
    "other": ("其他工程", {
        # 官网“招标公告”按钮错误地指向中标结果栏目 /zbgg；使用“其他工程-全部”页
        # 并在 Spider 中按列表前缀筛选招标公告，避免把中标结果错标成招标公告。
        "plan": ("zbjh", "招标计划"), "tender": ("qtgc", "招标公告"),
        "control_price": ("lbj", "招标控制价"), "clarification": ("bggg", "变更公告/澄清答疑"),
        "candidate": ("yzbgg", "中标候选人公示"), "award": ("zbgg", "中标结果公示"),
        "failure": ("fbgg", "异常公告"), "withdrawal": ("cxgg", "撤销公告"),
        "contract": ("htgk", "合同公开"),
    }),
}

CATEGORY_SCHEMA = {
    "plan": ("招标计划", "zbjh"), "tender": ("招标公告", "zbgg"),
    "transfer": ("招标公告", "crgg"), "candidate": ("中标候选人公示", "hxr"),
    "award": ("中标结果公示", "zbjg"), "contract": ("合同与履约", "htly"),
    "clarification": ("更正结果公示", "bggg"), "control_price": ("更正结果公示", "kzj"),
    "failure": ("更正结果公示", "ycgg"), "withdrawal": ("更正结果公示", "cxgg"),
}

DEFAULT_FEEDS = tuple(f"{module}.{category}" for module, (_, cats) in MODULES.items() for category in cats)


def feed_info(feed: str) -> dict[str, str]:
    module, category = feed.split(".", 1)
    module_label, categories = MODULES[module]
    path, category_label = categories[category]
    notice_type, subtype = CATEGORY_SCHEMA[category]
    return {"module": module, "category": category, "module_label": module_label,
            "category_label": category_label, "path": path, "notice_type": notice_type, "subtype": subtype}


def list_url(feed: str, page: int = 1) -> str:
    path = feed_info(feed)["path"]
    suffix = "index.htm" if page == 1 else f"index_{page}.htm"
    return f"{BASE_URL}/{path}/{suffix}"
