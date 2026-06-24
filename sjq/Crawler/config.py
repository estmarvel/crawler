"""
=============================================================================
全国公共资源交易平台（山西省）- 爬虫配置文件
=============================================================================
目标网站: https://prec.sxzwfw.gov.cn
栏目路径: 交易信息 > 工程建设

4个子栏目:
  zbjh  - 招标计划
  gczb  - 招标/资审公告
  gchxr - 中标候选人公示
  gcgs  - 中标结果公示
=============================================================================
"""

# ======================== 目标站点配置 ========================

BASE_URL = "https://prec.sxzwfw.gov.cn"

SECTION_DEFS = [
    {
        "key": "zbjh",
        "name": "招标计划",
        "path_prefix": "jyxxzbjh",
        "output_json": "zbjh_招标计划.json",
        "output_csv": "zbjh_招标计划.csv",
    },
    {
        "key": "gczb",
        "name": "招标资审公告",
        "path_prefix": "jyxxgczb",
        "output_json": "gczb_招标资审公告.json",
        "output_csv": "gczb_招标资审公告.csv",
    },
    {
        "key": "gchxr",
        "name": "中标候选人公示",
        "path_prefix": "jyxxgchxr",
        "output_json": "gchxr_中标候选人公示.json",
        "output_csv": "gchxr_中标候选人公示.csv",
    },
    {
        "key": "gcgs",
        "name": "中标结果公示",
        "path_prefix": "jyxxgcgs",
        "output_json": "gcgs_中标结果公示.json",
        "output_csv": "gcgs_中标结果公示.csv",
    },
]


def _list_index_url(path_prefix):
    return f"{BASE_URL}/{path_prefix}/index.jhtml"


def _list_page_url(path_prefix, page):
    return f"{BASE_URL}/{path_prefix}/index_{page}.jhtml"


def _detail_url(path_prefix, detail_id):
    return f"{BASE_URL}/{path_prefix}/{detail_id}.jhtml"


def _detail_pattern(path_prefix):
    return rf"/{path_prefix}/(\d+)\.jhtml"


# ======================== 爬取范围 ========================

DAYS_LOOKBACK = 2   # 爬取最近 N 天数据
MAX_LIST_PAGES = 30

# ======================== 请求控制 ========================

REQUEST_DELAY_MIN = 0.5
REQUEST_DELAY_MAX = 1.5
PAGE_TRANSITION_DELAY = (1.0, 2.0)
SECTION_COOLDOWN = (1.0, 2.0)
REQUEST_TIMEOUT = 30
MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 2

# ======================== 并发配置 ========================

CONCURRENT_WORKERS = 10       # ThreadPoolExecutor 线程数（= 一次 API 提取的 IP 数）
DETAIL_BATCH_SIZE = 10        # 每批提交的任务数

# ======================== 代理配置 ========================

PROXY_FILE = "proxies.txt"
PROXY_POOL_MIN_SIZE = 3
PROXY_POOL_TARGET_SIZE = 10
PROXY_VALIDATE_TIMEOUT = 10
PROXY_VALIDATE_URL = BASE_URL
PROXY_MAX_FAILURES = 1        # 一次失败立即剔除（IP 生命周期短）

# 天启代理 API 调用上限（2 次 = 20 个 IP × 3 分钟窗口）
TIANQIIP_MAX_API_CALLS = 2

TIANQIIP_API_URL = "http://api.tianqiip.com/getip"
TIANQIIP_SECRET = "aliejy64ogh6c1kx"
TIANQIIP_SIGN = "e69907c179da76044ed221bdb095e0f5"

TIANQIIP_PARAMS = {
    "secret": TIANQIIP_SECRET,
    "sign": TIANQIIP_SIGN,
    "time": 3,       # IP 有效期（分钟）：3/5/10/15
    "num": 10,       # 每次提取数量
    "type": "json",
    "port": 2,       # 1=HTTP, 2=HTTPS, 3=SOCKS5
    "mr": 2,         # 1=去重, 2=不去重
}

# ======================== UA 轮换池 ========================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ======================== 日志 ========================

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
