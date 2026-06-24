"""
=============================================================================
华新阳光采购平台 (ygcgpt.com) - 配置中心
=============================================================================
4 栏目配置，各自有子类型映射 + 字段定义
=============================================================================
"""

import logging
import os

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    认证 & 基础配置                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

ENABLE_ATTACHMENT_DOWNLOAD = False

BASE_URL = "https://www.ygcgpt.com:9998"
API_BASE = f"{BASE_URL}/bidding"

# JWT Token（浏览器获取 hxcookie → Bearer xxx）
AUTH_TOKEN = "Bearer eyJhbGciOiJIUzUxMiJ9.eyJsb2dpbl91c2VyX2tleSI6IjU0NzE3OTY4LWMyZWItNDEzZi04YjNkLWJiYmU1ZDEzOGY5ZSJ9.pL2P78nkzoHPHntf1mFSpBs28TqRGYnJWq7Rq0sCvZNYI3AkBENgFfUz1tYmF3Ds5oPVccGRsG9SmlJUduf9sg"

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    API 端点                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# 公告列表（POST，所有非招标计划栏目共用）
API_ANNOUNCE_LIST = f"{API_BASE}/bidAnnouncement/getWebAnnPage"

# 公告详情（GET，带 annId & n 参数）
API_ANNOUNCE_DETAIL = f"{API_BASE}/bidAnnouncement/getAnnWebByAnnId"

# 招标计划详情（GET，整数 ID 扫描）
API_BID_PLAN_DETAIL = f"{API_BASE}/web/biddingPlan/getBiddingPlan"

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    爬取参数                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# 并发线程数
CONCURRENT_WORKERS = 10

# 每次请求间隔 (秒)
REQUEST_DELAY_MIN, REQUEST_DELAY_MAX = 0.15, 0.50

# 翻页间隔
PAGE_TRANSITION_DELAY = (0.3, 0.8)

# 栏目间冷却
SECTION_COOLDOWN = (1.0, 3.0)

# 请求超时 / 重试
REQUEST_TIMEOUT = 60
MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 2

# ── 采集范围参数 ──
# 四个一级栏目分别最多取前 200 条：
#   1 招标计划、2 招标资审公告、3 候选公示、4 结果公示
# 说明：这是“最多 200 条”，不是必须凑满；招标计划等实际不足 200 条时按平台实际数量输出并正常结束。
MAX_RECORDS_PER_SECTION = 200

# 列表仍按每页 50 条请求，避免一次性拉太大导致接口不稳定；200 条通常需要 4 页。
DETAIL_PAGE_SIZE = 50
MAX_LIST_PAGES = 10

# 每次列表/详情请求如果代理失败，最多换 IP 重试次数。
PROXY_RETRY_PER_REQUEST = 3

# 数据不足 200 条是正常情况：列表接口返回少于 pageSize、total < 200 或到最后一页时立即结束，
# 不为了补满 200 条继续翻页或扫描 ID。
STOP_WHEN_PAGE_LESS_THAN_SIZE = True

# 严格按 JS 逻辑：招标计划也从列表 annClassifications=["4"] 获取。
# 旧版 ID 扫描只作为手动兜底，默认关闭，避免招标计划总量不足 200 条时继续扫描导致卡住。
ENABLE_BID_PLAN_SCAN_FALLBACK = False
API_BID_PLAN_SCAN_MAX = 200
API_BID_PLAN_MISS_LIMIT = 10    # 连续空值则停止

# 公告列表请求模板
ANNOUNCE_PAYLOAD_TEMPLATE = {
    "pageNum": 1,
    "pageSize": DETAIL_PAGE_SIZE,
    "annAttribute": "",
    "annAttributeList": [],
    "annNum": "",
    "annTitle": "",
    "bidName": "",
    "createEndTime": "",
    "createStartTime": "",
    "industryList": [],
    "navId": "1597613483694370816",
    "openWay": "",
    "purDiyCodes": [],
    "purName": "",
    "regionList": [],
    "type": "",
    "typeCode": "",
}

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    字段定义（按前端 JS 模板对齐）                            ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# 招标计划
ZBJH_FIELDS = [
    "项目性质", "招标方式", "项目名称", "项目类型", "项目总投资",
    "招标内容", "招标人名称", "行政监督部门", "建设地点", "建设内容及规模",
    "招标公告（资格预审公告）预计发布时间", "发布日期", "发布网站", "详情页面",
]

# 资格预审公告
ZBYS_FIELDS = [
    "项目性质", "项目名称", "所属行业", "组织形式", "开标时间",
    "项目编号/招标编号", "项目类型/行业分类", "项目总投资/估算金额",
    "招标金额", "资金来源", "项目地点", "招标人/采购人名称",
    "招标代理机构(名称)", "项目概况与招标范围",
    "申请人资格要求/投标人资格要求", "预审文件获取时间", "获取方式",
    "递交截止时间", "递交方法", "开启时间", "开启方式", "开启地点",
    "评审办法", "投标保证金方式",
    "招标人地址", "招标人联系人", "招标人联系方式",
    "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
    "招标代理机构联系方式", "发布日期", "发布网站", "详情页面",
]

# 招标公告
ZBGG_FIELDS = [
    "项目性质", "项目名称", "所属行业", "组织形式", "开标时间",
    "项目编号/招标编号", "项目类型/行业分类", "项目总投资/估算金额",
    "招标金额", "资金来源", "项目地点", "招标人/采购人名称",
    "招标代理机构(名称)", "项目规模", "工期/服务期/供货日期",
    "质量要求", "招标内容与范围", "申请人资格要求/投标人资格要求",
    "招标文件获取时间", "获取方式", "递交截止时间", "递交方法",
    "开启时间", "开启方式", "开启地点", "评审办法", "投标保证金方式",
    "招标人地址", "招标人联系人", "招标人联系方式",
    "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
    "招标代理机构联系方式", "发布日期", "发布网站", "详情页面",
]

# 候选公示：JS 中 annClassification=2 统一进入 candidatenotice，
# 不再拆“定标候选人”。
HXR_FIELDS = [
    "项目性质", "项目名称", "所属行业", "组织形式", "开标时间",
    "公示时间", "招标编号/项目编号", "中标候选人名称", "中标候选人报价",
    "招标人/采购人", "招标人地址", "招标人联系人", "招标人联系方式",
    "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
    "招标代理机构联系方式", "发布日期", "发布网站", "详情页面",
]

# 中标结果公示：按 JS dealnoticenotice 模板字段
# 模板内容：bidCondition、中标人信息 bidAnnDealDOS、其他公示内容、监督部门、联系方式、附件。
ZBJG_FIELDS = [
    "项目性质", "项目名称", "所属行业", "组织形式", "招标方式",
    "招标编号/项目编号", "招标条件/说明", "中标人名称", "中标价格", "其他公示内容", "监督部门",
    "招标人/采购人", "招标人地址", "招标人联系人", "招标人联系方式",
    "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
    "招标代理机构联系方式", "发布日期", "发布网站", "详情页面",
]

# 更正/撤销中标结果：JS 仍使用 dealnoticenotice 模板，只是列表前缀 annNature 不同。
GZJG_FIELDS = [
    "公共类型", "项目性质", "项目名称", "所属行业", "组织形式", "招标方式",
    "招标编号/项目编号", "招标条件/说明", "中标人名称", "中标价格", "其他公示内容", "监督部门",
    "招标人/采购人", "招标人地址", "招标人联系人", "招标人联系方式",
    "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
    "招标代理机构联系方式", "发布日期", "发布网站", "详情页面",
]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    4 栏目配置（按 JS 分类）                                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

SECTION_DEFS = [
    {
        "key": "zbjh",
        "name": "招标计划",
        "api_type": "bidplan",
        "fields": ZBJH_FIELDS,
        "subtypes": [],
        "output_json": "zbjh_招标计划.json",
        "output_csv": "zbjh_招标计划.csv",
    },
    {
        "key": "zbgg_zys",
        "name": "招标资审公告",
        "api_type": "announcement",
        "fields": None,
        "subtypes": ["zbgg", "zbys"],
        "sub_map": {
            "zbgg": {"json": "zbgg_招标公告.json", "csv": "zbgg_招标公告.csv", "fields": ZBGG_FIELDS},
            "zbys": {"json": "zbys_资格预审公告.json", "csv": "zbys_资格预审公告.csv", "fields": ZBYS_FIELDS},
        },
        "field_map": {"zbgg": ZBGG_FIELDS, "zbys": ZBYS_FIELDS},
    },
    {
        "key": "hxr",
        "name": "候选公示",
        "api_type": "announcement",
        "fields": None,
        "subtypes": ["hxr"],
        "sub_map": {
            "hxr": {"json": "hxr_中标候选人公示.json", "csv": "hxr_中标候选人公示.csv", "fields": HXR_FIELDS},
        },
        "field_map": {"hxr": HXR_FIELDS},
    },
    {
        "key": "gs",
        "name": "结果公示",
        "api_type": "announcement",
        "fields": None,
        "subtypes": ["zbjg", "gzjg"],
        "sub_map": {
            "zbjg": {"json": "zbjg_中标结果公示.json", "csv": "zbjg_中标结果公示.csv", "fields": ZBJG_FIELDS},
            "gzjg": {"json": "gzjg_更正撤销中标结果公示.json", "csv": "gzjg_更正撤销中标结果公示.csv", "fields": GZJG_FIELDS},
        },
        "field_map": {"zbjg": ZBJG_FIELDS, "gzjg": GZJG_FIELDS},
    },
]
