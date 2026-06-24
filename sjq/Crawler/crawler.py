"""
=============================================================================
全国公共资源交易平台（山西省）- 爬虫核心模块
=============================================================================
依赖: requests + beautifulsoup4 + lxml (conda 环境)

架构:
  列表页: 顺序请求（请求量小）
  详情页: ThreadPoolExecutor 并发（10 线程 = 10 个独立 IP）
  每个线程绑定一个代理，代理失效时自动替换

字段: 统一的 21 字段 schema，从 HTML 结构 + 全文文本 fallback 提取
=============================================================================
"""

import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from html import unescape as html_unescape
from threading import Lock
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    BASE_URL,
    SECTION_DEFS,
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
    CONCURRENT_WORKERS,
    _list_index_url,
    _list_page_url,
    _detail_url,
    _detail_pattern,
)
from proxy_pool import ProxyPool, ProxyPoolEmptyError

logger = logging.getLogger(__name__)


# ──────────────────── 21 字段 schema ────────────────────

OUTPUT_FIELDS = [
    "公共类型",        # 1
    "项目名称",        # 2
    "所属行业",        # 3
    "组织形式",        # 4
    "开标时间",        # 5
    "标书发售时间",    # 6
    "公告内容",        # 7
    "招标人地址",      # 8
    "招标人联系人",    # 9
    "招标人联系方式",  # 10
    "招标代理机构",    # 11
    "招标代理机构地址",# 12
    "招标代理机构联系人",# 13
    "招标代理机构联系方式",# 14
    "监督部门地址",    # 15
    "监督部门联系人",  # 16
    "监督部门联系方式",# 17
    "依据文件",        # 18
    "依据文号",        # 19
    "发布日期",        # 20
    "发布网站",        # 21
]


def _empty_result(detail_url="", detail_id="") -> Dict:
    """创建空结果字典（21 字段 + 元数据）。"""
    r = {f: "" for f in OUTPUT_FIELDS}
    r["详情页链接"] = detail_url
    r["详情ID"] = detail_id
    r["全文文本"] = ""
    r["交易场所"] = ""
    r["列表发布日期"] = ""
    r["信息来源"] = ""
    return r


# ──────────────────── 正文字段提取器 ────────────────────

class BodyFieldExtractor:
    """从正文纯文本中按标签+边界提取结构化字段。"""

    # 字段边界词（匹配到这些词时停止当前字段的截取）
    BOUNDARY = re.compile(
        r'(?:地\s*址|电\s*话|联\s*系\s*人|电子邮|项目名|招标项'
        r'|招标方|招标代|监督部|开标时|中标人|中标价|投标截'
        r'|获取招标|获取资格|标书发售|招标文件获取|组织形'
        r'|所属行|依据文|批准文|标段|一[、.]|二[、.]|三[、.]'
        r'|四[、.]|五[、.]|。|\n\s*\n|$)'
    )

    @staticmethod
    def extract(body_text: str, label_pattern: str, max_len: int = 500) -> str:
        """从正文中按 label 提取字段值。

        Args:
            body_text: 全文纯文本
            label_pattern: 字段标签正则（如 r'开标时间[：:]'）
            max_len: 取值最大长度

        Returns:
            提取到的值（已清理），失败返回 ""
        """
        m = re.search(
            label_pattern + r'\s*(.+?)'
            r'(?=' + BodyFieldExtractor.BOUNDARY.pattern + r')',
            body_text,
        )
        if not m:
            return ""
        val = m.group(1).strip()
        # 清理尾部标点
        val = re.sub(r'[：:，,、。．\s]+$', '', val)
        val = re.sub(r'^[：:，,、\s]+', '', val)
        if re.match(r'^[\s：:，,、。；;!！?？\'\"\\[\\]【】()（）\-—…·]+$', val):
            return ""
        return val[:max_len]


F = BodyFieldExtractor  # 简写


# ──────────────────── SectionCrawler ────────────────────

class SectionCrawler:
    """单个栏目的爬虫。"""

    def __init__(self, section_def: Dict, proxy_pool: Optional[ProxyPool] = None):
        self.section = section_def
        self.key = section_def["key"]
        self.name = section_def["name"]
        self.path_prefix = section_def["path_prefix"]
        self._proxy_pool = proxy_pool
        self._session = None
        self._stats = {
            "section": self.name,
            "list_pages_crawled": 0,
            "detail_pages_crawled": 0,
            "detail_pages_failed": 0,
            "items_collected": 0,
            "items_skipped_old": 0,
        }
        self._stats_lock = Lock()

    # ======================== 公开接口 ========================

    def crawl(self) -> List[Dict]:
        self._init_session()
        cutoff_date = (datetime.now() - timedelta(days=DAYS_LOOKBACK)).date()
        logger.info("[%s] 开始爬取, 日期过滤: >= %s", self.name, cutoff_date.isoformat())

        # 阶段1: 列表页（顺序）
        list_items = self._crawl_list_pages(cutoff_date)
        logger.info("[%s] 列表阶段完成: 收集到 %d 条", self.name, len(list_items))
        self._stats["items_collected"] = len(list_items)

        if not list_items:
            return []

        # 阶段2: 详情页（并发）
        results = self._crawl_detail_concurrent(list_items)
        logger.info("[%s] 详情完成: 成功 %d, 失败 %d",
                     self.name,
                     self._stats["detail_pages_crawled"],
                     self._stats["detail_pages_failed"])

        self._print_stats()
        return results

    def stats(self) -> Dict:
        return dict(self._stats)

    # ======================== 列表页（顺序） ========================

    def _crawl_list_pages(self, cutoff_date) -> List[Dict]:
        items = []
        list_index_url = _list_index_url(self.path_prefix)
        for page_num in range(1, MAX_LIST_PAGES + 1):
            list_url = _list_page_url(self.path_prefix, page_num) if page_num > 1 \
                       else list_index_url
            logger.info("[%s] --- 列表第 %d 页 ---", self.name, page_num)

            ref = BASE_URL + "/" if page_num == 1 else \
                  _list_page_url(self.path_prefix, page_num - 1)
            html = self._fetch_with_retry(list_url, referer=ref)
            if html is None:
                logger.warning("[%s] 列表第 %d 页请求失败, 跳过", self.name, page_num)
                continue

            self._stats["list_pages_crawled"] += 1
            page_items, page_has_old = self._parse_list_page(html, cutoff_date)
            items.extend(page_items)

            if page_has_old:
                logger.info("[%s] 日期过滤触发: 第 %d 页存在过期记录", self.name, page_num)
                break

            if page_num < MAX_LIST_PAGES:
                delay = random.uniform(*PAGE_TRANSITION_DELAY)
                time.sleep(delay)

        return items

    def _parse_list_page(self, html: str, cutoff_date) -> Tuple[List[Dict], bool]:
        items = []
        found_old = False
        pattern = re.compile(
            rf'<a\s+[^>]*href="[^"]*?/{self.path_prefix}/(\d+)\.jhtml"[^>]*>'
            r'(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        blocks = pattern.findall(html)
        if not blocks:
            logger.warning("[%s] 列表页未找到链接", self.name)
            return items, False

        date_pattern = re.compile(r'(\d{4})[/-](\d{2})[/-](\d{2})')
        for detail_id, link_html in blocks:
            title = re.sub(r'<[^>]+>', '', link_html).strip()
            title = html_unescape(title)
            if not title:
                continue

            # 提取日期
            dm = date_pattern.search(link_html)
            if dm:
                item_date = dm.group(0).replace('/', '-')
                try:
                    d = datetime.strptime(item_date, "%Y-%m-%d").date()
                    if d < cutoff_date:
                        found_old = True
                        continue
                except ValueError:
                    pass
            else:
                item_date = ""

            # 提取交易场所
            place_m = re.search(r'\[(.*?)\]', link_html)
            place = place_m.group(1).strip() if place_m else ""

            detail_url = _detail_url(self.path_prefix, detail_id)
            items.append({
                "详情页链接": detail_url,
                "详情ID": detail_id,
                "项目名称_列表": title,
                "交易场所": place,
                "列表发布日期": item_date,
            })

        return items, found_old

    # ======================== 详情页（并发） ========================

    def _crawl_detail_concurrent(self, list_items: List[Dict]) -> List[Dict]:
        """ThreadPoolExecutor 并发爬取详情页。

        每个线程绑定一个代理 IP，批量处理分配给它的 URL。
        代理失效时线程内部自动换 IP。
        """
        list_index_url = _list_index_url(self.path_prefix)
        results_map: Dict[int, Dict] = {}  # index → result
        failed = 0

        def _worker(assigned_items: List[Tuple[int, Dict]]):
            """一个线程：绑定一个代理，依次处理分配的 URL。"""
            nonlocal failed
            worker_success = 0
            worker_failed = 0

            for idx, item in assigned_items:
                detail_url = item["详情页链接"]
                detail_id = item["详情ID"]

                # 获取代理（每个线程首次调用时获取）
                html = None
                for retry in range(MAX_RETRIES + 1):
                    try:
                        proxy = self._proxy_pool.get_proxy() if self._proxy_pool else None
                    except ProxyPoolEmptyError:
                        logger.warning("[%s] 代理池枯竭, 详情 %s 放弃", self.name, detail_id)
                        worker_failed += 1
                        break

                    try:
                        resp = self._session.get(
                            detail_url,
                            headers=self._build_headers(list_index_url),
                            proxies=proxy,
                            timeout=REQUEST_TIMEOUT,
                        )
                        if resp.status_code == 200:
                            resp.encoding = resp.apparent_encoding or "utf-8"
                            html = resp.text
                            break
                        elif resp.status_code in (429, 503):
                            if proxy and self._proxy_pool:
                                self._proxy_pool.mark_bad(proxy)
                            time.sleep((retry + 1) * 5)
                        else:
                            if proxy and self._proxy_pool:
                                self._proxy_pool.mark_bad(proxy)
                    except (requests.exceptions.Timeout,
                            requests.exceptions.ConnectionError,
                            requests.exceptions.RequestException) as e:
                        if proxy and self._proxy_pool:
                            self._proxy_pool.mark_bad(proxy)
                        time.sleep(RETRY_BACKOFF_BASE * (2 ** retry))

                if html is None:
                    worker_failed += 1
                    continue

                # 解析
                try:
                    result = self._parse_detail(html, detail_url, detail_id)
                    result["交易场所"] = item.get("交易场所", "")
                    result["列表发布日期"] = item.get("列表发布日期", "")
                    result["信息来源"] = result.get("信息来源", "")
                    results_map[idx] = result
                    worker_success += 1
                except Exception as e:
                    logger.error("[%s] 解析失败 %s: %s", self.name, detail_id, e)
                    worker_failed += 1

            # 聚合统计（线程安全）
            with self._stats_lock:
                self._stats["detail_pages_crawled"] += worker_success
                self._stats["detail_pages_failed"] += worker_failed
                nonlocal failed
                failed += worker_failed

        # 将 list_items 均匀分配给 CONCURRENT_WORKERS 个线程
        total = len(list_items)
        chunk_size = max(1, total // CONCURRENT_WORKERS)
        chunks = []
        for w in range(CONCURRENT_WORKERS):
            start = w * chunk_size
            if w == CONCURRENT_WORKERS - 1:
                end = total
            else:
                end = start + chunk_size
            if start >= total:
                break
            chunks.append([(i, list_items[i]) for i in range(start, end)])

        logger.info("[%s] 并发详情: %d 条 → %d 线程 (每线程 ~%d 条)",
                     self.name, total, len(chunks), chunk_size)

        with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
            futures = [executor.submit(_worker, chunk) for chunk in chunks]
            for fut in futures:
                try:
                    fut.result()
                except ProxyPoolEmptyError:
                    logger.critical("[%s] 代理池枯竭 (并发线程)", self.name)
                except Exception as e:
                    logger.error("[%s] 线程异常: %s", self.name, e)

        # 按原序组装结果
        sorted_results = [results_map[i] for i in sorted(results_map)]
        return sorted_results

    # ======================== 详情解析 ========================

    def _parse_detail(self, html: str, detail_url: str, detail_id: str) -> Dict:
        """统一入口：根据栏目类型分发解析。"""
        if self.key == "zbjh":
            return self._parse_detail_zbjh(html, detail_url, detail_id)
        else:
            # gczb / gchxr / gcgs 共用统一的 body 字段提取
            return self._parse_detail_unified(html, detail_url, detail_id)

    def _parse_detail_zbjh(self, html: str, detail_url: str, detail_id: str) -> Dict:
        """招标计划：表格键值对结构。"""
        result = _empty_result(detail_url, detail_id)
        soup = BeautifulSoup(html, "lxml")

        # 标题
        title_tag = soup.select_one("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            title = re.sub(r"[-–—|]*\s*山西省公共资源交易平台\s*[-–—|]*", "", title).strip()
            result["项目名称"] = title or ""

        # 发布日期
        pub_info = self._extract_publish_info(html)
        result["发布日期"] = pub_info["发布日期"] or pub_info.get("发布时间", "")

        # 表格字段（全部映射到 OUTPUT_FIELDS）
        field_map = {
            "投资项目统一代码": "依据文号",
            "项目名称": "项目名称",
            "项目类型": "所属行业",
            "招标内容": "公告内容",
            "招标方式": "组织形式",
            "招标人名称": "招标人",         # zbjh 用"招标人名称"
            "行政监督部门": "监督部门",
            "发布类型": "公共类型",
            "发布单位": "发布网站",
        }
        tds = soup.select("td")
        td_texts = [td.get_text(strip=True) for td in tds]
        for i, td_text in enumerate(td_texts):
            td_text = td_text.replace("\uff1a", ":").replace("\uff08", "(").replace("\uff09", ")")
            for label, target in field_map.items():
                cl = label.replace("\uff08", "(").replace("\uff09", ")")
                if cl in td_text:
                    if i + 1 < len(td_texts):
                        val = td_texts[i + 1]
                        if not any(f.replace("\uff08","(") in val for f in field_map if f != label):
                            result[target] = html_unescape(val)
                        break

        return self._clean_common(result)

    def _parse_detail_unified(self, html: str, detail_url: str, detail_id: str) -> Dict:
        """统一正文解析（gczb / gchxr / gcgs）。"""
        result = _empty_result(detail_url, detail_id)
        soup = BeautifulSoup(html, "lxml")

        # ── 公共类型 ──
        result["公共类型"] = self._detect_notice_type(html)

        # ── 项目名称 ──
        result["项目名称"] = self._extract_title(soup)

        # ── 发布日期 + 信息来源 ──
        pub_info = self._extract_publish_info(html)
        result["发布日期"] = pub_info["发布日期"] or pub_info.get("发布时间", "")
        result["发布网站"] = pub_info.get("信息来源", "")

        # ── 正文纯文本 ──
        body_text = self._extract_body_text(soup)
        result["全文文本"] = body_text
        result["公告内容"] = body_text[:10000]

        # ── 从正文提取各字段 ──
        result["所属行业"] = F.extract(body_text, r'所属行业[：:]')
        result["组织形式"] = F.extract(body_text, r'(?:组织形式|招标方式)[：:]')

        result["开标时间"] = F.extract(body_text, r'(?:开标时间|投标截止时间|投标文件递交.*?截止时间)[：:]')
        result["标书发售时间"] = F.extract(
            body_text, r'(?:标书发售时间|招标文件\s*获取时间|获取招标文件时间|资格预审文件获取时间)[：:]'
        )

        result["依据文件"] = F.extract(
            body_text, r'(?:依据文件|批准文件|审批文件|批复文件)[：:]'
        )
        result["依据文号"] = F.extract(
            body_text, r'(?:依据文号|批准文号|审批文号|批复文号|文\s*号)[：:]'
        )
        if not result["依据文号"]:
            m = re.search(r'[（(]\s*(晋[^）)]{5,30})\s*[）)]', body_text)
            if m:
                result["依据文号"] = m.group(1).strip()

        # ── 联系人信息（招标人/代理/监督） ──
        contacts = self._extract_contacts(body_text)
        result["招标人地址"] = contacts.get("招标人地址", "")
        result["招标人联系人"] = contacts.get("招标人联系人", "")
        result["招标人联系方式"] = contacts.get("招标人联系方式", "")
        result["招标代理机构"] = contacts.get("招标代理机构", "")
        result["招标代理机构地址"] = contacts.get("招标代理机构地址", "")
        result["招标代理机构联系人"] = contacts.get("招标代理机构联系人", "")
        result["招标代理机构联系方式"] = contacts.get("招标代理机构联系方式", "")
        result["监督部门地址"] = contacts.get("监督部门地址", "")
        result["监督部门联系人"] = contacts.get("监督部门联系人", "")
        result["监督部门联系方式"] = contacts.get("监督部门联系方式", "")

        return self._clean_common(result)

    # ======================== 标题提取 ========================

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """从 BeautifulSoup 提取项目名称。

        优先级:
          1. cs_dyk 面包屑链接文本
          2. h1 标签
          3. 居中段落
          4. div-article2 第一段
        """
        # 0. cs_dyk 面包屑（真实标题，格式：项目名称+公告类型）
        dyk = soup.select_one("a.cs_dyk")
        if dyk:
            val = dyk.get_text(strip=True)
            if val and len(val) >= 4:
                return val

        # 1. h1 标签
        h1 = soup.select_one("h1")
        if h1:
            val = h1.get_text(strip=True)
            if val and len(val) >= 4:
                return val

        # 2. 居中段落（font-size 不限）
        for p in soup.select("p[style*='text-align:center']"):
            val = p.get_text(strip=True)
            if val and len(val) >= 4 and len(val) <= 200:
                return val
        for p in soup.select("p[style*='text-align: center']"):
            val = p.get_text(strip=True)
            if val and len(val) >= 4:
                return val

        # 3. div-article2 第一段有意义文字
        article = soup.select_one(".div-article2")
        if article:
            text = article.get_text("\n", strip=True)
            first_line = text.split("\n")[0].strip() if text else ""
            if first_line and len(first_line) >= 4:
                return first_line

        return ""

    # ======================== 公告类型检测 ========================

    @staticmethod
    def _detect_notice_type(html: str) -> str:
        """检测公告的公共类型。

        从全文 HTML（去标签后）中匹配关键词。
        """
        text = re.sub(r'<[^>]+>', '', html)[:8000]
        # 优先匹配明确的关键词
        types = [
            ("招标终止公告", "招标终止公告"),
            ("中止公告", "中止公告"),
            ("终止公告", "终止公告"),
            ("更正公告", "更正公告"),
            ("流标公告", "流标公告"),
            ("废标公告", "废标公告"),
            ("延期公告", "延期公告"),
            ("变更公告", "变更公告"),
            ("澄清公告", "澄清公告"),
            ("招标控制价", "招标控制价"),
            ("最高投标限价", "最高投标限价"),
            ("中标结果公示", "中标结果公示"),
            ("中标候选人公示", "中标候选人公示"),
            ("中标候选人公示更正", "中标候选人公示 更正"),
            ("中标候选人更正", "中标候选人修正"),
            ("评标结果公示", "评标结果公示"),
            ("资格预审公告", "资格预审公告"),
            ("再次公告", "再次公告"),
            ("二次公告", "二次公告"),
            ("补遗", "补遗公告"),
        ]
        for keyword, label in types:
            if keyword in text:
                return label

        # 默认类型
        if "招标公告" in text:
            return "招标公告"
        if "招标资审公告" in text:
            return "招标资审公告"
        if "中标结果" in text:
            return "中标结果公示"
        if "中标候选人" in text:
            return "中标候选人公示"
        return ""

    # ======================== 联系人信息提取 ========================

    @staticmethod
    def _extract_contacts(body_text: str) -> Dict[str, str]:
        """从正文提取招标人/代理/监督部门的地址、联系人、电话。

        正文格式通常为：
            招标人：XXX
            地  址：XXX
            联 系 人：XXX
            电  话：XXX
            招标代理机构：XXX
            地  址：XXX
            ...
            监督部门：XXX
            电  话：XXX
        """
        result = {
            "招标人地址": "", "招标人联系人": "", "招标人联系方式": "",
            "招标代理机构": "", "招标代理机构地址": "",
            "招标代理机构联系人": "", "招标代理机构联系方式": "",
            "监督部门地址": "", "监督部门联系人": "", "监督部门联系方式": "",
        }

        # 按实体分割正文
        sections = re.split(
            r'(?=招\s*标\s*人[：:])|'
            r'(?=招\s*标\s*代\s*理|代理机构[：:])|'
            r'(?=监\s*督\s*部\s*门[：:])|'
            r'(?=本\s*招\s*标\s*项\s*目\s*的\s*监\s*督)',
            body_text,
        )

        for section in sections:
            section = section.strip()
            if not section:
                continue

            # 判断属于哪个实体
            if re.match(r'招\s*标\s*人\s*[：:]', section):
                prefix = "招标人"
            elif re.match(r'(?:招\s*标\s*代\s*理|代理机构)\s*[：:]', section):
                prefix = "招标代理机构"
            elif re.match(r'(?:监\s*督\s*部\s*门|本\s*招\s*标\s*项\s*目\s*的\s*监\s*督)\s*[：:为]', section):
                prefix = "监督部门"
            else:
                continue

            # 提取实体名称（第一行）
            name_m = re.search(r'[\u4e00-\u9fff]{2,40}(?:公司|局|中心|单位|院|处|部|委员会|平台|站|集团|所)',
                             section)
            entity_name = name_m.group(0) if name_m else ""

            if prefix == "招标代理机构" and entity_name:
                result["招标代理机构"] = entity_name

            # 提取各类联系信息
            addr = re.search(r'地\s*址\s*[：:]\s*(\S.{0,120}?)(?=\s*(?:联\s*系|电\s*话|电子邮|邮\s*编|传\s*真|$))',
                           section)
            contact = re.search(r'联\s*系\s*人\s*[：:]\s*(\S.{0,40}?)(?=\s*(?:电\s*话|电子邮|地\s*址|$))',
                              section)
            phone = re.search(r'(?:电\s*话|联系电话|电话\s*传真)\s*[：:]\s*(\S.{0,40}?)(?=\s*(?:电子邮|地\s*址|邮\s*箱|$))',
                            section)

            if addr:
                result[f"{prefix}地址"] = addr.group(1).strip()
            if contact:
                result[f"{prefix}联系人"] = contact.group(1).strip().rstrip("）)")
            if phone:
                result[f"{prefix}联系方式"] = phone.group(1).strip()

        return result

    # ======================== 辅助方法 ========================

    def _extract_body_text(self, soup: BeautifulSoup) -> str:
        """从 BeautifulSoup 提取正文纯文本。

        优先从 .div-article2 提取，限制在 cs_xq_content 到 footer 标记之间。
        """
        # 尝试从 div-article2 取
        article = soup.select_one(".div-article2")
        if article:
            # 移除 script/style
            for tag in article.find_all(["script", "style"]):
                tag.decompose()
            text = article.get_text("\n", strip=False)
            # 规范化空白
            text = re.sub(r' {2,}', ' ', text)
            text = re.sub(r'\n{3,}', '\n\n', text)
            return text.strip()

        # fallback: 从整个 cs_xq_content 提取
        content = soup.select_one(".cs_xq_content")
        if content:
            for tag in content.find_all(["script", "style"]):
                tag.decompose()
            text = content.get_text("\n", strip=False)
            text = re.sub(r' {2,}', ' ', text)
            return text.strip()

        return ""

    @staticmethod
    def _extract_publish_info(html: str) -> Dict[str, str]:
        """提取发布日期和发布网站。"""
        result = {"发布时间": "", "发布日期": "", "信息来源": "", "发布网站": ""}

        date_m = re.search(
            r'(?:发布|公告|公示)\s*(?:日期|时间)\s*[：:]\s*'
            r'(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)',
            html,
        )
        if date_m:
            dt = date_m.group(1).replace("/", "-")
            result["发布日期"] = dt
            result["发布时间"] = dt

        source_m = re.search(
            r'信息来源[：:]\s*([^\n<;]+)',
            html,
        )
        if source_m:
            result["信息来源"] = source_m.group(1).strip()
            result["发布网站"] = result["信息来源"]

        return result

    @staticmethod
    def _clean_common(result: Dict) -> Dict:
        """清理所有字段中的 HTML 实体和多余空白。"""
        for k in list(result.keys()):
            v = result.get(k, "")
            if isinstance(v, str):
                v = html_unescape(v)
                v = re.sub(r'\s{3,}', '  ', v)
                v = v.replace("\u3000", " ")
                result[k] = v.strip()
        return result

    # ======================== 网络请求 ========================

    def _init_session(self):
        self._session = requests.Session()
        ua = random.choice(USER_AGENTS)
        self._session.headers.update({**BASE_HEADERS, "User-Agent": ua})

    def _build_headers(self, referer: str = None) -> Dict:
        h = {
            **{k: v for k, v in BASE_HEADERS.items()},
            "User-Agent": random.choice(USER_AGENTS),
        }
        if referer:
            h["Referer"] = referer
        return h

    def _fetch_with_retry(self, url: str, referer: str = None) -> Optional[str]:
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            proxy = None
            headers = self._build_headers(referer)
            if self._proxy_pool:
                try:
                    proxy = self._proxy_pool.get_proxy()
                except ProxyPoolEmptyError as e:
                    logger.critical("[%s] 代理池枯竭: %s", self.name, e)
                    raise

            try:
                resp = self._session.get(url, headers=headers, proxies=proxy,
                                         timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    content = resp.text
                    if len(content) < 500 and ("页面找不到" in content or "404" in content):
                        if proxy and self._proxy_pool:
                            self._proxy_pool.mark_bad(proxy)
                        continue
                    resp.encoding = resp.apparent_encoding or "utf-8"
                    return resp.text

                if resp.status_code == 429:
                    time.sleep((attempt + 1) * 10)
                    if proxy and self._proxy_pool:
                        self._proxy_pool.mark_bad(proxy)
                    continue
                if resp.status_code == 503:
                    if proxy and self._proxy_pool:
                        self._proxy_pool.mark_bad(proxy)
                    time.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
                    continue

                logger.warning("[%s] HTTP %d: %s", self.name, resp.status_code, url[:60])
                if proxy and self._proxy_pool:
                    self._proxy_pool.mark_bad(proxy)

            except requests.exceptions.Timeout as e:
                last_error = e
                if proxy and self._proxy_pool:
                    self._proxy_pool.mark_bad(proxy)
            except requests.exceptions.ConnectionError as e:
                last_error = e
                if proxy and self._proxy_pool:
                    self._proxy_pool.mark_bad(proxy)
            except requests.exceptions.RequestException as e:
                last_error = e
                if proxy and self._proxy_pool:
                    self._proxy_pool.mark_bad(proxy)

            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                time.sleep(wait + random.uniform(0, 1))

        logger.error("[%s] 请求最终失败 (%s), 已重试 %d 次: %s",
                     self.name, url[:60], MAX_RETRIES, last_error)
        return None

    def _print_stats(self):
        ps = self._proxy_pool.stats() if self._proxy_pool else {}
        proxy_count = ps.get("available", 0)
        api_calls = ps.get("api_calls_used", 0)
        api_limit = ps.get("api_calls_limit", 0)
        logger.info(
            "[%s] 统计: 列表=%d页 详情=%d/%d 收集=%d 跳过=%d 代理=%d个 API=%d/%d",
            self.name,
            self._stats["list_pages_crawled"],
            self._stats["detail_pages_crawled"],
            self._stats["detail_pages_failed"],
            self._stats["items_collected"],
            self._stats["items_skipped_old"],
            proxy_count,
            api_calls, api_limit,
        )
