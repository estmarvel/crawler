import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from config import BASE_URL, CATEGORIES, HEADERS, REQUEST_DELAY, REQUEST_TIMEOUT, MAX_RETRIES
from storage import save_announcement, save_crawl_log

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

URL_TYPE_MAP = {
    "jyxxgczb":  "招标公告",
    "jyxxgcgs":  "中标结果公示",
    "jyxxgchxr": "候选人公示",
    "jyxxgcgz":  "更正公告",
    "jyxxzbjh":  "招标计划",
    "jyxxzczb":  "招标公告",
    "jyxxzcgs":  "中标结果公示",
    "jyxxtdzb":  "招标公告",
    "jyxxcqzb":  "招标公告",
}


def fetch(url: str, retries: int = MAX_RETRIES) -> str:
    for attempt in range(1, retries + 1):
        try:
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False)
            if resp.status_code in (301, 302):
                location = resp.headers.get("Location", "")
                if location.startswith("http"):
                    resp = SESSION.get(location, timeout=REQUEST_TIMEOUT, allow_redirects=False)
                    resp.encoding = resp.apparent_encoding or "utf-8"
                    return resp.text
            resp.encoding = resp.apparent_encoding or "utf-8"
            if resp.status_code == 200:
                return resp.text
            logger.warning("HTTP %s  [%s]", resp.status_code, url)
        except requests.RequestException as e:
            logger.warning("第 %d 次请求失败 [%s]: %s", attempt, url, e)
        time.sleep(REQUEST_DELAY * attempt)
    return None


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def detect_ann_type(url: str) -> str:
    for key, val in URL_TYPE_MAP.items():
        if key in url:
            return val
    return "其他"


def parse_list_page(html: str, category: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    rows = soup.select("div.cs_two_content a.cs_two_c_2")
    for row in rows:
        title_tag = row.find("p", class_="cs_bz_cont")
        date_tag  = row.find("span", class_="cs_bz_cont_1_time")
        href = row.get("href", "")
        if not title_tag or not href:
            continue
        title    = clean(title_tag.get_text())
        url      = urljoin(BASE_URL, href)
        pub_date = clean(date_tag.get_text()) if date_tag else ""

        # 交易场所（地区）直接从列表页取
        loc_p = row.find("p", class_="cs_bz_cont_1")
        location = ""
        if loc_p:
            for span in loc_p.find_all("span"):
                t = span.get_text()
                if "交易场所" in t:
                    location = t.replace("交易场所：", "").strip()

        ann_type = detect_ann_type(url)

        if title and url:
            items.append({
                "category": category,
                "ann_type": ann_type,
                "title":    title,
                "url":      url,
                "pub_date": pub_date,
                "location": location,
            })
    logger.info("列表页解析到 %d 条 [%s]", len(items), category)
    return items


def _parse_table_page(soup) -> dict:
    """招标计划页：解析 bid_msgTable 结构化表格"""
    result = {}
    table = soup.find("table", class_="bid_msgTable")
    if not table:
        return result
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        i = 0
        while i < len(cells):
            b = cells[i].find("b")
            if b and i + 1 < len(cells):
                key = clean(b.get_text()).rstrip("：:")
                val = clean(cells[i + 1].get_text())
                result[key] = val
                i += 2
            else:
                i += 1
    return result


def _parse_zhaobiao_page(content: str, soup=None) -> dict:
    """招标公告页：正则提取自由文本字段"""
    def extract(pattern, flags=0):
        m = re.search(pattern, content, flags)
        return m.group(1).strip() if m else ""

    # 地区优先从 <u> 标签取，避免正则吃掉正文
    location = ""
    if soup:
        for tag in soup.find_all(["u", "span", "p"]):
            t = tag.get_text()
            if "招标项目所在地区" in t:
                u = tag.find("u")
                if u:
                    location = clean(u.get_text())
                    break
        if not location:
            location = extract(r"招标项目所在地区[：:]\s*([^\s，。、一二三四]{2,20})")
    project_no   = extract(r"招标(?:项目)?编号[：:]\s*([A-Za-z0-9\-]+)")
    budget       = extract(r"(?:估算金额|预算金额|概算金额|最高限价|招标控制价)[：:]\s*([0-9,.万元亿（()）]+)")
    reg_start    = extract(r"获取时间[：:]\s*(\d{4}年\d+月\d+日\s*\d+时\d+分)")
    reg_end      = extract(r"获取时间[：:].+?至\s*(\d{4}年\d+月\d+日\s*\d+时\d+分)")
    bid_deadline = extract(r"投标文件(?:递交)?截止时间[：:]\s*(\d{4}年\d+月\d+日\s*\d+时\d+分)")
    bid_opening  = extract(r"开标时间[：:]\s*(\d{4}年\d+月\d+日\s*\d+时\d+分)")
    tenderee     = extract(r"招\s*标\s*人[：:]\s*([^\n地电联]{2,20})")
    agency       = extract(r"招标代理机构[：:]\s*([^\n地电联]{2,20})")

    qual_m = re.search(r"施工资质要求[：:](.*?)(?=\n\s*\d+[、．.]|$)", content, re.S)
    qualification = clean(qual_m.group(1))[:200] if qual_m else ""

    return {
        "location":      location,
        "project_no":    project_no,
        "budget":        budget,
        "reg_start":     reg_start,
        "reg_end":       reg_end,
        "bid_deadline":  bid_deadline,
        "bid_opening":   bid_opening,
        "qualification": qualification,
        "tenderee":      tenderee,
        "agency":        agency,
    }


def parse_detail_page(html: str, ann_type: str = "") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("div", class_="cs_xq_content") or soup.body
    content = clean(body.get_text()) if body else ""

    # 招标计划：结构化表格
    if ann_type == "招标计划" or soup.find("table", class_="bid_msgTable"):
        fields = _parse_table_page(soup)
        location = ""
        meta_p = soup.find("p", class_="cs_title_P3")
        if meta_p:
            m = re.search(r"信息来源[：:](.+?)(?:公共资源交易平台|交易中心)", meta_p.get_text())
            if m:
                location = m.group(1).strip()
        return {
            "budget":        fields.get("项目总投资", ""),
            "location":      location or fields.get("建设地点", "")[:20],
            "project_no":    fields.get("项目编号", ""),
            "project_type":  fields.get("项目类型", ""),
            "bid_deadline":  "",
            "bid_opening":   "",
            "reg_start":     "",
            "reg_end":       "",
            "qualification": "",
            "tenderee":      fields.get("招标人名称", ""),
            "agency":        "",
            "content":       content[:2000],
        }

    # 招标公告：自由文本正则
    result = _parse_zhaobiao_page(content, soup)
    result["content"] = content[:2000]
    result["project_type"] = ""
    return result


def crawl_category(category: str, url_tpl: str, max_pages: int = 5, fetch_detail: bool = True):
    total_new = 0
    for page in range(1, max_pages + 1):
        list_url = BASE_URL + url_tpl.format(page=page)
        logger.info("▶ 爬取第 %d 页 [%s] %s", page, category, list_url)
        html = fetch(list_url)
        if not html:
            logger.warning("第 %d 页获取失败，停止该分类", page)
            break
        items = parse_list_page(html, category)
        if not items:
            logger.info("第 %d 页无数据，提前终止", page)
            break
        page_new = 0
        for item in items:
            if fetch_detail:
                time.sleep(REQUEST_DELAY)
                detail_html = fetch(item["url"])
                if detail_html:
                    detail = parse_detail_page(detail_html, item.get("ann_type", ""))
                    if item.get("location") and not detail.get("location"):
                        detail["location"] = item["location"]
                    item.update(detail)
            if save_announcement(item):
                page_new += 1
        save_crawl_log(category, page, page_new)
        total_new += page_new
        logger.info("  ✔ 第 %d 页新增 %d 条", page, page_new)
        time.sleep(REQUEST_DELAY)
    logger.info("分类 [%s] 完成，共新增 %d 条", category, total_new)
    return total_new


def crawl_all(max_pages: int = 5, fetch_detail: bool = True) -> dict:
    from config import CATEGORIES
    results = {}
    for cat, tpl in CATEGORIES.items():
        results[cat] = crawl_category(cat, tpl, max_pages, fetch_detail)
    return results
