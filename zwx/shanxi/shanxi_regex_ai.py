"""
山西省公共资源交易平台爬虫 v4.3
逻辑：
  - 招标计划：直接插入，不搜历史
  - 其他所有公告（招标公告/变更/候选人/结果等）：
      无论项目是否已存在，都先搜索该项目所有历史公告，
      把招标计划、招标公告、变更、候选人、结果全部合并，
      再 upsert 到 CSV，保证每行数据完整。
"""

import os, re, json, time, random, base64, hashlib
from datetime import datetime
from urllib.parse import urljoin
import pandas as pd
import requests
from bs4 import BeautifulSoup
from proxy_pool import ProxyPool, ProxyPoolEmptyError
from requests.exceptions import RequestException, ProxyError, ConnectTimeout, ReadTimeout, SSLError

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# ==================== 配置 ====================
BASE_URL = "https://prec.sxzwfw.gov.cn"
LIST_URL = "https://prec.sxzwfw.gov.cn/queryContent-jyxx.jspx"

CHANNEL = {
    "招标计划": 198,
    "招标公告": 12,
    "变更公告": 13,
    "中标候选人": 14,
    "中标结果": 15,
}

OUTPUT_DIR = "/home/intsig/zwx/shanxi/output"
LOG_DIR    = "/home/intsig/zwx/shanxi/log"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR,    exist_ok=True)

TODAY_STR      = datetime.now().strftime("%Y%m%d")
CSV_PATH       = os.path.join(OUTPUT_DIR, "山西.csv")
CHECK_LOG_PATH = os.path.join(LOG_DIR, f"{TODAY_STR}_check.log")
AI_REPORT_PATH = os.path.join(LOG_DIR, f"{TODAY_STR}_ai_report.log")

CRAWL_DAYS    = 1
DEFAULT_VALUE = "无"
SITE_NAME     = "山西省公共资源交易平台"
SESSION       = requests.Session()

USE_PROXY                       = True
DIRECT_FALLBACK_WHEN_PROXY_EMPTY = False
VERIFY_SSL                      = True

PROXY_POOL             = ProxyPool(num=30, time=5, port=2, min_size=8, max_fail_count=1)
PROXY_BAD_STATUS_CODES = {403, 407, 429, 430, 431, 432, 434, 435, 436, 451, 452,
                           453, 454, 455, 456, 500, 502, 503, 504}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer":      "https://prec.sxzwfw.gov.cn/jyxx/index.jhtml",
    "Content-Type": "application/x-www-form-urlencoded",
}

# ==================== AI 配置 ====================
AI_API_KEY             = os.getenv("DMX_API_KEY", "").strip()
AI_MODEL               = "glm-4.6-thinking"
AI_BASE_URL            = "https://vip.dmxapi.com/v1"
AI_MIN_INTERVAL_SECONDS = 2.0
AI_RETRY_BASE_SECONDS  = 3
AI_RETRY_MAX_SECONDS   = 30
_LAST_AI_CALL_TS       = 0.0

# ==================== PDF / OCR 配置 ====================
# 用 DMX 上的 qwen-vl-ocr 做 PDF 扫描件 OCR。可在 .env 中覆盖：
# export DMX_OCR_MODEL='qwen-vl-ocr'
OCR_MODEL = os.getenv("DMX_OCR_MODEL", "qwen-vl-ocr").strip() or "qwen-vl-ocr"
PDF_CACHE_DIR = "/home/intsig/zwx/shanxi/pdf_cache"
OCR_CACHE_DIR = "/home/intsig/zwx/shanxi/ocr_cache"
os.makedirs(PDF_CACHE_DIR, exist_ok=True)
os.makedirs(OCR_CACHE_DIR, exist_ok=True)

# PDF 页数过多会非常耗时和耗 token，先限制前 10 页；公告正文通常够用。
PDF_TEXT_MAX_PAGES = int(os.getenv("PDF_TEXT_MAX_PAGES", "20"))
OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "10"))
OCR_DPI = int(os.getenv("OCR_DPI", "160"))
PDF_TEXT_MIN_CHARS = int(os.getenv("PDF_TEXT_MIN_CHARS", "500"))


class _TokenCounter:
    def __init__(self):
        self.prompt_tokens     = 0
        self.completion_tokens = 0
        self.total_tokens      = 0
        self.call_count        = 0

    def add(self, usage):
        if not usage:
            return
        if hasattr(usage, "prompt_tokens"):
            self.prompt_tokens     += usage.prompt_tokens     or 0
            self.completion_tokens += usage.completion_tokens or 0
            self.total_tokens      += usage.total_tokens      or 0
        else:
            self.prompt_tokens     += usage.get("prompt_tokens",     0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            self.total_tokens      += usage.get("total_tokens",      0)
        self.call_count += 1

    def report(self):
        return (f"AI调用统计：共 {self.call_count} 次\n"
                f"  输入 tokens : {self.prompt_tokens:,}\n"
                f"  输出 tokens : {self.completion_tokens:,}\n"
                f"  合计 tokens : {self.total_tokens:,}")


TOKEN_COUNTER = _TokenCounter()


def get_ai_client():
    if OpenAI is None:
        raise RuntimeError("openai 包未安装，无法调用 AI")
    if not AI_API_KEY:
        raise RuntimeError("请先设置环境变量 DMX_API_KEY")
    return OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)


def sleep_before_ai_call():
    global _LAST_AI_CALL_TS
    now     = time.time()
    elapsed = now - _LAST_AI_CALL_TS
    if elapsed < AI_MIN_INTERVAL_SECONDS:
        time.sleep(AI_MIN_INTERVAL_SECONDS - elapsed)
    _LAST_AI_CALL_TS = time.time()


def ai_retry_sleep(attempt: int):
    time.sleep(min(AI_RETRY_BASE_SECONDS * (2 ** attempt), AI_RETRY_MAX_SECONDS))


# ==================== 字段表 ====================
COLUMNS = [
    "公告类型",
    "项目名称",
    "所属行业",
    "组织形式",
    "开标时间",
    "标书发售时间",
    "公告内容",
    "招标人",
    "招标人地址",
    "招标人联系人",
    "招标人联系方式",
    "招标代理机构",
    "招标代理机构地址",
    "招标代理机构联系人",
    "招标代理机构联系方式",
    "监督部门",
    "监督部门地址",
    "监督部门联系人",
    "监督部门联系方式",
    "依据文件",
    "依据文号",
    "发布日期",
    "发布网站",
    "公告历史",
]

NOTICE_REQUIRED_FIELDS = [
    "开标时间", "标书发售时间", "公告内容",
    "招标人", "招标人地址", "招标人联系人", "招标人联系方式",
    "招标代理机构", "招标代理机构地址", "招标代理机构联系人", "招标代理机构联系方式",
    "监督部门", "监督部门联系方式",
]

SKIP_KEYWORDS      = ["招标控制价变更", "招标控制价", "控制价变更", "控制价"]
TERMINATE_KEYWORDS = ["终止公告", "废标公告", "终止招标", "撤销公告", "招标公告撤销公告", "招标撤销公告"]
AMENDMENT_KEYWORDS = ["变更公告", "更正公告", "变更公示", "更正公示", "澄清公告", "澄清答疑", "延期公告", "二次延期公告", "变更通知"]
NOTICE_KEYWORDS    = ["招标公告", "二次招标公告", "二次重新招标公告", "三次招标公告", "三次重新招标公告", "预审公告", "资审公告", "资格预审公告"]
CANDIDATE_KEYWORDS = ["中标候选人公示"]
RESULT_KEYWORDS    = ["中标结果公示澄清", "中标结果公示"]

SKIPPED_UNKNOWN = []


# ==================== 通用工具 ====================

def sleep():
    time.sleep(random.uniform(2.0, 5.0))


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("&nbsp;", " ").replace("\u3000", " ")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def normalize_inline(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("&nbsp;", " ").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("：:，,；;。 ")


def clean_value(value: str) -> str:
    value = normalize_inline(str(value or ""))
    if not value:
        return DEFAULT_VALUE
    for kw in ["备案号", "晋ICP", "网站标识码", "晋公网安备", "山西省行政审批服务管理局"]:
        pos = value.find(kw)
        if pos != -1:
            value = value[:pos].strip()
    return value or DEFAULT_VALUE


def is_empty_value(v) -> bool:
    s = normalize_inline(str(v or ""))
    return s in ("", DEFAULT_VALUE, "暂无", "None", "nan")


def append_unique(existing: str, new_part: str) -> str:
    """用 '；' 分隔追加，自动去重"""
    new_part = normalize_inline(new_part)
    if not new_part:
        return existing if not is_empty_value(existing) else DEFAULT_VALUE
    existing = str(existing or DEFAULT_VALUE)
    if is_empty_value(existing):
        return new_part
    parts = [p.strip() for p in existing.split("；") if p.strip()]
    if new_part not in parts:
        parts.append(new_part)
    return "；".join(parts)



def format_publish_date(date_text: str) -> str:
    """
    发布日期统一格式：
      2026-06-29 17:25 -> 2026.6.29
      2026/06/29      -> 2026.6.29
      2026年06月29日  -> 2026.6.29
    """
    s = normalize_inline(str(date_text or ""))
    if not s:
        return ""
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if not m:
        return s
    return f"{m.group(1)}.{int(m.group(2))}.{int(m.group(3))}"


def make_publish_part(label: str, date_text: str) -> str:
    """
    生成“发布日期”字段中的一项：
      招标计划-2026.6.18
      招标公告-2026.6.20
      中标候选人公示-2026.6.22
    """
    label = normalize_inline(label)
    date = format_publish_date(date_text)
    if not label or not date:
        return ""
    return f"{label}-{date}"


def normalize_publish_cell(cell: str) -> str:
    """
    只保留最新格式：公告类型-日期。

    正确示例：
      招标计划-2026.6.18
      招标公告-2026.6.20
      中标候选人公示-2026.6.22

    不符合该格式的内容直接丢弃，避免出现纯日期、冒号格式或其他历史脏数据。
    """
    cell = normalize_inline(str(cell or ""))
    if is_empty_value(cell):
        return DEFAULT_VALUE

    valid_types = [
        "招标计划",
        "招标公告",
        "资格预审公告",
        "变更公告",
        "更正公告",
        "澄清公告",
        "延期公告",
        "中标候选人公示",
        "中标结果公示",
        "终止公告",
        "废标公告",
        "撤销公告",
    ]

    out = []
    for part in re.split(r"[；;]", cell):
        part = normalize_inline(part)
        if not part:
            continue

        # 只接受：公告类型-YYYY.M.D
        m = re.fullmatch(r"(.+?)-(\d{4}\.\d{1,2}\.\d{1,2})", part)
        if not m:
            continue

        label = normalize_inline(m.group(1))
        date = normalize_inline(m.group(2))
        if label not in valid_types:
            continue

        new_part = f"{label}-{date}"
        if new_part not in out:
            out.append(new_part)

    return "；".join(out) if out else DEFAULT_VALUE


# ==================== 网络请求 ====================

def get_proxy_for_request():
    if not USE_PROXY:
        return None
    try:
        return PROXY_POOL.get_proxy()
    except ProxyPoolEmptyError as e:
        print(f"  [代理池为空] {e}")
        if DIRECT_FALLBACK_WHEN_PROXY_EMPTY:
            print("  [代理] 允许直连降级，本次将使用服务器真实IP")
            return None
        raise


def should_mark_bad(resp=None, exc=None):
    if exc is not None:
        return isinstance(exc, (ProxyError, ConnectTimeout, ReadTimeout, SSLError, RequestException))
    if resp is None:
        return True
    return resp.status_code in PROXY_BAD_STATUS_CODES


def get(url, retries=3):
    for i in range(retries):
        proxy = None
        try:
            proxy = get_proxy_for_request()
            resp  = SESSION.get(url, headers=HEADERS, timeout=15, proxies=proxy, verify=VERIFY_SSL)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp
            print(f"  [GET异常{i+1}/{retries}] status={resp.status_code} | {url}")
            if USE_PROXY and proxy and should_mark_bad(resp=resp):
                PROXY_POOL.mark_bad(proxy)
        except ProxyPoolEmptyError:
            print(f"  [GET失败{i+1}/{retries}] 代理池为空，且禁止直连 | {url}")
            return None
        except Exception as e:
            print(f"  [GET失败{i+1}/{retries}] {type(e).__name__}: {e} | {url}")
            if USE_PROXY and proxy and should_mark_bad(exc=e):
                PROXY_POOL.mark_bad(proxy)
        time.sleep(random.uniform(1.5, 3.5))
    return None


def post_req(url, data, retries=3, headers=None):
    h = headers if headers else HEADERS
    for i in range(retries):
        proxy = None
        try:
            proxy = get_proxy_for_request()
            resp  = SESSION.post(url, headers=h, data=data, timeout=18, proxies=proxy, verify=VERIFY_SSL)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp
            print(f"  [POST异常{i+1}/{retries}] status={resp.status_code} | {url}")
            if USE_PROXY and proxy and should_mark_bad(resp=resp):
                PROXY_POOL.mark_bad(proxy)
        except ProxyPoolEmptyError:
            print(f"  [POST失败{i+1}/{retries}] 代理池为空，且禁止直连 | {url}")
            return None
        except Exception as e:
            print(f"  [POST失败{i+1}/{retries}] {type(e).__name__}: {e} | {url}")
            if USE_PROXY and proxy and should_mark_bad(exc=e):
                PROXY_POOL.mark_bad(proxy)
        time.sleep(random.uniform(2.0, 4.0))
    return None


# ==================== 标题分类 ====================

def classify_title(raw_title: str) -> str:
    for kw in SKIP_KEYWORDS:
        if kw in raw_title:
            return "skip"
    for kw in TERMINATE_KEYWORDS:
        if kw in raw_title:
            return "terminate"
    for kw in AMENDMENT_KEYWORDS:
        if kw in raw_title:
            return "amendment"
    for kw in RESULT_KEYWORDS:
        if kw in raw_title:
            return "result"
    for kw in CANDIDATE_KEYWORDS:
        if kw in raw_title:
            return "candidate"
    for kw in NOTICE_KEYWORDS:
        if kw in raw_title:
            return "notice"
    return "unknown"


def type_label(raw_title: str, forced_type: str = "") -> str:
    t = forced_type or classify_title(raw_title)
    if t == "plan":
        return "招标计划"
    if t == "notice":
        if "资格预审" in raw_title or "预审公告" in raw_title or "资审公告" in raw_title:
            return "资格预审公告"
        return "招标公告"
    if t == "amendment":
        if "更正" in raw_title:   return "更正公告"
        if "澄清" in raw_title:   return "澄清公告"
        if "延期" in raw_title:   return "延期公告"
        return "变更公告"
    if t == "candidate":  return "中标候选人公示"
    if t == "result":     return "中标结果公示"
    if t == "terminate":
        if "废标" in raw_title:   return "废标公告"
        if "撤销" in raw_title:   return "撤销公告"
        return "终止公告"
    if t == "skip":       return "控制价公告"
    return "未知公告"


def clean_title(title: str) -> str:
    suffixes = [
        "中标结果公示澄清", "中标结果公示", "中标候选人公示",
        "变更通知", "变更公告", "更正公告", "变更公示", "更正公示",
        "澄清公告", "澄清答疑", "延期公告", "二次延期公告",
        "招标计划", "招标控制价变更", "招标控制价", "控制价变更", "控制价",
        "二次重新招标公告", "三次重新招标公告", "二次招标公告", "三次招标公告",
        "资格预审公告", "预审公告", "资审公告", "招标公告",
        "撤销公告", "招标公告撤销公告", "招标撤销公告", "终止公告", "废标公告", "其他公告",
    ]
    t       = normalize_inline(title)
    changed = True
    while changed:
        changed = False
        for s in suffixes:
            if t.endswith(s):
                t       = t[:-len(s)].strip()
                changed = True
                break
    t = re.sub(r"（?\(?第?\d+\s*标段\)?）?$", "", t).strip()
    t = re.sub(r"\(?\d+\s*标段\)?$",           "", t).strip()
    t = re.sub(r"其它第一标段$",                "", t).strip()
    return t or normalize_inline(title)


# ==================== 列表页 ====================

def fetch_list(channel_id, days=4, search_title="", verbose=True):
    results  = []
    page     = 1
    prev_url = "https://prec.sxzwfw.gov.cn/jyxx/index.jhtml"
    while True:
        url  = LIST_URL if page == 1 else LIST_URL.replace("queryContent-", f"queryContent_{page}-")
        data = {
            "title": search_title, "channelId": channel_id, "inDates": days,
            "beginTime": "", "endTime": "", "origin": "", "ext": "",
        }
        headers          = dict(HEADERS)
        headers["Referer"] = prev_url
        if verbose:
            print(f"  [列表] channelId={channel_id} 第{page}页...")
        resp     = post_req(url, data, headers=headers)
        prev_url = url
        if not resp:
            if verbose: print("  [列表] 请求失败")
            break
        if len(resp.text) < 100:
            if verbose: print(f"  [列表] 响应内容异常(长度={len(resp.text)})，跳过")
            break
        soup  = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".cs_two_c_2")
        if not items:
            if page == 1 and verbose: print(f"  [列表] 无数据，状态码={resp.status_code}")
            break
        for item in items:
            link = item.get("href", "")
            if not link.startswith("http"):
                link = BASE_URL + link
            link = link.replace("http://prec.sxzwfw.gov.cn:80", "https://prec.sxzwfw.gov.cn")
            link = link.replace("http://prec.sxzwfw.gov.cn",    "https://prec.sxzwfw.gov.cn")
            title_el  = item.select_one(".cs_bz_cont")
            raw_title = title_el.get_text(strip=True) if title_el else ""
            date_el   = item.select_one(".cs_bz_cont_1_time")
            date      = date_el.get_text(strip=True) if date_el else ""
            results.append({
                "raw_title": raw_title,
                "title":     clean_title(raw_title),
                "link":      link,
                "date":      date,
            })
        count_match = re.search(r",count:\s*(\d+)", resp.text)
        if count_match:
            total = int(count_match.group(1))
            if verbose: print(f"  [列表] 总条数={total}，当前已获取={len(results)}")
            if page * 10 >= total:
                break
        else:
            break
        page += 1
        sleep()
    if verbose: print(f"  [列表] 共 {len(results)} 条")
    return results


# ==================== 详情页解析 ====================

def get_content_text(soup: BeautifulSoup) -> str:
    content_el = soup.select_one("table.gycq-table td") or soup.select_one(".cs_xq_content")
    return normalize_text(content_el.get_text(separator="\n") if content_el else soup.get_text(separator="\n"))


def section_between(text: str, start_patterns, end_patterns) -> str:
    start_pos = -1
    for p in start_patterns:
        m = re.search(p, text or "", re.S)
        if m:
            start_pos = m.start()
            break
    if start_pos == -1:
        return ""
    sub     = text[start_pos:]
    end_pos = len(sub)
    for p in end_patterns:
        m = re.search(p, sub, re.S)
        if m and 0 < m.start() < end_pos:
            end_pos = m.start()
    return sub[:end_pos].strip()


def extract_first(patterns, text: str) -> str:
    for p in patterns:
        m = re.search(p, text or "", re.S)
        if m:
            val = clean_value(m.group(1))
            if not is_empty_value(val):
                return val
    return DEFAULT_VALUE


def extract_label_value(block: str, label_patterns, next_label_patterns=None) -> str:
    if not block:
        return DEFAULT_VALUE
    if next_label_patterns is None:
        next_label_patterns = [
            r"招\s*标\s*人", r"招\s*标\s*代\s*理\s*机\s*构", r"监督部门", r"监督单位",
            r"地\s*址", r"地址", r"联\s*系\s*人", r"联系人",
            r"电\s*话", r"电话", r"联系电话", r"联系方式", r"电子邮件", r"邮\s*箱", r"邮箱",
        ]
    label_alt = "|".join(label_patterns)
    next_alt  = "|".join(next_label_patterns)
    pattern   = rf"(?:{label_alt})\s*[:：]\s*(.*?)(?=\n\s*(?:{next_alt})\s*[:：]|$)"
    m         = re.search(pattern, block, re.S)
    if m:
        return clean_value(m.group(1))
    flat    = normalize_inline(block)
    pattern = rf"(?:{label_alt})\s*[:：]\s*([^：:]+?)(?=(?:{next_alt})\s*[:：]|$)"
    m       = re.search(pattern, flat, re.S)
    if m:
        return clean_value(m.group(1))
    return DEFAULT_VALUE


def parse_plan_detail(url):
    """招标计划详情页 → 提取所属行业、组织形式"""
    resp = get(url)
    if not resp:
        return {}
    soup   = BeautifulSoup(resp.text, "html.parser")
    table  = soup.select_one("table.bid_msgTable")
    result = {}
    if not table:
        return result
    for row in table.select("tr"):
        tds        = row.select("td")
        label_tds  = [(i, td) for i, td in enumerate(tds) if td.find("b")]
        for idx, ltd in label_tds:
            label       = ltd.get_text(strip=True).rstrip("：: ")
            label_clean = re.sub(r"[（(][^）)]*[）)]", "", label).strip()
            val         = ""
            for j in range(idx + 1, len(tds)):
                if not tds[j].find("b"):
                    val = tds[j].get_text(strip=True)
                    break
            val = clean_value(val)
            if label_clean == "项目类型":
                result["所属行业"] = val
            elif label_clean == "招标方式":
                result["组织形式"] = val
    return result


def extract_open_time(full_text: str) -> str:
    section = section_between(
        full_text,
        [r"六\s*[、\.．]\s*开标时间", r"开标时间及地点", r"开标时间"],
        [r"七\s*[、\.．]", r"提交投标保证金", r"投标保证金", r"八\s*[、\.．]", r"$"],
    )
    return extract_first([
        r"开标时间\s*[:：]\s*([0-9]{4}[-年]\s*[0-9]{1,2}[-月]\s*[0-9]{1,2}(?:日)?\s*[0-9]{1,2}[:时：]\s*[0-9]{1,2}(?:分|:[0-9]{1,2})?)",
        r"开标时间\s*[:：]\s*([^\n；;。]+)",
    ], section or full_text)


def extract_file_sale_time(full_text: str) -> str:
    section = section_between(
        full_text,
        [r"四\s*[、\.．]\s*招标文件的获取", r"招标文件的获取", r"文件的获取"],
        [r"五\s*[、\.．]\s*投标文件", r"投标文件的递交", r"递交截止时间", r"$"],
    )
    return extract_first([
        r"获取时间\s*[:：]\s*([0-9]{4}[-年]\s*[0-9]{1,2}[-月]\s*[0-9]{1,2}.*?(?:--|至|到).*?[0-9]{4}[-年]\s*[0-9]{1,2}[-月]\s*[0-9]{1,2}.*?[0-9]{1,2}[:时：]\s*[0-9]{1,2}(?:分)?)",
        r"获取时间\s*[:：]\s*([^\n]+(?:--|至|到)[^\n]+)",
        r"招标文件获取时间\s*[:：]\s*([^\n。；;]+)",
    ], section or full_text)


def split_contact_sections(contact_section: str):
    tenderer_block = section_between(
        contact_section,
        [r"招\s*标\s*人\s*[:：]"],
        [r"招\s*标\s*代\s*理\s*机\s*构\s*[:：]", r"招标人或招标代理机构", r"主要负责人", r"签章"],
    )
    agency_block = section_between(
        contact_section,
        [r"招\s*标\s*代\s*理\s*机\s*构\s*[:：]"],
        [r"招标人或招标代理机构", r"主要负责人", r"签章", r"$"],
    )
    return tenderer_block, agency_block


def extract_contact_fields(full_text: str) -> dict:
    contact_section = section_between(
        full_text,
        [r"十[一1]\s*[、\.．]\s*联系方式", r"[一二三四五六七八九十]+[、\.．]\s*联系方式", r"联系方式"],
        [r"招标人或招标代理机构", r"主要负责人", r"签章", r"山西省行政审批服务管理局", r"$"],
    )
    tenderer_block, agency_block = split_contact_sections(contact_section)
    result = {}
    result["招标人"]         = extract_first([r"招\s*标\s*人\s*[:：]\s*(.*?)(?=\n\s*(?:地\s*址|地址)\s*[:：])", r"招\s*标\s*人\s*[:：]\s*([^\n]+)"], tenderer_block)
    result["招标人地址"]     = extract_label_value(tenderer_block, [r"地\s*址", r"地址"])
    result["招标人联系人"]   = extract_label_value(tenderer_block, [r"联\s*系\s*人", r"联系人"])
    result["招标人联系方式"] = extract_label_value(tenderer_block, [r"电\s*话", r"电话", r"联系电话", r"联系方式"])
    result["招标代理机构"]         = extract_first([r"招\s*标\s*代\s*理\s*机\s*构\s*[:：]\s*(.*?)(?=\n\s*(?:地\s*址|地址)\s*[:：])", r"招\s*标\s*代\s*理\s*机\s*构\s*[:：]\s*([^\n]+)"], agency_block)
    result["招标代理机构地址"]     = extract_label_value(agency_block, [r"地\s*址", r"地址"])
    result["招标代理机构联系人"]   = extract_label_value(agency_block, [r"联\s*系\s*人", r"联系人"])
    result["招标代理机构联系方式"] = extract_label_value(agency_block, [r"电\s*话", r"电话", r"联系电话", r"联系方式"])
    return result


def extract_supervision_fields(full_text: str) -> dict:
    section = section_between(
        full_text,
        [r"十\s*[、\.．]\s*监督部门", r"[一二三四五六七八九十]+[、\.．]\s*监督部门",
         r"监督单位\s*[:：]", r"监督部门\s*[:：]", r"监督部门"],
        [r"十[一1]\s*[、\.．]\s*联系方式", r"[一二三四五六七八九十]+[、\.．]\s*联系方式",
         r"联系方式", r"[一二三四五六七八九十]+[、\.．]\s*其他", r"$"],
    )
    base   = section or full_text
    result = {}
    result["监督部门"] = extract_first([
        r"本招标项目的监督部门为\s*([^。\n；;]+)",
        r"监督部门为\s*([^。\n；;]+)",
        r"监督单位\s*[:：]\s*([^。\n；;]+)",
        r"监督部门\s*[:：]\s*([^。\n；;]+)",
    ], base)
    result["监督部门地址"]   = extract_label_value(section, [r"地\s*址", r"地址"])
    result["监督部门联系人"] = extract_label_value(section, [r"联\s*系\s*人", r"联系人"])
    phone = extract_first([
        r"电话为\s*([0-9\-、，,/／\s]{6,})",
        r"联系电话\s*[:：]?\s*([0-9\-、，,/／\s]{6,})",
        r"联系方式\s*[:：]?\s*([0-9\-、，,/／\s]{6,})",
        r"电\s*话\s*[:：]?\s*([0-9\-、，,/／\s]{6,})",
    ], base)
    if not is_empty_value(phone):
        phone = re.sub(r"\s+", "", phone).strip("。；;，,")
    result["监督部门联系方式"] = phone
    return result


def parse_notice_detail(url):
    """招标公告详情页 → 提取全部字段"""
    resp = get(url)
    if not resp:
        return {}
    soup      = BeautifulSoup(resp.text, "html.parser")
    full_text = get_content_text(soup)
    result    = {
        "公告内容":   full_text if full_text else DEFAULT_VALUE,
        "开标时间":   extract_open_time(full_text),
        "标书发售时间": extract_file_sale_time(full_text),
    }
    result.update(extract_contact_fields(full_text))
    result.update(extract_supervision_fields(full_text))
    return result


def parse_supervision_detail(url):
    """候选人/结果详情页 → 只提取监督部门字段"""
    resp = get(url)
    if not resp:
        return {}
    soup = BeautifulSoup(resp.text, "html.parser")
    return extract_supervision_fields(get_content_text(soup))



# ==================== PDF 详情页 / OCR 辅助 ====================

def extract_pdf_url_from_detail(soup: BeautifulSoup, page_url: str) -> str:
    """
    从详情页中识别 PDF 地址。
    兼容 iframe/embed/object/a 标签。
    样例：<iframe src="https://.../xxx.pdf"></iframe>
    """
    candidates = []
    for tag, attr in [("iframe", "src"), ("embed", "src"), ("object", "data"), ("a", "href")]:
        for el in soup.select(tag):
            u = el.get(attr, "")
            if u and ".pdf" in u.lower():
                candidates.append(u.strip())

    if not candidates:
        return ""

    pdf_url = candidates[0]
    if pdf_url.startswith("//"):
        pdf_url = "https:" + pdf_url
    elif pdf_url.startswith("/") or not pdf_url.startswith("http"):
        pdf_url = urljoin(page_url, pdf_url)

    return pdf_url


def _safe_cache_name(url: str, suffix: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest() + suffix


def download_pdf(pdf_url: str) -> str:
    """下载 PDF 到本地缓存，返回 PDF 路径。"""
    if not pdf_url:
        return ""

    os.makedirs(PDF_CACHE_DIR, exist_ok=True)
    pdf_path = os.path.join(PDF_CACHE_DIR, _safe_cache_name(pdf_url, ".pdf"))

    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1024:
        return pdf_path

    print(f"  [PDF] 下载: {pdf_url}")
    resp = get(pdf_url, retries=5)
    if not resp:
        print("  [PDF] 下载失败")
        return ""

    try:
        with open(pdf_path, "wb") as f:
            f.write(resp.content)
    except Exception as e:
        print(f"  [PDF] 写入失败: {e}")
        return ""

    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) <= 1024:
        print("  [PDF] 文件过小，疑似下载失败")
        return ""

    return pdf_path


def extract_text_from_pdf(pdf_path: str, max_pages: int = PDF_TEXT_MAX_PAGES) -> str:
    """
    先尝试从文字型 PDF 直接提取文本。
    如果 PDF 是扫描件，这里通常只能得到很少文字，后续再走 OCR。
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        print("  [PDF] 未安装 PyMuPDF，无法直接提取PDF文字；可执行：pip install pymupdf")
        return ""

    try:
        doc = fitz.open(pdf_path)
        texts = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text = page.get_text("text") or ""
            if text.strip():
                texts.append(text)
        doc.close()
        return normalize_text("\n".join(texts))
    except Exception as e:
        print(f"  [PDF] 直接提取文字失败: {e}")
        return ""


def render_pdf_pages_to_images(pdf_path: str, max_pages: int = OCR_MAX_PAGES, dpi: int = OCR_DPI) -> list:
    """把 PDF 前几页渲染成 PNG 图片，供 qwen-vl-ocr 识别。"""
    try:
        import fitz  # PyMuPDF
    except Exception:
        print("  [OCR] 未安装 PyMuPDF，无法把PDF转图片；可执行：pip install pymupdf")
        return []

    image_dir = os.path.join(OCR_CACHE_DIR, "images")
    os.makedirs(image_dir, exist_ok=True)

    image_paths = []
    try:
        doc = fitz.open(pdf_path)
        key = hashlib.md5((pdf_path + str(os.path.getmtime(pdf_path))).encode("utf-8")).hexdigest()
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            img_path = os.path.join(image_dir, f"{key}_{i+1}.png")
            if not os.path.exists(img_path) or os.path.getsize(img_path) <= 1024:
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                pix.save(img_path)
            image_paths.append(img_path)
        doc.close()
    except Exception as e:
        print(f"  [OCR] PDF转图片失败: {e}")
        return []

    return image_paths


def image_to_data_url(image_path: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def ocr_image_with_qwen(image_path: str, page_no: int = 1, retries: int = 2) -> str:
    """调用 DMX 的 qwen-vl-ocr 对单页图片做 OCR，返回纯文本。"""
    client = get_ai_client()
    data_url = image_to_data_url(image_path)

    prompt = (
        "请对这张招标公告PDF页面做OCR识别，只输出页面中的原始文字。"
        "保持段落顺序，不要总结，不要解释，不要补充不存在的内容。"
        "如果有表格，也请按从上到下、从左到右的阅读顺序输出。"
    )

    for attempt in range(retries + 1):
        try:
            sleep_before_ai_call()
            resp = client.chat.completions.create(
                model=OCR_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                temperature=0.0,
                max_tokens=6000,
            )
            TOKEN_COUNTER.add(resp.usage)
            text = resp.choices[0].message.content.strip()
            return normalize_text(text)
        except Exception as e:
            print(f"  [OCR] 第{page_no}页调用 {OCR_MODEL} 失败({attempt+1}/{retries+1}): {e}")
            if attempt < retries:
                ai_retry_sleep(attempt)

    return ""


def ocr_pdf_with_qwen(pdf_url: str, pdf_path: str) -> str:
    """PDF 扫描件走 qwen-vl-ocr，结果缓存为 txt。"""
    os.makedirs(OCR_CACHE_DIR, exist_ok=True)
    txt_path = os.path.join(OCR_CACHE_DIR, _safe_cache_name(pdf_url, ".txt"))

    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 100:
        with open(txt_path, "r", encoding="utf-8") as f:
            return normalize_text(f.read())

    image_paths = render_pdf_pages_to_images(pdf_path, max_pages=OCR_MAX_PAGES, dpi=OCR_DPI)
    if not image_paths:
        return ""

    print(f"  [OCR] 使用 {OCR_MODEL} 识别PDF，共 {len(image_paths)} 页")
    page_texts = []
    for i, img in enumerate(image_paths, start=1):
        text = ocr_image_with_qwen(img, page_no=i)
        if text:
            page_texts.append(f"【第{i}页】\n{text}")

    full_text = normalize_text("\n".join(page_texts))
    if full_text:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(full_text)
        print(f"  [OCR] 结果缓存: {txt_path}")

    return full_text


def get_pdf_text(pdf_url: str) -> str:
    """
    获取 PDF 可用文本：
    1. 先下载 PDF
    2. 优先用 PyMuPDF 直接提文字
    3. 如果文字过少，再用 qwen-vl-ocr 做 OCR
    """
    pdf_path = download_pdf(pdf_url)
    if not pdf_path:
        return ""

    text = extract_text_from_pdf(pdf_path)
    if len(text) >= PDF_TEXT_MIN_CHARS:
        print(f"  [PDF] 直接提取文字成功，长度={len(text)}")
        return text

    print(f"  [PDF] 直接文字过少，长度={len(text)}，切换 OCR 模型: {OCR_MODEL}")
    return ocr_pdf_with_qwen(pdf_url, pdf_path)


def get_detail_text_for_ai(link: str) -> str:
    """
    AI补全专用：
    - 普通 HTML 详情页：返回正文文本
    - PDF iframe 详情页：下载PDF，先直接提文字，失败/过少再 qwen-vl-ocr
    """
    resp = get(link, retries=5)
    if not resp:
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")
    pdf_url = extract_pdf_url_from_detail(soup, link)

    if pdf_url:
        print(f"  [PDF公告] 发现PDF: {pdf_url}")
        return get_pdf_text(pdf_url)

    return get_content_text(soup)


# ==================== CSV 操作 ====================

def empty_row(project_name: str) -> dict:
    row = {col: DEFAULT_VALUE for col in COLUMNS}
    row["项目名称"] = project_name
    row["发布网站"] = SITE_NAME
    row["依据文件"] = "无"
    row["依据文号"] = "无"
    return row


def load_csv():
    if not os.path.exists(CSV_PATH):
        print(f"[CSV] 新建增量总表：{CSV_PATH}")
        return pd.DataFrame(columns=COLUMNS)
    if os.path.getsize(CSV_PATH) == 0:
        df = pd.DataFrame(columns=COLUMNS)
        save_csv(df)
        return df
    try:
        df = pd.read_csv(CSV_PATH, dtype=str).fillna(DEFAULT_VALUE)
    except pd.errors.EmptyDataError:
        df = pd.DataFrame(columns=COLUMNS)
        save_csv(df)
        return df
    except Exception as e:
        backup_path = CSV_PATH + f".bad_{TODAY_STR}"
        print(f"[CSV] 读取失败：{e}，备份到 {backup_path}")
        try: os.rename(CSV_PATH, backup_path)
        except Exception: pass
        df = pd.DataFrame(columns=COLUMNS)
        save_csv(df)
        return df
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = DEFAULT_VALUE
    df = df[COLUMNS].fillna(DEFAULT_VALUE)
    df.loc[df["发布网站"].isin(["", "暂无", DEFAULT_VALUE]), "发布网站"] = SITE_NAME

    # 清洗旧数据：发布日期统一为“公告类型-日期”，例如“招标公告-2026.6.29”
    if "发布日期" in df.columns:
        df["发布日期"] = df["发布日期"].apply(normalize_publish_cell)

    print(f"[CSV] 加载增量总表 {len(df)} 行：{CSV_PATH}")
    return df


def save_csv(df: pd.DataFrame):
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = DEFAULT_VALUE
    df       = df[COLUMNS].fillna(DEFAULT_VALUE)

    # 保存前再次规范发布日期字段，避免出现纯日期或“类型:日期”
    if "发布日期" in df.columns:
        df["发布日期"] = df["发布日期"].apply(normalize_publish_cell)

    tmp_path = CSV_PATH + ".tmp"
    df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    os.replace(tmp_path, CSV_PATH)


def find_row(df: pd.DataFrame, project_name: str) -> int:
    if df.empty:
        return -1
    project_name = normalize_inline(project_name)
    mask         = df["项目名称"] == project_name
    if mask.any():
        return df[mask].index[0]
    for idx, row in df.iterrows():
        name = str(row["项目名称"])
        if len(name) >= 5 and len(project_name) >= 5 and (project_name in name or name in project_name):
            return idx
    return -1


def merge_into_row(row: dict, updates: dict) -> dict:
    """
    将 updates 中的字段合并进 row（dict），规则：
      - 目标字段为空时直接写入
      - 公告内容/开标时间/标书发售时间 不覆盖（保留最早的招标公告数据）
      - 其他非空字段覆盖写入
    """
    KEEP_FIRST = {"公告内容", "开标时间", "标书发售时间"}
    for k, v in updates.items():
        if k not in COLUMNS:
            continue
        v = clean_value(v)
        if is_empty_value(v):
            continue
        old = row.get(k, DEFAULT_VALUE)
        if is_empty_value(old):
            row[k] = v
        elif k not in KEEP_FIRST:
            row[k] = v
    return row


def insert_or_replace_row(df: pd.DataFrame, row: dict) -> pd.DataFrame:
    """
    根据项目名称查找已有行：
      - 存在 → 用新 row 完整替换（已在 find_and_fill_history 里合并过了）
      - 不存在 → 新增
    """
    for col in COLUMNS:
        if col not in row:
            row[col] = DEFAULT_VALUE
    row["发布网站"] = SITE_NAME
    row["依据文件"] = row.get("依据文件", "无")
    row["依据文号"] = row.get("依据文号", "无")

    idx = find_row(df, row["项目名称"])
    if idx >= 0:
        for col in COLUMNS:
            df.at[idx, col] = row.get(col, DEFAULT_VALUE)
        return df
    return pd.concat([df, pd.DataFrame([row], columns=COLUMNS)], ignore_index=True)


# ==================== 核心：全量历史搜索合并 ====================

def search_history(project_name: str, channel_id: int):
    """在指定频道搜索项目历史，返回列表"""
    return fetch_list(channel_id, days=4000, search_title=project_name, verbose=False)


def find_and_fill_history(project_name: str, trigger_item: dict, trigger_type: str) -> dict:
    """
    搜索项目在所有频道的历史公告，合并成一个完整行后返回。
    trigger_item / trigger_type 是本次触发的那条新公告（已包含在合并结果里）。

    合并顺序（后写的覆盖先写的，KEEP_FIRST 字段除外）：
      招标计划 → 招标公告 → 变更（只聚合类型/日期/历史） → 候选人 → 结果 → 当前触发公告
    """
    print(f"  [历史回填] 搜索项目：{project_name}")
    row = empty_row(project_name)

    # ── 1. 招标计划 ──
    plan_items = search_history(project_name, CHANNEL["招标计划"])
    if plan_items:
        sleep()
        plan_detail = parse_plan_detail(plan_items[0]["link"])
        row = merge_into_row(row, plan_detail)
        row["公告类型"] = append_unique(row["公告类型"], "招标计划")
        if plan_items[0].get("date"):
            row["发布日期"] = append_unique(row["发布日期"], make_publish_part("招标计划", plan_items[0]["date"]))
        row["公告历史"] = append_unique(row["公告历史"], f"{plan_items[0]['raw_title']}|{plan_items[0]['link']}")
        print("    ✓ 历史招标计划")

    # ── 2. 招标公告 ──
    notice_items = [
        i for i in search_history(project_name, CHANNEL["招标公告"])
        if classify_title(i["raw_title"]) == "notice"
        and not any(kw in i["raw_title"] for kw in SKIP_KEYWORDS)
    ]
    if notice_items:
        sleep()
        notice_detail = parse_notice_detail(notice_items[0]["link"])
        row = merge_into_row(row, notice_detail)
        label = type_label(notice_items[0]["raw_title"], "notice")
        row["公告类型"] = append_unique(row["公告类型"], label)
        if notice_items[0].get("date"):
            row["发布日期"] = append_unique(row["发布日期"], make_publish_part(label, notice_items[0]["date"]))
        row["公告历史"] = append_unique(row["公告历史"], f"{notice_items[0]['raw_title']}|{notice_items[0]['link']}")
        print("    ✓ 历史招标公告")

    # ── 3. 变更公告（只聚合，不解析正文） ──
    for item in search_history(project_name, CHANNEL["变更公告"]):
        label = type_label(item["raw_title"], "amendment")
        row["公告类型"] = append_unique(row["公告类型"], label)
        if item.get("date"):
            row["发布日期"] = append_unique(row["发布日期"], make_publish_part(label, item["date"]))
        row["公告历史"] = append_unique(row["公告历史"], f"{item['raw_title']}|{item['link']}")

    # ── 4. 中标候选人（补监督部门） ──
    cand_items = search_history(project_name, CHANNEL["中标候选人"])
    if cand_items:
        sleep()
        cand_detail = parse_supervision_detail(cand_items[0]["link"])
        row = merge_into_row(row, cand_detail)
        for item in cand_items:
            label = type_label(item["raw_title"], "candidate")
            row["公告类型"] = append_unique(row["公告类型"], label)
            if item.get("date"):
                row["发布日期"] = append_unique(row["发布日期"], make_publish_part(label, item["date"]))
            row["公告历史"] = append_unique(row["公告历史"], f"{item['raw_title']}|{item['link']}")
        print("    ✓ 历史候选人监督部门")

    # ── 5. 中标结果（补监督部门） ──
    result_items = search_history(project_name, CHANNEL["中标结果"])
    if result_items:
        sleep()
        result_detail = parse_supervision_detail(result_items[0]["link"])
        row = merge_into_row(row, result_detail)
        for item in result_items:
            label = type_label(item["raw_title"], "result")
            row["公告类型"] = append_unique(row["公告类型"], label)
            if item.get("date"):
                row["发布日期"] = append_unique(row["发布日期"], make_publish_part(label, item["date"]))
            row["公告历史"] = append_unique(row["公告历史"], f"{item['raw_title']}|{item['link']}")
        print("    ✓ 历史结果监督部门")

    # ── 6. 合并触发公告本身 ──
    label = type_label(trigger_item["raw_title"], trigger_type)
    row["公告类型"] = append_unique(row["公告类型"], label)
    if trigger_item.get("date"):
        row["发布日期"] = append_unique(row["发布日期"], make_publish_part(label, trigger_item["date"]))
    row["公告历史"] = append_unique(row["公告历史"], f"{trigger_item['raw_title']}|{trigger_item['link']}")

    return row


# ==================== 五步爬取 ====================

def step1_plan(df: pd.DataFrame) -> pd.DataFrame:
    """
    招标计划：直接解析详情页后插入。
    不搜历史——招标计划是项目生命周期的起点，此时其他公告还未发布。
    """
    print("\n===== 第一步：招标计划 =====")
    for item in fetch_list(CHANNEL["招标计划"], CRAWL_DAYS):
        name = item["title"]
        print(f"\n  [招标计划] {name}")
        sleep()
        detail = parse_plan_detail(item["link"])

        row = empty_row(name)
        row = merge_into_row(row, detail)
        row["公告类型"] = append_unique(row["公告类型"], "招标计划")
        if item.get("date"):
            row["发布日期"] = append_unique(row["发布日期"], make_publish_part("招标计划", item["date"]))
        row["公告历史"] = append_unique(row["公告历史"], f"{item['raw_title']}|{item['link']}")

        # 招标计划：已存在则只补充空字段，不覆盖；不存在则新增
        idx = find_row(df, name)
        if idx >= 0:
            # 只把非空字段写进已有行，不整行替换
            for col in COLUMNS:
                v = row.get(col, DEFAULT_VALUE)
                if not is_empty_value(v) and is_empty_value(df.at[idx, col]):
                    df.at[idx, col] = v
            # 聚合字段始终追加
            df.at[idx, "公告类型"] = append_unique(df.at[idx, "公告类型"], "招标计划")
            df.at[idx, "发布日期"] = append_unique(df.at[idx, "发布日期"], make_publish_part("招标计划", item["date"]) if item.get("date") else "")
            df.at[idx, "公告历史"] = append_unique(df.at[idx, "公告历史"], f"{item['raw_title']}|{item['link']}")
            print(f"  → 已存在，补充字段")
        else:
            df = pd.concat([df, pd.DataFrame([row], columns=COLUMNS)], ignore_index=True)
            print(f"  → 新增")

        save_csv(df)
    return df


def _upsert_with_history(df: pd.DataFrame, item: dict, forced_type: str,
                          extra_detail: dict = None) -> pd.DataFrame:
    """
    通用 upsert（招标计划以外的所有公告类型）：
      1. 调用 find_and_fill_history 搜索全量历史，得到合并行
      2. 如果有额外的当前公告详情（招标公告正文等），再合并进去
      3. insert_or_replace_row 写入 CSV
    """
    name = item["title"]
    print(f"  [历史搜索] {name}")

    # 全量历史合并
    merged_row = find_and_fill_history(name, item, forced_type)

    # 追加当前触发公告的详情字段（如招标公告正文解析结果）
    if extra_detail:
        merged_row = merge_into_row(merged_row, extra_detail)

    label = type_label(item["raw_title"], forced_type)
    df    = insert_or_replace_row(df, merged_row)
    print(f"  → upsert 完成：{label}")
    return df


def step2_notice(df: pd.DataFrame) -> pd.DataFrame:
    print("\n===== 第二步：招标公告频道 =====")
    for item in fetch_list(CHANNEL["招标公告"], CRAWL_DAYS):
        raw = item["raw_title"]
        t   = classify_title(raw)

        if t == "skip":
            print(f"\n  [跳过-控制价] {raw}")
            continue

        print(f"\n  [{type_label(raw, t)}] {item['title']}")

        if t == "notice":
            sleep()
            detail = parse_notice_detail(item["link"])
            df = _upsert_with_history(df, item, "notice", extra_detail=detail)

        elif t in ("candidate", "result"):
            sleep()
            detail = parse_supervision_detail(item["link"])
            df = _upsert_with_history(df, item, t, extra_detail=detail)

        elif t in ("terminate", "amendment"):
            df = _upsert_with_history(df, item, t)

        else:
            # unknown：记录但仍走历史回填
            SKIPPED_UNKNOWN.append(item)
            df = _upsert_with_history(df, item, "unknown")

        save_csv(df)
    return df


def step3_amendment(df: pd.DataFrame) -> pd.DataFrame:
    print("\n===== 第三步：变更公告 =====")
    for item in fetch_list(CHANNEL["变更公告"], CRAWL_DAYS):
        raw = item["raw_title"]
        t   = classify_title(raw)

        if t == "skip":
            print(f"\n  [跳过-控制价] {raw}")
            continue

        actual_type = t if t != "unknown" else "amendment"
        print(f"\n  [{type_label(raw, actual_type)}] {item['title']}")

        if t == "notice":
            sleep()
            detail = parse_notice_detail(item["link"])
            df = _upsert_with_history(df, item, "notice", extra_detail=detail)

        elif t in ("candidate", "result"):
            sleep()
            detail = parse_supervision_detail(item["link"])
            df = _upsert_with_history(df, item, t, extra_detail=detail)

        elif t == "terminate":
            df = _upsert_with_history(df, item, "terminate")

        else:
            df = _upsert_with_history(df, item, "amendment")

        save_csv(df)
    return df


def step4_candidate(df: pd.DataFrame) -> pd.DataFrame:
    print("\n===== 第四步：中标候选人公示 =====")
    for item in fetch_list(CHANNEL["中标候选人"], CRAWL_DAYS):
        raw = item["raw_title"]
        if classify_title(raw) == "skip":
            print(f"\n  [跳过-控制价] {raw}")
            continue
        print(f"\n  [中标候选人公示] {item['title']}")
        sleep()
        detail = parse_supervision_detail(item["link"])
        df = _upsert_with_history(df, item, "candidate", extra_detail=detail)
        save_csv(df)
    return df


def step5_result(df: pd.DataFrame) -> pd.DataFrame:
    print("\n===== 第五步：中标结果公示 =====")
    for item in fetch_list(CHANNEL["中标结果"], CRAWL_DAYS):
        raw = item["raw_title"]
        if classify_title(raw) == "skip":
            print(f"\n  [跳过-控制价] {raw}")
            continue
        print(f"\n  [中标结果公示] {item['title']}")
        sleep()
        detail = parse_supervision_detail(item["link"])
        df = _upsert_with_history(df, item, "result", extra_detail=detail)
        save_csv(df)
    return df


# ==================== 自检和 AI ====================

def build_check_log(df: pd.DataFrame):
    problems = []
    for _, row in df.iterrows():
        issues = []
        types  = str(row.get("公告类型", ""))
        if "招标公告" in types or "资格预审公告" in types:
            for field in NOTICE_REQUIRED_FIELDS:
                if is_empty_value(row.get(field, DEFAULT_VALUE)):
                    issues.append(f"{field}未提取")
        if "中标候选人公示" in types or "中标结果公示" in types:
            if is_empty_value(row.get("监督部门",       DEFAULT_VALUE)):
                issues.append("监督部门未提取")
            if is_empty_value(row.get("监督部门联系方式", DEFAULT_VALUE)):
                issues.append("监督部门联系方式未提取")
        if issues:
            problems.append((row["项目名称"], issues))

    with open(CHECK_LOG_PATH, "w", encoding="utf-8") as f:
        f.write("山西省公共资源交易平台 自检日志\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"【类型1】未识别公告（共{len(SKIPPED_UNKNOWN)}条）\n")
        f.write("-" * 40 + "\n")
        if SKIPPED_UNKNOWN:
            for item in SKIPPED_UNKNOWN:
                f.write(f"标题: {item['raw_title']}\n链接: {item['link']}\n\n")
        else:
            f.write("无\n\n")
        f.write(f"【类型2】字段提取失败（共{len(problems)}个项目）\n")
        f.write("-" * 40 + "\n")
        if problems:
            for name, issues in problems:
                f.write(f"项目: {name}\n")
                for issue in issues:
                    f.write(f"  - {issue}\n")
                f.write("\n")
        else:
            f.write("无\n")

    print(f"[自检] → {CHECK_LOG_PATH}")
    return problems


def find_best_link_from_history(history: str, fields) -> str:
    out = []
    if is_empty_value(history):
        return ""
    for p in [x.strip() for x in str(history).split("；") if x.strip()]:
        if "|" not in p:
            continue
        title, link = p.split("|", 1)
        out.append({"title": title.strip(), "link": link.strip(), "type": classify_title(title.strip())})

    if any(f in NOTICE_REQUIRED_FIELDS for f in fields):
        for h in out:
            if h["type"] == "notice" and h["link"].startswith("http"):
                return h["link"]
    for h in out:
        if h["type"] in ("candidate", "result") and h["link"].startswith("http"):
            return h["link"]
    for h in out:
        if h["link"].startswith("http"):
            return h["link"]
    return ""


def ai_extract_fields(html_or_text: str, fields: list, project_name: str = "", retries: int = 2) -> dict:
    if not html_or_text or not fields:
        return {f: "" for f in fields}
    try:
        clean = BeautifulSoup(html_or_text, "html.parser").get_text(separator="\n")
        clean = normalize_text(clean)
    except Exception:
        clean = normalize_text(str(html_or_text))
    client     = get_ai_client()
    fields_str = "、".join(fields)
    system_prompt = (
        "你是招标公告字段提取助手，只返回 JSON 对象，不要解释。"
        "字段找不到返回空字符串。"
        "开标时间从开标时间及地点提取；标书发售时间从招标文件的获取/获取时间提取；"
        "招标人和招标代理机构字段从联系方式章节提取；"
        "监督部门字段只从监督部门/监督单位章节提取，不能把异议联系人当监督部门联系人；"
        "公告内容返回正文全文并去掉页脚备案号。"
    )
    user_prompt = f"项目名称：{project_name}\n需要提取的字段：{fields_str}\n\n公告文本：\n{clean}"
    for attempt in range(retries + 1):
        raw = ""
        try:
            sleep_before_ai_call()
            resp = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=4000,
            )
            TOKEN_COUNTER.add(resp.usage)
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "",           raw)
            result = json.loads(raw)
            for f in fields:
                result.setdefault(f, "")
            return result
        except json.JSONDecodeError as e:
            print(f"    [AI] JSON解析失败(第{attempt+1}次): {e} | 原始: {raw[:100]}")
        except Exception as e:
            print(f"    [AI] 调用失败(第{attempt+1}次): {e}")
        if attempt < retries:
            ai_retry_sleep(attempt)
    return {f: "" for f in fields}


def ai_supplement(df: pd.DataFrame, problems: list) -> pd.DataFrame:
    print("\n===== AI 兜底补充提取 =====")
    if not problems:
        print("  自检无字段缺失，跳过 AI")
        _write_ai_report([], 0)
        return df
    if not AI_API_KEY or OpenAI is None:
        print("  未配置 DMX_API_KEY 或 openai 包不可用，跳过 AI 兜底")
        _write_ai_report([], 0, skipped_reason="未配置 DMX_API_KEY 或 openai 包不可用")
        return df

    records       = []
    updated_count = 0

    for project_name, issue_names in problems:
        idx = find_row(df, project_name)
        if idx < 0:
            continue
        fields = []
        for issue in issue_names:
            field = issue.replace("未提取", "").strip()
            if field in COLUMNS and field not in fields:
                fields.append(field)
        if not fields:
            continue
        link = find_best_link_from_history(str(df.at[idx, "公告历史"]), fields)
        if not link:
            records.append({"project": project_name, "link": "", "fields": fields,
                            "status": "跳过（无可用详情链接）", "values": {}})
            continue

        print(f"  [{project_name}] AI补字段 {fields} ← {link}")
        sleep()

        # AI补全阶段统一取可提取文本：普通HTML取正文；PDF公告下载PDF并用 qwen-vl-ocr OCR。
        detail_text = get_detail_text_for_ai(link)
        if not detail_text:
            records.append({"project": project_name, "link": link, "fields": fields,
                            "status": "失败（详情页/PDF/OCR均未取得文本，AI未调用）", "values": {}})
            continue

        result      = ai_extract_fields(detail_text, fields, project_name)
        values      = {}
        success_any = False
        for field in fields:
            val = clean_value(result.get(field, ""))
            values[field] = val
            if not is_empty_value(val):
                df.at[idx, field] = val
                updated_count    += 1
                success_any       = True
                print(f"    ✓ {field}: {val[:80]}")
            else:
                print(f"    ✗ {field}: AI未提取")
        records.append({
            "project": project_name, "link": link, "fields": fields,
            "status":  "成功" if success_any else "失败", "values": values,
        })

    _write_ai_report(records, updated_count)
    print(f"  AI补全完成：{updated_count} 个字段")
    print(f"  {TOKEN_COUNTER.report()}")
    return df


def _write_ai_report(records: list, updated_count: int, skipped_reason: str = ""):
    success = sum(1 for r in records if r.get("status") == "成功")
    failed  = sum(1 for r in records if str(r.get("status", "")).startswith("失败"))
    skipped = sum(1 for r in records if str(r.get("status", "")).startswith("跳过"))
    with open(AI_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("山西省公共资源交易平台 AI补全报告\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"使用模型: {AI_MODEL}\n")
        f.write("=" * 60 + "\n\n")
        if skipped_reason:
            f.write(f"AI未执行原因：{skipped_reason}\n\n")
        f.write("【汇总】\n")
        f.write(f"  处理项目数 : {len(records)}\n")
        f.write(f"  成功        : {success}\n")
        f.write(f"  失败        : {failed}\n")
        f.write(f"  跳过        : {skipped}\n")
        f.write(f"  字段补全数  : {updated_count}\n\n")
        f.write("【Token消耗】\n")
        f.write(f"  AI调用次数  : {TOKEN_COUNTER.call_count}\n")
        f.write(f"  输入tokens  : {TOKEN_COUNTER.prompt_tokens:,}\n")
        f.write(f"  输出tokens  : {TOKEN_COUNTER.completion_tokens:,}\n")
        f.write(f"  合计tokens  : {TOKEN_COUNTER.total_tokens:,}\n\n")
        f.write("【详细记录】\n")
        f.write("-" * 40 + "\n")
        for r in records:
            f.write(f"项目: {r['project']}\n")
            f.write(f"  链接: {r.get('link', '')}\n")
            f.write(f"  目标字段: {', '.join(r.get('fields', []))}\n")
            f.write(f"  结果: {r.get('status', '')}\n")
            for field, val in r.get("values", {}).items():
                show = str(val)[:100] + "..." if len(str(val)) > 100 else str(val)
                f.write(f"  - {field}: {show}\n")
            f.write("\n")
    print(f"[AI报告] → {AI_REPORT_PATH}")


# ==================== 索引 ====================

def build_index(df: pd.DataFrame):
    path = CSV_PATH.replace(".csv", "_index.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("山西省公共资源交易平台 文本索引\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"总记录数: {len(df)}\n")
        f.write("=" * 60 + "\n\n")
        for idx, row in df.iterrows():
            f.write(f"[{idx+1}] {row['项目名称']}\n")
            f.write(f"  公告类型: {row['公告类型']}\n")
            f.write(f"  发布日期: {row['发布日期']}\n")
            f.write(f"  所属行业: {row['所属行业']} | 组织形式: {row['组织形式']}\n")
            f.write(f"  开标时间: {row['开标时间']} | 标书发售时间: {row['标书发售时间']}\n")
            f.write(f"  招标人: {row['招标人']} | 联系人: {row['招标人联系人']} | 电话: {row['招标人联系方式']}\n")
            f.write(f"  监督部门: {row['监督部门']} | 电话: {row['监督部门联系方式']}\n")
            f.write(f"  公告历史: {row['公告历史']}\n\n")
    print(f"[索引] → {path}")


# ==================== 主程序 ====================

def main():
    print("=" * 60)
    print("山西省公共资源交易平台爬虫 v4.3（历史回填 + 自检 + AI兜底 + IP代理池）")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"输出: {CSV_PATH}")
    print("=" * 60)

    print("初始化 Session...")
    try:
        init_resp = get("https://prec.sxzwfw.gov.cn/jyxx/index.jhtml")
        print("Session 初始化成功" if init_resp else "Session 初始化失败，继续尝试")
    except Exception as e:
        print(f"Session 初始化失败: {e}")

    print(f"代理开关: {USE_PROXY}")
    if USE_PROXY:
        try:    print(f"代理池状态: {PROXY_POOL.stats()}")
        except Exception as e: print(f"代理池状态读取失败: {e}")

    df = load_csv()

    # ── 五步爬取 ──
    # step1：招标计划 → 直接插入，不搜历史
    # step2~5：所有公告 → 先搜全量历史再 upsert
    df = step1_plan(df)
    df = step2_notice(df)
    df = step3_amendment(df)
    df = step4_candidate(df)
    df = step5_result(df)

    save_csv(df)
    build_index(df)

    # ── 自检 ──
    problems = build_check_log(df)

    # ── AI 兜底 ──
    df = ai_supplement(df, problems)
    save_csv(df)
    build_check_log(df)   # 二次自检确认AI补全效果
    build_index(df)

    print(f"\n{'=' * 60}")
    print(f"完成！共 {len(df)} 条记录 → {CSV_PATH}")
    print(f"自检日志 → {CHECK_LOG_PATH}")
    print(f"AI报告   → {AI_REPORT_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()