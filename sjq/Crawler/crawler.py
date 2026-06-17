"""
=============================================================================
全国公共资源交易平台（山西省）- 招标计划公告爬虫核心模块
=============================================================================
负责:
  - 列表页请求与解析(提取项目名称、交易场所、发布日期、详情URL)
  - 详情页请求与解析(提取全部字段)
  - 日期范围过滤(仅保留最近N天)
  - 异常处理与重试
  - 请求频率控制(随机延迟)

依赖: 仅需 requests (无需 bs4/lxml, 使用 stdlib re + html.parser)

数据流:
  crawl()
    → _crawl_list_pages()   逐页爬取列表
      → _fetch_with_retry()  带重试的HTTP请求
      → _parse_list_page()   解析列表页HTML
    → _crawl_detail_page()  逐条爬取详情
      → _fetch_with_retry()  带重试的HTTP请求
      → _parse_detail_page() 解析详情页HTML
    → 返回 List[Dict]       所有数据在内存中
=============================================================================
"""

import logging
import random
import re
import time
from datetime import datetime, timedelta
from html import unescape as html_unescape
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from config import (
    BASE_URL,
    LIST_INDEX_URL,
    LIST_PAGE_URL_TEMPLATE,
    DETAIL_URL_PATTERN,
    DAYS_LOOKBACK,
    MAX_LIST_PAGES,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    PAGE_TRANSITION_DELAY,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    USER_AGENTS,
    BASE_HEADERS,
)
from proxy_pool import ProxyPool

logger = logging.getLogger(__name__)


# ======================== 轻量级HTML解析器(仅用stdlib) ========================

class LinkExtractor(HTMLParser):
    """提取所有 <a> 标签及其href和文本内容."""

    def __init__(self):
        super().__init__()
        self.links: List[Dict] = []  # [{"href": "...", "text": "..."}]

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]):
        if tag.lower() == "a":
            href = ""
            for name, value in attrs:
                if name.lower() == "href":
                    href = value
                    break
            self._current_href = href
            self._current_text = ""

    def handle_data(self, data: str):
        if hasattr(self, "_current_text"):
            self._current_text += data

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and hasattr(self, "_current_href"):
            self.links.append({
                "href": self._current_href,
                "text": self._current_text.strip(),
            })
            del self._current_href
            del self._current_text


class TableFieldExtractor(HTMLParser):
    """从HTML表格中提取字段名-字段值对.

    解析 <td> 元素, 匹配预定义的字段标签, 提取对应的值。
    """

    def __init__(self, field_label_map: Dict[str, str]):
        """
        Args:
            field_label_map: {"标签文本": "字段key"} 映射
        """
        super().__init__()
        self._field_labels = field_label_map
        self.result: Dict[str, str] = {}
        self._in_td = False
        self._td_text = ""
        self._td_texts: List[str] = []  # 所有td的文本内容序列

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "td":
            self._in_td = True
            self._td_text = ""

    def handle_data(self, data):
        if self._in_td:
            self._td_text += data

    def handle_endtag(self, tag):
        if tag.lower() == "td":
            self._in_td = False
            clean = self._td_text.replace("**", "").replace("\u3000", " ").strip()
            # 统一中文标点
            clean = clean.replace("\uff1a", ":").replace("\uff08", "(").replace("\uff09", ")")
            # 规范化空白: 多个连续空格合并为一个
            clean = re.sub(r"\s{2,}", " ", clean)
            self._td_texts.append(clean)

    def extract(self):
        """遍历td序列, 匹配标签并提取值."""
        # 对于每个td, 检查是否匹配某个字段标签
        for i, td_text in enumerate(self._td_texts):
            for label, field_key in self._field_labels.items():
                # 清理标签以便比较
                clean_label = label.replace("\uff08", "(").replace("\uff09", ")").replace("\u3000", " ")
                if clean_label in td_text:
                    # 值在下一个td(或隔一个td, 有些表格有空列)
                    for offset in (1, 2):
                        if i + offset < len(self._td_texts):
                            val = self._td_texts[i + offset]
                            # 排除值本身又是一个标签的情况
                            is_other_label = any(
                                l in val for l in self._field_labels if l != label
                            )
                            if val and not is_other_label:
                                self.result[field_key] = val
                                break
                    break
        return self.result


# ======================== 主爬虫类 ========================

class TenderCrawler:
    """招标计划公告爬虫.

    使用方式:
        pool = ProxyPool()
        crawler = TenderCrawler(proxy_pool=pool)
        results = crawler.crawl()
        # results 是 List[Dict], 每个Dict包含一条招标计划的所有字段
    """

    def __init__(self, proxy_pool: ProxyPool = None):
        """
        Args:
            proxy_pool: 代理池实例, 为None时使用直连模式(不推荐)
        """
        self._proxy_pool = proxy_pool
        self._session = None
        self._stats = {
            "list_pages_crawled": 0,
            "detail_pages_crawled": 0,
            "detail_pages_failed": 0,
            "items_collected": 0,
            "items_skipped_old": 0,
        }

    # ======================== 公开接口 ========================

    def crawl(self) -> List[Dict]:
        """执行爬取: 遍历列表页 → 收集详情URL → 逐条爬取详情.

        Returns:
            招标计划数据列表, 每个元素为包含所有字段的字典
        """
        self._init_session()
        cutoff_date = datetime.now() - timedelta(days=DAYS_LOOKBACK)
        logger.info(f"开始爬取, 日期过滤: 最近 {DAYS_LOOKBACK} 天 (>= {cutoff_date.strftime('%Y-%m-%d')})")

        list_items = self._crawl_list_pages(cutoff_date)
        logger.info(f"列表阶段完成: 收集到 {len(list_items)} 条在日期范围内的记录")

        results = self._crawl_detail_pages(list_items)
        logger.info(
            f"详情阶段完成: 成功 {self._stats['detail_pages_crawled']} 条, "
            f"失败 {self._stats['detail_pages_failed']} 条"
        )

        logger.info(f"爬取结束: 总计 {len(results)} 条有效记录")
        self._print_stats()
        return results

    def stats(self) -> Dict:
        """返回爬取统计信息."""
        return dict(self._stats)

    # ======================== 第一阶段: 列表页爬取 ========================

    def _crawl_list_pages(self, cutoff_date: datetime) -> List[Dict]:
        """逐页爬取列表, 收集详情条目."""
        items = []
        stopped_by_date = False

        for page_num in range(1, MAX_LIST_PAGES + 1):
            list_url = self._build_list_url(page_num)
            logger.info(f"--- 列表第 {page_num} 页 ---")

            html = self._fetch_with_retry(list_url, is_list_page=(page_num == 1))
            if html is None:
                logger.warning(f"列表第 {page_num} 页请求失败, 跳过")
                continue

            self._stats["list_pages_crawled"] += 1
            page_items, page_has_old = self._parse_list_page(html, cutoff_date)
            items.extend(page_items)

            if page_has_old:
                stopped_by_date = True
                logger.info(f"日期过滤触发: 第 {page_num} 页存在 {DAYS_LOOKBACK} 天前的记录, 停止列表爬取")
                break

            if page_num < MAX_LIST_PAGES:
                delay = random.uniform(*PAGE_TRANSITION_DELAY)
                logger.debug(f"翻页等待 {delay:.1f}s...")
                time.sleep(delay)

        if not stopped_by_date and self._stats["list_pages_crawled"] >= MAX_LIST_PAGES:
            logger.warning(f"已达到最大翻页限制 ({MAX_LIST_PAGES} 页), 可能未覆盖全部日期范围")

        return items

    def _build_list_url(self, page_num: int) -> str:
        """构造列表页URL: 第1页 index.jhtml, 第N页 index_N.jhtml."""
        if page_num <= 1:
            return LIST_INDEX_URL
        return LIST_PAGE_URL_TEMPLATE.format(page=page_num)

    def _parse_list_page(
        self, html: str, cutoff_date: datetime
    ) -> Tuple[List[Dict], bool]:
        """解析列表页HTML, 提取项目条目.

        列表页HTML结构 (每个条目是一个 <a> 标签):
          <a href=".../jyxxzbjh/{id}.jhtml" ... class="cs_two_c_2">
            <p class="cs_bz_cont">{项目名称}</p>
            <p class="cs_bz_cont_1">
              <span>交易场所：{地区}</span>
              <span class="cs_bz_cont_1_time">{日期}</span>
            </p>
          </a>

        Args:
            html: 列表页HTML内容
            cutoff_date: 日期下限

        Returns:
            (items列表, 是否遇到超出日期范围的记录)
        """
        items = []
        found_old = False

        # 匹配每个完整的 <a> 标签块: 从开幕到落幕
        a_block_pattern = re.compile(
            r'<a\s+[^>]*href="[^"]*?/jyxxzbjh/(\d+)\.jhtml"[^>]*>'
            r'(.*?)'
            r'</a>',
            re.DOTALL | re.IGNORECASE,
        )

        a_blocks = a_block_pattern.findall(html)
        if not a_blocks:
            logger.warning("列表页未找到招标计划链接")
            return items, found_old

        for detail_id, block_text in a_blocks:
            # 提取项目名称 (第一个 <p class="cs_bz_cont">)
            name_match = re.search(
                r'<p[^>]*class="cs_bz_cont"[^>]*>\s*(.*?)\s*</p>',
                block_text,
                re.DOTALL | re.IGNORECASE,
            )
            title_text = html_unescape(name_match.group(1).strip()) if name_match else ""

            # 提取交易场所
            place_match = re.search(
                r'<span>\s*交易场所[：:]\s*([^<]+)\s*</span>',
                block_text,
                re.IGNORECASE,
            )
            trading_place = place_match.group(1).strip() if place_match else ""

            # 提取发布日期
            date_match = re.search(
                r'<span[^>]*class="cs_bz_cont_1_time"[^>]*>\s*(\d{4}/\d{2}/\d{2})\s*</span>',
                block_text,
                re.IGNORECASE,
            )
            publish_date_str = date_match.group(1) if date_match else ""

            if not title_text:
                continue

            # 日期过滤
            if publish_date_str:
                try:
                    pub_date = datetime.strptime(
                        publish_date_str.replace("/", "-"), "%Y-%m-%d"
                    )
                    if pub_date < cutoff_date:
                        logger.debug(f"跳过过期: {title_text[:30]}... ({publish_date_str})")
                        found_old = True
                        self._stats["items_skipped_old"] += 1
                        continue
                except ValueError:
                    logger.debug(f"无法解析日期: {publish_date_str}")

            detail_url = urljoin(BASE_URL, f"/jyxxzbjh/{detail_id}.jhtml")
            items.append({
                "detail_id": detail_id,
                "detail_url": detail_url,
                "project_name_list": title_text,
                "trading_place": trading_place,
                "publish_date_list": publish_date_str,
            })
            self._stats["items_collected"] += 1

        return items, found_old

    @staticmethod
    def _parse_info_line(info_text: str) -> Tuple[str, str]:
        """解析列表页信息行.

        格式: "交易场所：运城市2026/06/17" → ("运城市", "2026/06/17")
        """
        trading_place = ""
        publish_date = ""
        if not info_text:
            return trading_place, publish_date

        text = info_text.replace("交易场所：", "").replace("交易场所:", "").strip()
        date_match = re.search(r"(\d{4}/\d{2}/\d{2})\s*$", text)
        if date_match:
            publish_date = date_match.group(1)
            trading_place = text[:date_match.start()].strip()

        return trading_place, publish_date

    # ======================== 第二阶段: 详情页爬取 ========================

    def _crawl_detail_pages(self, list_items: List[Dict]) -> List[Dict]:
        """逐条爬取详情页, 解析完整字段."""
        results = []

        for idx, item in enumerate(list_items):
            detail_url = item["detail_url"]
            detail_id = item["detail_id"]

            logger.info(
                f"[{idx + 1}/{len(list_items)}] 爬取详情: {detail_id} - "
                f"{item.get('project_name_list', '未知')[:40]}..."
            )

            html = self._fetch_with_retry(detail_url, is_list_page=False)
            if html is None:
                logger.warning(f"详情页请求失败: {detail_url}")
                self._stats["detail_pages_failed"] += 1
                continue

            self._stats["detail_pages_crawled"] += 1

            try:
                detail_data = self._parse_detail_page(html, detail_url)
                detail_data["trading_place"] = item.get("trading_place", "")
                detail_data["publish_date_list"] = item.get("publish_date_list", "")
                results.append(detail_data)
            except Exception as e:
                logger.error(f"详情解析失败 ({detail_url}): {e}")
                self._stats["detail_pages_failed"] += 1
                continue

            if idx < len(list_items) - 1:
                delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                logger.debug(f"请求间隔 {delay:.1f}s...")
                time.sleep(delay)

        return results

    def _parse_detail_page(self, html: str, detail_url: str) -> Dict:
        """解析详情页HTML, 提取全部字段.

        详情页结构:
          - 标题元素包含项目名称
          - 时间文本显示发布时间 (YYYY-MM-DD HH:MM:SS)
          - HTML表格: <td>标签含字段名</td><td>字段值</td>

        使用 stdlib HTMLParser 解析表格, 无需 BeautifulSoup。
        """
        # 初始化结果字典
        result = {
            "detail_url": detail_url,
            "detail_id": "",
            "project_name": "",
            "project_code": "",
            "project_type": "",
            "total_investment": "",
            "bid_content": "",
            "bid_method": "",
            "bidder_name": "",
            "supervision_dept": "",
            "publish_type": "",
            "publish_unit": "",
            "construction_site": "",
            "construction_scale": "",
            "expected_publish_date": "",
            "publish_time": "",
            "trading_place": "",
            "publish_date_list": "",
        }

        # detail_id from URL
        id_match = re.search(DETAIL_URL_PATTERN, detail_url)
        if id_match:
            result["detail_id"] = id_match.group(1)

        # ---- 1. 提取项目名称 ----
        # 从 <title> 标签提取
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        if title_match:
            title = html_unescape(title_match.group(1).strip())
            # 标题通常格式: "项目名称" 或 "xxx - 项目名称"
            if "山西省公共资源交易" not in title and len(title) > 2:
                result["project_name"] = title

        # ---- 2. 提取发布时间 ----
        # 格式: "发布日期：2026-06-17 16:10" (可能带秒或不带秒)
        time_match = re.search(
            r"发布日期[：:]\s*(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)",
            html,
        )
        if time_match:
            result["publish_time"] = time_match.group(1).replace("/", "-")

        # ---- 3. 提取表格字段 ----
        field_label_map = {
            "投资项目统一代码": "project_code",
            "项目名称": "project_name",
            "项目类型": "project_type",
            "项目总投资": "total_investment",
            "招标内容": "bid_content",
            "招标方式": "bid_method",
            "招标人名称": "bidder_name",
            "行政监督部门": "supervision_dept",
            "发布类型": "publish_type",
            "发布单位": "publish_unit",
            "建设地点": "construction_site",
            "建设内容及规模": "construction_scale",
            "招标公告（资格预审公告） 预计发布时间": "expected_publish_date",
            "招标公告（资格预审公告）预计发布时间": "expected_publish_date",
            "招标公告(资格预审公告) 预计发布时间": "expected_publish_date",
            "招标公告(资格预审公告)预计发布时间": "expected_publish_date",
            "招标公告预计发布时间": "expected_publish_date",
        }

        # 方法A: 使用 TableFieldExtractor 解析
        extractor = TableFieldExtractor(field_label_map)
        try:
            extractor.feed(html)
            table_data = extractor.extract()
            for key, value in table_data.items():
                result[key] = html_unescape(value)
        except Exception as e:
            logger.debug(f"TableFieldExtractor 解析异常: {e}")

        # 方法B: 正则fallback - 直接匹配 <td>标签</td><td>值</td> 模式
        # 适用于 TableFieldExtractor 解析不完全的情况
        if not result.get("project_name") or not result.get("bidder_name"):
            self._parse_detail_page_regex_fallback(html, result, field_label_map)

        # ---- 4. 数据清洗 ----
        result = self._clean_detail_data(result)
        return result

    @staticmethod
    def _parse_detail_page_regex_fallback(
        html: str, result: Dict, field_label_map: Dict[str, str]
    ):
        """正则fallback: 直接匹配 HTML 标签模式提取字段.

        匹配模式: <td ...>标签名</td><td ...>值</td>
        """
        # 提取所有 <td> 内容及其位置
        td_pattern = re.compile(
            r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE
        )
        tds = td_pattern.findall(html)
        # 清理每个td内容: 去除HTML标签和多余空白
        def clean_td(text: str) -> str:
            # 去除嵌套HTML标签
            clean = re.sub(r"<[^>]+>", "", text)
            # 解码HTML实体
            clean = html_unescape(clean)
            # 统一中文标点
            clean = clean.replace("\uff1a", ":").replace("\uff08", "(").replace("\uff09", ")")
            clean = clean.replace("**", "").replace("\u3000", " ").strip()
            # 规范化空白
            clean = re.sub(r"\s{2,}", " ", clean)
            return clean

        cleaned_tds = [clean_td(t) for t in tds]

        # 匹配标签-值对
        already_filled = {k for k, v in result.items() if v}
        for i, td_text in enumerate(cleaned_tds):
            for label, field_key in field_label_map.items():
                if field_key in already_filled:
                    continue
                clean_label = label.replace("\uff08", "(").replace("\uff09", ")").replace("\u3000", " ")
                if clean_label in td_text:
                    for offset in (1, 2):
                        if i + offset < len(cleaned_tds):
                            val = cleaned_tds[i + offset]
                            # 排除值为其他标签的情况
                            is_label = any(
                                l.replace("\uff08", "(").replace("\uff09", ")") in val
                                for l in field_label_map if l != label
                            )
                            if val and not is_label:
                                result[field_key] = html_unescape(val)
                                already_filled.add(field_key)
                                break
                    break

    @staticmethod
    def _clean_detail_data(data: Dict) -> Dict:
        """清洗详情数据: 去除多余空白, 统一日期格式, 处理特殊字符."""
        for key in data:
            if isinstance(data[key], str):
                value = data[key].strip()
                value = re.sub(r"[ \t]+", " ", value)
                value = re.sub(r"\n{3,}", "\n\n", value)
                data[key] = value

        # 总投资: 提取数值部分
        investment = data.get("total_investment", "")
        if investment:
            num_match = re.search(r"[\d.]+", investment)
            if num_match:
                data["total_investment_raw"] = investment
                data["total_investment"] = num_match.group(0)

        # 统一发布时间格式 (支持 YYYY-MM-DD HH:MM 和 YYYY-MM-DD HH:MM:SS)
        publish_time = data.get("publish_time", "")
        if publish_time:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"]:
                try:
                    dt = datetime.strptime(publish_time, fmt)
                    data["publish_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                    break
                except ValueError:
                    continue

        return data

    # ======================== HTTP请求与重试 ========================

    def _init_session(self):
        """初始化HTTP会话."""
        self._session = requests.Session()

    def _build_headers(self, referer: str = BASE_URL) -> Dict:
        """构建请求头, 包含随机UA和Referer."""
        headers = dict(BASE_HEADERS)
        headers["User-Agent"] = random.choice(USER_AGENTS)
        headers["Referer"] = referer
        return headers

    def _fetch_with_retry(self, url: str, is_list_page: bool = False) -> Optional[str]:
        """带重试机制的HTTP GET请求.

        重试策略:
          - 每次重试前切换代理(如有代理池)
          - 指数退避延迟: base, base*2, base*4
          - 特定HTTP状态码(429, 503)触发额外等待
          - 连续失败超过最大重试次数后放弃
        """
        last_error = None

        for attempt in range(MAX_RETRIES + 1):
            proxy = None
            referer = BASE_URL
            if is_list_page and attempt > 0:
                referer = LIST_INDEX_URL

            headers = self._build_headers(referer=referer)
            if self._proxy_pool:
                proxy = self._proxy_pool.get_proxy()

            try:
                resp = self._session.get(
                    url, headers=headers, proxies=proxy, timeout=REQUEST_TIMEOUT,
                )

                if resp.status_code == 200:
                    content = resp.text
                    # 检测空页面或错误页面
                    if len(content) < 500 and ("\u9875\u9762\u627e\u4e0d\u5230" in content or "404" in content):
                        logger.warning(f"\u54cd\u5e94\u5185\u5bb9\u5f02\u5e38: {url}")
                        if proxy and self._proxy_pool:
                            self._proxy_pool.mark_bad(proxy)
                        continue
                    resp.encoding = resp.apparent_encoding or "utf-8"
                    return resp.text

                if resp.status_code == 429:
                    wait_time = (attempt + 1) * 15
                    logger.warning(f"HTTP 429 (速率限制), 等待 {wait_time}s...")
                    time.sleep(wait_time)
                    if proxy and self._proxy_pool:
                        self._proxy_pool.mark_bad(proxy)
                    continue

                if resp.status_code == 503:
                    logger.warning(f"HTTP 503, 尝试: {attempt + 1}/{MAX_RETRIES}")
                    if proxy and self._proxy_pool:
                        self._proxy_pool.mark_bad(proxy)
                    time.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
                    continue

                logger.warning(
                    f"HTTP {resp.status_code}: {url}, 尝试: {attempt + 1}/{MAX_RETRIES}"
                )
                if proxy and self._proxy_pool:
                    self._proxy_pool.mark_bad(proxy)

            except requests.exceptions.Timeout as e:
                last_error = e
                logger.warning(f"请求超时 ({url[:60]}...), 尝试: {attempt + 1}/{MAX_RETRIES}")
                if proxy and self._proxy_pool:
                    self._proxy_pool.mark_bad(proxy)
            except requests.exceptions.ConnectionError as e:
                last_error = e
                logger.warning(f"连接错误, 尝试: {attempt + 1}/{MAX_RETRIES}")
                if proxy and self._proxy_pool:
                    self._proxy_pool.mark_bad(proxy)
            except requests.exceptions.RequestException as e:
                last_error = e
                logger.warning(f"请求异常, 尝试: {attempt + 1}/{MAX_RETRIES}")
                if proxy and self._proxy_pool:
                    self._proxy_pool.mark_bad(proxy)

            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                jitter = wait * 0.3 * (random.random() * 2 - 1)
                actual_wait = wait + jitter
                logger.debug(f"重试等待 {actual_wait:.1f}s...")
                time.sleep(actual_wait)

        logger.error(f"请求最终失败 ({url[:60]}...), 已重试 {MAX_RETRIES} 次: {last_error}")
        return None

    # ======================== 辅助方法 ========================

    def _print_stats(self):
        """打印爬取统计信息."""
        proxy_stats = {}
        if self._proxy_pool:
            proxy_stats = self._proxy_pool.stats()

        logger.info("=" * 60)
        logger.info("  爬取统计")
        logger.info("=" * 60)
        logger.info(f"  列表页爬取:     {self._stats['list_pages_crawled']} 页")
        logger.info(f"  详情页爬取:     {self._stats['detail_pages_crawled']} 条 (成功)")
        logger.info(f"  详情页失败:     {self._stats['detail_pages_failed']} 条")
        logger.info(f"  收集条目数:     {self._stats['items_collected']} 条")
        logger.info(f"  过期跳过:       {self._stats['items_skipped_old']} 条")
        logger.info(f"  日期范围:       最近 {DAYS_LOOKBACK} 天")
        logger.info(f"  代理池状态:     可用 {proxy_stats.get('available', 'N/A')} 个")
        logger.info("=" * 60)
