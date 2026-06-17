BASE_URL = "https://prec.sxzwfw.gov.cn"

CATEGORIES = {
    "工程建设": "/jyxxgc/index_{page}.jhtml",
    "政府采购": "/jyxxzc/index_{page}.jhtml",
    "土地矿权": "/jyxxtd/index_{page}.jhtml",
    "国有产权": "/jyxxcq/index_{page}.jhtml",
}

MAX_PAGES        = 5
REQUEST_DELAY    = 1.5
REQUEST_TIMEOUT  = 15
MAX_RETRIES      = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": BASE_URL,
}

DB_PATH  = "jinchan.db"
LOG_FILE = "jinchan.log"

SCHEDULE_HOUR   = 6
SCHEDULE_MINUTE = 0
