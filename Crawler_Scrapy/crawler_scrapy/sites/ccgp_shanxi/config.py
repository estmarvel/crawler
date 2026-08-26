"""山西政府采购网公开栏目与接口配置。"""

from __future__ import annotations

from urllib.parse import urlencode

PLATFORM_NAME = "山西政府采购网"
PLATFORM_CODE = "ccgp_shanxi"
WEB_BASE_URL = "http://www.ccgp-shanxi.gov.cn"
LIST_URL = f"{WEB_BASE_URL}/portal/category"
DETAIL_URL = f"{WEB_BASE_URL}/portal/detail"
CATEGORY_PARENT_ID = "138010"

# 只请求叶子栏目，避免父子栏目重复。键保持稳定，便于命令行选择和输出审计。
FEEDS = {
    "intention": ("采购意向公开", "采购意向公开", "ZcyAnnouncement10016"),
    "notice.open": ("采购公告", "公开招标公告", "ZcyAnnouncement3001"),
    "notice.invited": ("采购公告", "邀请招标公告", "ZcyAnnouncement3020"),
    "notice.negotiation": ("采购公告", "竞争性谈判公告", "ZcyAnnouncement3002"),
    "notice.consultation": ("采购公告", "竞争性磋商公告", "ZcyAnnouncement3011"),
    "notice.inquiry": ("采购公告", "询价公告", "ZcyAnnouncement3003"),
    "notice.prequal_open": ("采购公告", "公开招标资格预审公告", "ZcyAnnouncement2001"),
    "notice.prequal_invited": ("采购公告", "邀请招标资格预审公告", "ZcyAnnouncement3008"),
    "notice.cooperation": ("采购公告", "合作创新采购公告", "78-979055"),
    "notice.rd_negotiation": ("采购公告", "研发谈判文件公告", "78-180665"),
    "result.award": ("采购结果公告", "中标（成交）结果公告", "ZcyAnnouncement3004"),
    "result.termination": ("采购终止公告", "终止公告", "ZcyAnnouncement3015"),
    "result.failed": ("采购终止公告", "废标公告", "ZcyAnnouncement3007"),
    "result.changed": ("采购结果公告", "采购结果变更公告", "ZcyAnnouncement3017"),
    "change.correction": ("采购变更公告", "更正（变更）公告", "ZcyAnnouncement3005"),
    "change.clarification": ("采购变更公告", "澄清（修改）公告", "ZcyAnnouncement3006"),
    "change.suspension": ("采购变更公告", "中止（暂停）公告", "ZcyAnnouncement3018"),
    "contract": ("采购合同公告", "合同公示", "ZcyAnnouncement3010"),
    "contract.change": ("采购合同变更公告", "合同变更公告", "78-108377"),
    "acceptance": ("履约验收公告", "履约验收公告", "ZcyAnnouncement3016"),
    "opinion.demand": ("采购意见征询", "采购需求公示", "ZcyAnnouncement3014"),
    "opinion.single": ("采购意见征询", "单一来源公示", "ZcyAnnouncement3012"),
    "opinion.import": ("采购意见征询", "进口产品论证意见公示", "ZcyAnnouncement3013"),
    "sme": ("中小企业预留执行情况", "面向中小企业预留项目执行情况", "ZcyAnnouncement14001"),
    "history.notice": ("历史未归类公告", "历史采购公告", "78-680207"),
    "history.result": ("历史未归类公告", "历史结果公告", "78-824144"),
    "history.change": ("历史未归类公告", "历史变更公告", "78-867960"),
    "history.opinion": ("历史未归类公告", "历史意见征询", "78-325198"),
    "history.contract": ("历史未归类公告", "历史合同公告", "78-763674"),
}
DEFAULT_FEEDS = tuple(FEEDS)


def detail_api_url(article_id: str) -> str:
    return f"{DETAIL_URL}?{urlencode({'articleId': article_id})}"


def detail_page_url(article_id: str) -> str:
    return f"{WEB_BASE_URL}/site/detail?{urlencode({'parentId': CATEGORY_PARENT_ID, 'articleId': article_id})}"
