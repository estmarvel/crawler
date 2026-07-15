import os
from dotenv import load_dotenv

load_dotenv()

BOT_NAME = "shanxi_crawler"
SPIDER_MODULES = ["shanxi_crawler.spiders"]
NEWSPIDER_MODULE = "shanxi_crawler.spiders"
ROBOTSTXT_OBEY = False

CONCURRENT_REQUESTS = int(os.getenv("CRAWLER_CONCURRENT_REQUESTS", "32"))
CONCURRENT_REQUESTS_PER_DOMAIN = int(os.getenv("CRAWLER_CONCURRENT_REQUESTS_PER_DOMAIN", "16"))
DOWNLOAD_TIMEOUT = int(os.getenv("CRAWLER_DOWNLOAD_TIMEOUT", "20"))
DOWNLOAD_DELAY = float(os.getenv("CRAWLER_DOWNLOAD_DELAY", "0"))
RANDOMIZE_DOWNLOAD_DELAY = True
COOKIES_ENABLED = True
REACTOR_THREADPOOL_MAXSIZE = int(os.getenv("REACTOR_THREADPOOL_MAXSIZE", "32"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

DOWNLOADER_MIDDLEWARES = {
    "shanxi_crawler.middlewares.RandomUserAgentMiddleware": 400,
    "shanxi_crawler.middlewares.ProxyMiddleware": 543,
    "scrapy.downloadermiddlewares.retry.RetryMiddleware": 550,
}

ITEM_PIPELINES = {
    "shanxi_crawler.pipelines.ShanxiCsvUpsertPipeline": 300,
}

DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {"headless": True}

CRAWLER_OUTPUT_DIR = os.getenv("CRAWLER_OUTPUT_DIR", "output")
LOG_DIR = os.getenv("LOG_DIR", "logs")

USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"
PROXY_DIRECT_FALLBACK = os.getenv("PROXY_DIRECT_FALLBACK", "true").lower() == "true"
PROXY_PROVIDER = os.getenv("PROXY_PROVIDER", "legacy")
PROXY_API_URL = os.getenv("PROXY_API_URL", "")

ENABLE_AI = os.getenv("ENABLE_AI", "false").lower() == "true"
ENABLE_PDF_OCR = os.getenv("ENABLE_PDF_OCR", "true").lower() == "true"

RETRY_ENABLED = True
RETRY_TIMES = int(os.getenv("RETRY_TIMES", "3"))
RETRY_HTTP_CODES = [403, 407, 408, 429, 500, 502, 503, 504]
FEED_EXPORT_ENCODING = "utf-8-sig"
