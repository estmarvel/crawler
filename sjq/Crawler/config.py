"""
=============================================================================
全国公共资源交易平台（山西省）- 招标计划公告爬虫 配置文件
=============================================================================
目标网站: https://prec.sxzwfw.gov.cn
栏目路径: 交易信息 > 招标计划
列表页URL: /jyxxzbjh/index.jhtml (第1页)
分页URL:   /jyxxzbjh/index_{N}.jhtml (第N页, N>=2)
详情页URL: /jyxxzbjh/{id}.jhtml

说明:
  - 该站点无 robots.txt, 无需登录即可访问
  - 页面为服务端渲染(SSR) HTML, 非AJAX动态加载
  - 列表按发布日期降序排列, 每页10条, 共约8922条/893页
  1111
=============================================================================
"""

# ======================== 目标站点配置 ========================

BASE_URL = "https://prec.sxzwfw.gov.cn"
LIST_INDEX_URL = f"{BASE_URL}/jyxxzbjh/index.jhtml"  # 第1页
LIST_PAGE_URL_TEMPLATE = f"{BASE_URL}/jyxxzbjh/index_{{page}}.jhtml"  # 第N页 (N>=2)
DETAIL_URL_TEMPLATE = f"{BASE_URL}/jyxxzbjh/{{detail_id}}.jhtml"  # 详情页
DETAIL_URL_PATTERN = r"/jyxxzbjh/(\d+)\.jhtml"  # 正则匹配详情链接

# ======================== 爬取范围与日期过滤 ========================

DAYS_LOOKBACK = 7  # 仅爬取最近N天的数据
MAX_LIST_PAGES = 100  # 最多翻页数(安全上限, 防止无限循环)

# ======================== 请求控制 ========================

# 请求延迟(秒): 在 [MIN, MAX] 区间内随机取值, 模拟人类浏览行为
REQUEST_DELAY_MIN = 2.0
REQUEST_DELAY_MAX = 5.0

# 列表页额外延迟: 翻页时额外等待
PAGE_TRANSITION_DELAY = (3.0, 6.0)

# 请求超时(秒)
REQUEST_TIMEOUT = 30

# 最大重试次数
MAX_RETRIES = 3

# 重试退避基数(秒): 第1次重试等 base, 第2次等 base*2, 第3次等 base*4
RETRY_BACKOFF_BASE = 3

# 代理切换阈值: 同一代理连续失败N次后自动剔除
PROXY_MAX_FAILURES = 3

# ======================== 代理配置 ========================

# 代理池配置文件路径(每行一个代理: ip:port 或 ip:port:user:pass)
PROXY_FILE = "proxies.txt"

# 代理池最小可用数量
PROXY_POOL_MIN_SIZE = 5

# 代理池目标数量(从免费源补充到此数量)
PROXY_POOL_TARGET_SIZE = 20

# 代理验证超时(秒)
PROXY_VALIDATE_TIMEOUT = 10

# 代理验证目标URL(用目标站验证代理可达性)
PROXY_VALIDATE_URL = BASE_URL

# 免费代理API源(HTTP/HTTPS)
FREE_PROXY_APIS = [
    # ProxyScrape - 提供HTTP代理
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    # ProxyScrape - 提供HTTPS代理
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=https&timeout=10000&country=all&ssl=all&anonymity=all",
]

# ======================== User-Agent 轮换池 ========================

USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
    # Edge on Windows
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

# ======================== 数据输出配置 ========================

OUTPUT_JSON_FILE = "tender_plans_output.json"  # 默认输出JSON文件名
OUTPUT_CSV_FILE = "tender_plans_output.csv"    # 可选CSV输出文件名

# ======================== 日志配置 ========================

LOG_LEVEL = "INFO"  # DEBUG | INFO | WARNING | ERROR
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
