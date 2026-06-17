"""
=============================================================================
全国公共资源交易平台（山西省）- 爬虫配置文件
=============================================================================
目标网站: https://prec.sxzwfw.gov.cn
栏目路径: 交易信息 > 工程建设 子栏目

4个子栏目:
  zbjh  - 招标计划
  gczb  - 招标/资审公告
  gchxr - 中标候选人公示
  gcgs  - 中标结果公示

说明:
  - 该站点无 robots.txt, 无需登录即可访问
  - 页面为服务端渲染(SSR) HTML, 非AJAX动态加载
  - 列表按发布日期降序排列, 每页10条
=============================================================================
"""

# ======================== 目标站点配置 ========================

BASE_URL = "https://prec.sxzwfw.gov.cn"

# ---- 4个栏目的URL配置 ----
# 每个栏目: (中文名称, 路径前缀, 列表详情正则片段, 输出JSON文件名)
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

# 辅助函数: 根据path_prefix生成URL
def _list_index_url(path_prefix):
    return f"{BASE_URL}/{path_prefix}/index.jhtml"

def _list_page_url(path_prefix, page):
    return f"{BASE_URL}/{path_prefix}/index_{page}.jhtml"

def _detail_url(path_prefix, detail_id):
    return f"{BASE_URL}/{path_prefix}/{detail_id}.jhtml"

def _detail_pattern(path_prefix):
    return rf"/{path_prefix}/(\d+)\.jhtml"

# ======================== 爬取范围与日期过滤 ========================

DAYS_LOOKBACK = 1  # 仅爬取最近N天的数据
MAX_LIST_PAGES = 30  # 最多翻页数(安全上限)

# ======================== 请求控制 ========================

REQUEST_DELAY_MIN = 5.0   # 详情页之间最小延迟(秒)
REQUEST_DELAY_MAX = 10.0  # 详情页之间最大延迟(秒)
PAGE_TRANSITION_DELAY = (8.0, 15.0)  # 翻页延迟范围(秒)
SECTION_COOLDOWN = (60.0, 120.0)  # 栏目间冷却间隔(秒)
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 3
PROXY_MAX_FAILURES = 3

# ======================== 代理配置 ========================

PROXY_FILE = "proxies.txt"
PROXY_POOL_MIN_SIZE = 5
PROXY_POOL_TARGET_SIZE = 20
PROXY_VALIDATE_TIMEOUT = 10
PROXY_VALIDATE_URL = BASE_URL

FREE_PROXY_APIS = [
    # 89ip 免费代理 - 纯文本 ip:port 格式
    "https://www.89ip.cn/tqdl.html?api=1&num=60",
    # ip3366 云代理 - HTML表格格式
    "http://proxy.ip3366.net/free/?action=china&page=1",
]

# ======================== User-Agent 轮换池 ========================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
]

# ======================== 请求头模板 ========================

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ======================== 日志配置 ========================

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
