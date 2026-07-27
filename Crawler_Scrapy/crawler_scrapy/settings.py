"""Scrapy 全局公共配置。

本文件只放“所有网站都可以复用”的框架级设置：
- 请求调度与并发
- 下载延迟和自动限速
- 超时与失败重试
- Cookie、重定向、压缩
- 公共 User-Agent 和基础请求头
- HTTP 缓存开关
- 日志、统计
- 公告字段、HTML 快照、CSV/JSON 输出 Pipeline

以下内容不要放在本文件：
- 某个网站的 API 地址
- 某个网站的 Referer、Origin、Token、CSRF
- 某个网站的栏目参数、分页参数
- 请求签名、响应解密
- 代理平台密钥

网站专用配置应放在具体 Spider、网站配置模块或环境变量中。
"""

from pathlib import Path


# =============================================================================
# 1. Scrapy 项目基础配置
# =============================================================================

BOT_NAME = "crawler_scrapy"

SPIDER_MODULES = ["crawler_scrapy.spiders"]
NEWSPIDER_MODULE = "crawler_scrapy.spiders"

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Scrapy 组件会使用该值标识客户端。
# 具体网站需要特殊 User-Agent 时，可以在 Spider.custom_settings
# 或具体 Request.headers 中覆盖。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)

# 默认遵守目标网站 robots.txt。
# 不要为了“请求成功”而在全局直接关闭；确有授权且确认规则后，
# 可在对应 Spider 的 custom_settings 中单独调整。
ROBOTSTXT_OBEY = True

# 关闭生产环境不需要的 Telnet 调试入口。
TELNETCONSOLE_ENABLED = False


# =============================================================================
# 2. 并发与访问频率
# =============================================================================

# 整个 Scrapy 进程允许同时处理的最大请求数。
# 这是所有域名合计的上限，不等于单个网站的并发数。
CONCURRENT_REQUESTS = 8

# 同一个域名允许同时下载的请求数。
# 多网站框架使用保守默认值，JSON API 等稳定网站可在 Spider 中覆盖。
CONCURRENT_REQUESTS_PER_DOMAIN = 2

# 不启用“按 IP 限制并发”，因此仍按域名执行并发和延迟控制。
CONCURRENT_REQUESTS_PER_IP = 0

# 同一下载槽两次请求之间的基础间隔，单位为秒。
DOWNLOAD_DELAY = 1.0

# Scrapy 会在 DOWNLOAD_DELAY 的基础上随机化实际等待时间，
# 避免所有请求以完全固定的时间间隔发出。
RANDOMIZE_DOWNLOAD_DELAY = True


# =============================================================================
# 3. AutoThrottle 自动限速
# =============================================================================

# 让 Scrapy 根据目标网站响应延迟动态调整每个下载槽的请求间隔。
AUTOTHROTTLE_ENABLED = True

# 第一次请求前后的初始延迟。
AUTOTHROTTLE_START_DELAY = 1.0

# 动态调整时允许达到的最大延迟。
AUTOTHROTTLE_MAX_DELAY = 15.0

# 每个远程网站期望保持的平均并发请求数量。
# 公共默认值保持为 1，避免未测试网站一开始就高频访问。
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

# 调试限速时可以临时改为 True；正式运行不需要逐响应打印。
AUTOTHROTTLE_DEBUG = False


# =============================================================================
# 4. 下载超时与 Scrapy 内置重试
# =============================================================================

# 单个请求允许等待的最长时间，单位为秒。
DOWNLOAD_TIMEOUT = 45

# 启用 Scrapy 自带 RetryMiddleware。
RETRY_ENABLED = True

# 重试次数不包含第一次请求。
# RETRY_TIMES=2 表示最多：第一次请求 + 2 次重试。
RETRY_TIMES = 2

# 仅对通常具有临时性的状态码重试。
# 不在这里加入 401 和 403：
# - 401 通常是 Token、Cookie 或登录失效；
# - 403 可能是权限、签名、访问规则或 IP 限制；
# 盲目重试通常无法解决问题，后续应由具体网站判断。
RETRY_HTTP_CODES = [
    408,  # Request Timeout
    429,  # Too Many Requests
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
]

# 重试请求降低优先级，使正常请求先完成，避免失败请求持续抢占队列。
RETRY_PRIORITY_ADJUST = -1


# =============================================================================
# 5. Cookie、重定向与响应压缩
# =============================================================================

# 使用 Scrapy 自带 CookiesMiddleware 自动维护同一 Spider 的会话 Cookie。
COOKIES_ENABLED = True

# 调试登录、CSRF、Cookie 传递问题时可临时设为 True。
COOKIES_DEBUG = False

# 启用 HTTP 301/302/303/307/308 重定向处理。
REDIRECT_ENABLED = True
REDIRECT_MAX_TIMES = 10

# 支持 HTML 中的 meta refresh 跳转。
METAREFRESH_ENABLED = True
METAREFRESH_MAXDELAY = 100

# 自动处理 gzip、deflate、br 等压缩响应。
COMPRESSION_ENABLED = True


# =============================================================================
# 6. 公共请求头
# =============================================================================

# 只配置所有网站都可以使用的基础请求头。
# Content-Type、Referer、Origin、Authorization、authentication、
# X-CSRF-TOKEN 等网站专用请求头，必须由具体 Spider/Request 设置。
DEFAULT_REQUEST_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


# =============================================================================
# 7. DNS 与下载器基础行为
# =============================================================================

# 启用 Scrapy DNS 缓存，减少同一域名反复解析。
DNSCACHE_ENABLED = True
DNSCACHE_SIZE = 10000
DNS_TIMEOUT = 60

# 不允许非 2xx 响应自动进入普通回调。
# 具体 Spider 如需处理 401、403、404，可在 Request.meta 中使用：
# meta={"handle_httpstatus_list": [401, 403, 404]}
HTTPERROR_ALLOW_ALL = False


# =============================================================================
# 8. HTTP 缓存
# =============================================================================

# 正式采集默认关闭，避免读取旧公告。
# 调试选择器和字段解析时，可以通过命令临时启用：
# scrapy crawl spider_name -s HTTPCACHE_ENABLED=True
HTTPCACHE_ENABLED = False

# 缓存启用后的有效时间；0 表示不过期。
HTTPCACHE_EXPIRATION_SECS = 3600

HTTPCACHE_DIR = "httpcache"

# 不缓存错误和频率限制响应。
HTTPCACHE_IGNORE_HTTP_CODES = [
    401,
    403,
    408,
    429,
    500,
    502,
    503,
    504,
]

HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"


# =============================================================================
# 9. Spider Middleware 与 Downloader Middleware
# =============================================================================

# 当前不配置自定义 Middleware。
#
# Scrapy 的内置组件会通过 DOWNLOADER_MIDDLEWARES_BASE 自动启用，
# 包括但不限于：
# - RobotsTxtMiddleware
# - HttpAuthMiddleware
# - DownloadTimeoutMiddleware
# - DefaultHeadersMiddleware
# - UserAgentMiddleware
# - RetryMiddleware
# - MetaRefreshMiddleware
# - HttpCompressionMiddleware
# - RedirectMiddleware
# - CookiesMiddleware
# - HttpProxyMiddleware
# - DownloaderStats
#
# 不要在这里重复注册这些内置组件。
#
# 后续接入天启 ProxyPool 时，只增加一个很薄的自定义 ProxyMiddleware，
# 用于从 ProxyPool 取出代理并写入 request.meta["proxy"]。
SPIDER_MIDDLEWARES = {}

DOWNLOADER_MIDDLEWARES = {}


# =============================================================================
# 9.1 天启代理池默认配置（具体 Spider 显式注册 Middleware 后生效）
# =============================================================================

# 公共默认不启用，避免未配置代理的其他 Spider 被意外影响。
TIANQI_PROXY_ENABLED = False
TIANQI_PROXY_REQUIRED = True
TIANQI_PROXY_API_URL = "http://api.tianqiip.com/getip"
TIANQI_PROXY_NUM = 10
TIANQI_PROXY_LIFETIME = 3
TIANQI_PROXY_PORT_TYPE = 2
TIANQI_PROXY_MIN_SIZE = 3
TIANQI_PROXY_MAX_FAILURES = 1
TIANQI_PROXY_API_CALL_LIMIT = 5
TIANQI_PROXY_API_TIMEOUT = 15.0
TIANQI_PROXY_EXPIRY_SAFETY_SECONDS = 20
TIANQI_PROXY_RETRY_TIMES = 3
TIANQI_PROXY_FAILURE_HTTP_CODES = [403, 407, 429, *range(430, 457)]

# TIANQI_SECRET、TIANQI_SIGN 不写入源码，由同名环境变量注入。

# 固定认证代理。只把可公开轮换的 endpoint 放在配置中，账号密码必须由环境变量
# HUAXIN_PROXY_USERNAME、HUAXIN_PROXY_PASSWORD 注入，禁止写入源码和命令行。
STATIC_PROXY_ENABLED = False
STATIC_PROXY_REQUIRED = True
STATIC_PROXY_ENDPOINT = "http://210.51.27.8:10000"
STATIC_PROXY_ENDPOINT_ENV = "HUAXIN_PROXY_ENDPOINT"
STATIC_PROXY_USERNAME_ENV = "HUAXIN_PROXY_USERNAME"
STATIC_PROXY_PASSWORD_ENV = "HUAXIN_PROXY_PASSWORD"
STATIC_PROXY_AUTH_REQUIRED = True
STATIC_PROXY_RETRY_TIMES = 1

# 出口模式由具体 Spider 读取。华新、玖邦默认使用服务器固定公网出口，并由
# DirectAccessGuard、低并发、随机延迟和 AutoThrottle 共同保护。
CRAWLER_OUTBOUND_MODE = "direct"

# 服务器固定公网出口采用更保守的默认值。
DIRECT_CONCURRENT_REQUESTS = 1
DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN = 1
DIRECT_DOWNLOAD_DELAY = 5.0
DIRECT_AUTOTHROTTLE_START_DELAY = 5.0
DIRECT_AUTOTHROTTLE_MAX_DELAY = 120.0
DIRECT_AUTOTHROTTLE_TARGET_CONCURRENCY = 0.25
DIRECT_RETRY_TIMES = 0
DIRECT_MAX_RESPONSES_PER_RUN = 200

# 403/429 第一次即退避；同一域名连续 2 次或本次任务累计 4 次时主动停爬。
DIRECT_GUARD_HTTP_CODES = [403, 429]
DIRECT_GUARD_CONSECUTIVE_LIMIT = 1
DIRECT_GUARD_TOTAL_LIMIT = 2
DIRECT_GUARD_BASE_BACKOFF = 120.0
DIRECT_GUARD_MAX_BACKOFF = 900.0


# =============================================================================
# 10. Item Pipeline：保留当前已实现的公共输出能力
# =============================================================================

ITEM_PIPELINES = {
    # 最先过滤已经保存过的“公告身份 + 内容指纹”，避免重复快照、AI和导出。
    "crawler_scrapy.pipelines.NoticeDedupPipeline": 40,

    # 先保存详情页 HTML 原文快照，并写入快照路径和 SHA256。
    "crawler_scrapy.pipelines.HtmlSnapshotPipeline": 50,

    # 可选附件下载：只落盘并回写路径/哈希/大小/状态，不执行 OCR 或 AI。
    "crawler_scrapy.pipelines.NoticeFilesPipeline": 75,

    # 再规范公告类型、补齐该类型全部字段并统计缺失字段。
    "crawler_scrapy.pipelines.NoticeSchemaPipeline": 100,

    # 默认关闭；启用后只用 AI 补充规则解析仍为空的业务字段。
    "crawler_scrapy.pipelines.AiHtmlExtractionPipeline": 200,

    # 最后按网站、公告类型同时输出 CSV 与 JSON。
    "crawler_scrapy.pipelines.NoticeMultiFormatPipeline": 300,
}


# =============================================================================
# 11. 多网站输出与 HTML 快照
# =============================================================================

# 固定输出结构：
# output/<网站代码>/csv/
# output/<网站代码>/json/
# output/<网站代码>/snapshots/<公告类型>/
NOTICE_OUTPUT_ROOT = str(PROJECT_ROOT / "output")
# Can point to a stable directory while each API task uses an isolated output root.
NOTICE_DEDUP_ROOT = NOTICE_OUTPUT_ROOT

NOTICE_SNAPSHOT_ENABLED = True

# 框架搭建和网站迁移期间保持 False：
# 某个 Spider 尚未传 raw_html 时，只警告，不丢弃结构化数据。
# 等所有正式 Spider 都实现快照后，可以改为 True。
NOTICE_SNAPSHOT_REQUIRED = False

# CSV/JSON 中保留平台名称、平台代码、公告 ID、公告标题等公共元数据。
NOTICE_EXPORT_INCLUDE_META = True

# 输出“缺失字段”，便于检查字段提取质量。
NOTICE_EXPORT_DIAGNOSTICS = True

# 多数网站只包含八类公告中的一部分，默认只创建实际抓到的类型文件。
NOTICE_EXPORT_EMPTY_FILES = False

# 启用跨运行去重。索引保存在 output/<网站代码>/state/notice_versions.json。
# 不使用数据库；同一公告内容变化时追加新版本，旧版本不覆盖。
NOTICE_DEDUP_ENABLED = True

# 严格去重模式：已导出过的源站公告 ID 不再请求详情。默认关闭，以便普通生产任务
# 在列表字段发生变化时保存公告新版本；华新手工增量脚本会显式开启。
NOTICE_DEDUP_SKIP_KNOWN_IDENTITIES = False

# 附件下载默认关闭，由已经验证过下载接口的 Spider 单独开启。文件保存在
# FILES_STORE/<网站代码>/attachments/，storage_path 记录其相对路径。
NOTICE_ATTACHMENT_DOWNLOAD_ENABLED = False
FILES_STORE = NOTICE_OUTPUT_ROOT
# 源站 fileId 作为稳定文件身份；十年内复用已落盘文件，避免重复下载和覆盖。
FILES_EXPIRES = 3650


# =============================================================================
# 11.1 公告 HTML AI 缺失字段补全（默认关闭）
# =============================================================================

# 开启后执行顺序为：网站规则解析 -> Schema 类型转换 -> AI 补空 -> 再次类型转换。
# 未显式开启时不创建客户端，也不会调用任何 AI API。
NOTICE_AI_ENABLED = False

# API Key 默认从该环境变量读取，也可由部署系统注入 NOTICE_AI_API_KEY 设置。
# 密钥不要写入源码或提交到仓库。
NOTICE_AI_API_KEY_ENV = "DMX_API_KEY"
NOTICE_AI_BASE_URL = "https://vip.dmxapi.com/v1"
NOTICE_AI_MODEL = "glm-4.6-thinking"
NOTICE_AI_TIMEOUT = 90.0

# 所有 AI 请求起始时间至少间隔 1 秒；等待和调用在线程池执行，不阻塞下载器。
NOTICE_AI_MIN_INTERVAL = 1.0
NOTICE_AI_RETRY_TIMES = 2
NOTICE_AI_RETRY_BASE_DELAY = 3.0
NOTICE_AI_RETRY_MAX_DELAY = 30.0

# 超长正文同时保留开头和结尾，避免丢失文末联系方式/监督部门。
NOTICE_AI_MAX_INPUT_CHARS = 16000
NOTICE_AI_MAX_OUTPUT_TOKENS = 4000

# 每个 Scrapy 进程的实际 API 请求上限（包含失败重试）；0 表示不限制。
NOTICE_AI_MAX_CALLS = 100

# 部分 OpenAI 兼容服务不支持 response_format，默认仅通过 Prompt 约束 JSON。
NOTICE_AI_JSON_MODE = False

# 默认只补必填字段；True 时连 Schema 中标为可选的业务字段也会尝试补全。
NOTICE_AI_INCLUDE_OPTIONAL_FIELDS = False

# 默认 AI 失败仅记录日志和统计，不丢弃规则已经成功提取的数据。
NOTICE_AI_FAIL_ON_ERROR = False


# =============================================================================
# 12. 日志与统计
# =============================================================================

LOG_ENABLED = True
LOG_LEVEL = "INFO"
LOG_ENCODING = "utf-8"

LOG_FORMAT = (
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
LOG_DATEFORMAT = "%Y-%m-%d %H:%M:%S"

# 不把 print() 自动重定向为日志，项目代码统一使用 spider.logger。
LOG_STDOUT = False

# 每隔 60 秒打印一次当前抓取统计。
LOGSTATS_INTERVAL = 60.0

# Spider 关闭时输出完整 Stats。
STATS_DUMP = True


# =============================================================================
# 13. 导出编码
# =============================================================================

# Scrapy 自带 Feed Export 的编码。
# 当前 CSV/JSON 主要由自定义 Pipeline 输出，此设置仍保留给临时 -O/-o 导出。
FEED_EXPORT_ENCODING = "utf-8"


# =============================================================================
# 14. 暂不全局启用的内置能力
# =============================================================================

# JOBDIR 不应在全局固定：
# 每个 JOBDIR 只能对应一个 Spider 的一次任务。
# 需要断点续爬时，在命令中传入：
# scrapy crawl huaxin -s JOBDIR=jobstate/huaxin_full_001
#
# CLOSESPIDER_* 不设全局阈值：
# 不同网站的数据量和错误特征不同，应由 Spider.custom_settings 单独设置。
#
# FilesPipeline 暂未启用：
# 当前先在“附件”字段中保存附件元数据。
# 等真实网站能获取稳定下载 URL 后，再接入 Scrapy 自带 FilesPipeline。
