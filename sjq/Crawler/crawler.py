"""
=============================================================================
全国公共资源交易平台（山西省）- 爬虫核心模块
=============================================================================
支持4个栏目: 招标计划、招标资审公告、中标候选人公示、中标结果公示

HTML页面结构(非zbjh栏目):
  <div class="cs_xq_content">
    <div class="div-title2">公告发布时间 / 公示发布时间</div>
    <div class="div-article2">
      <table class="gycq-table"><tr><td> ... 正文内容 ... </td></tr></table>
    </div>
  </div>

  发布日期 + 信息来源 在 cs_xq_content 上方, 以 &nbsp; 分隔

依赖: 仅需 requests (stdlib re + html.parser)
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
    _list_index_url,
    _list_page_url,
    _detail_url,
    _detail_pattern,
)
from proxy_pool import ProxyPool

logger = logging.getLogger(__name__)


# ======================== HTML解析器 ========================

class TableFieldExtractor(HTMLParser):
    """从HTML表格中提取字段名-字段值对."""

    def __init__(self, field_label_map: Dict[str, str]):
        super().__init__()
        self._field_labels = field_label_map
        self.result: Dict[str, str] = {}
        self._in_td = False
        self._td_text = ""
        self._td_texts: List[str] = []

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
            clean = clean.replace("\uff1a", ":").replace("\uff08", "(").replace("\uff09", ")")
            clean = re.sub(r"\s{2,}", " ", clean)
            self._td_texts.append(clean)

    def extract(self):
        for i, td_text in enumerate(self._td_texts):
            for label, field_key in self._field_labels.items():
                clean_label = label.replace("\uff08", "(").replace("\uff09", ")").replace("\u3000", " ")
                if clean_label in td_text:
                    for offset in (1, 2):
                        if i + offset < len(self._td_texts):
                            val = self._td_texts[i + offset]
                            is_other_label = any(
                                l in val for l in self._field_labels if l != label
                            )
                            if val and not is_other_label:
                                self.result[field_key] = val
                                break
                    break
        return self.result


# ======================== Section爬虫类 ========================

class SectionCrawler:
    """单个栏目的爬虫."""

    def __init__(self, section_def: Dict, proxy_pool: ProxyPool = None):
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

    # ======================== 公开接口 ========================

    def crawl(self) -> List[Dict]:
        self._init_session()
        cutoff_datetime = datetime.now() - timedelta(days=DAYS_LOOKBACK)
        # 仅比较日期部分，忽略时分秒（列表页日期无时间）
        cutoff_date = cutoff_datetime.date()
        logger.info("[%s] 开始爬取, 日期过滤: >= %s", self.name,
                     cutoff_date.isoformat())

        list_items = self._crawl_list_pages(cutoff_date)
        logger.info("[%s] 列表阶段完成: 收集到 %d 条", self.name, len(list_items))

        results = self._crawl_detail_pages(list_items)
        logger.info("[%s] 详情完成: 成功 %d 条, 失败 %d 条",
                     self.name, self._stats["detail_pages_crawled"],
                     self._stats["detail_pages_failed"])

        self._print_stats()
        return results

    def stats(self) -> Dict:
        return dict(self._stats)

    # ======================== 列表页爬取 ========================

    def _crawl_list_pages(self, cutoff_date) -> List[Dict]:
        items = []
        list_index_url = _list_index_url(self.path_prefix)
        for page_num in range(1, MAX_LIST_PAGES + 1):
            list_url = _list_page_url(self.path_prefix, page_num) if page_num > 1 \
                       else list_index_url
            logger.info("[%s] --- 列表第 %d 页 ---", self.name, page_num)

            # 第一页用首页作referer，后续页用上一页
            ref = BASE_URL + "/" if page_num == 1 else _list_page_url(self.path_prefix, page_num - 1)
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

        a_block_pattern = re.compile(
            rf'<a\s+[^>]*href="[^"]*?/{self.path_prefix}/(\d+)\.jhtml"[^>]*>'
            r'(.*?)'
            r'</a>',
            re.DOTALL | re.IGNORECASE,
        )

        a_blocks = a_block_pattern.findall(html)
        if not a_blocks:
            logger.warning("[%s] 列表页未找到链接", self.name)
            return items, found_old

        for detail_id, block_text in a_blocks:
            name_match = re.search(
                r'<p[^>]*class="cs_bz_cont"[^>]*>\s*(.*?)\s*</p>',
                block_text, re.DOTALL | re.IGNORECASE,
            )
            title_text = html_unescape(name_match.group(1).strip()) if name_match else ""

            place_match = re.search(
                r'<span>\s*交易场所[：:]\s*([^<]+)\s*</span>',
                block_text, re.IGNORECASE,
            )
            trading_place = place_match.group(1).strip() if place_match else ""

            date_match = re.search(
                r'<span[^>]*class="cs_bz_cont_1_time"[^>]*>\s*(\d{4}/\d{2}/\d{2})\s*</span>',
                block_text, re.IGNORECASE,
            )
            publish_date_str = date_match.group(1) if date_match else ""

            if not title_text:
                continue

            if publish_date_str:
                try:
                    pub_date = datetime.strptime(
                        publish_date_str.replace("/", "-"), "%Y-%m-%d"
                    ).date()
                    if pub_date < cutoff_date:
                        found_old = True
                        self._stats["items_skipped_old"] += 1
                        continue
                except ValueError:
                    pass

            detail_url = urljoin(BASE_URL, f"/{self.path_prefix}/{detail_id}.jhtml")
            items.append({
                "详情ID": detail_id,
                "详情页链接": detail_url,
                "项目名称_列表": title_text,
                "交易场所": trading_place,
                "列表发布日期": publish_date_str,
            })
            self._stats["items_collected"] += 1

        return items, found_old

    # ======================== 详情页爬取 ========================

    def _crawl_detail_pages(self, list_items: List[Dict]) -> List[Dict]:
        results = []
        list_index_url = _list_index_url(self.path_prefix)
        for idx, item in enumerate(list_items):
            detail_url = item["详情页链接"]
            detail_id = item["详情ID"]
            logger.info("[%s] [%d/%d] 爬取详情: %s - %s",
                         self.name, idx + 1, len(list_items),
                         detail_id, item.get("项目名称_列表", "未知")[:40])

            # 用栏目首页作referer，模拟从列表页点进去
            html = self._fetch_with_retry(detail_url, referer=list_index_url)
            if html is None:
                logger.warning("[%s] 详情页请求失败: %s", self.name, detail_url)
                self._stats["detail_pages_failed"] += 1
                continue

            self._stats["detail_pages_crawled"] += 1

            try:
                detail_data = self._parse_detail(html, detail_url, detail_id)
                detail_data["交易场所"] = item.get("交易场所", "")
                detail_data["列表发布日期"] = item.get("列表发布日期", "")
                results.append(detail_data)
            except Exception as e:
                logger.error("[%s] 详情解析失败: %s", self.name, e)
                self._stats["detail_pages_failed"] += 1
                continue

            if idx < len(list_items) - 1:
                delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                time.sleep(delay)

        return results

    def _parse_detail(self, html: str, detail_url: str, detail_id: str) -> Dict:
        if self.key == "zbjh":
            return self._parse_detail_zbjh(html, detail_url, detail_id)
        elif self.key == "gczb":
            return self._parse_detail_gczb(html, detail_url, detail_id)
        elif self.key == "gchxr":
            return self._parse_detail_gchxr(html, detail_url, detail_id)
        elif self.key == "gcgs":
            return self._parse_detail_gcgs(html, detail_url, detail_id)
        return {"详情页链接": detail_url, "详情ID": detail_id}

    # ======================== 共享内容提取 ========================

    @staticmethod
    def _extract_publish_info(html: str) -> Dict[str, str]:
        """提取 cs_xq_content 上方的 发布日期 和 信息来源.

        格式: 发布日期：2026-06-17 18:20 &nbsp;&nbsp;...&nbsp;&nbsp;信息来源：xxx
        """
        result = {"发布时间": "", "信息来源": ""}

        # 发布日期: 先找到 cs_xq_content 之前的"发布日期"行
        cs_pos = html.find("cs_xq_content")
        if cs_pos < 0:
            cs_pos = len(html)

        before_content = html[:cs_pos]
        # 发布日期 + 空格/&nbsp; + 信息来源 可能在同一个闭合tag内
        # 用 DOTALL 匹配跨 HTML 标签
        pub_match = re.search(
            r"发布日期[：:]\s*(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)",
            before_content, re.DOTALL,
        )
        if pub_match:
            result["发布时间"] = pub_match.group(1).replace("/", "-")

        # 信息来源: 单独匹配, 可能在 &nbsp; 后面
        info_match = re.search(
            r"信息来源[：:]\s*([^<&]+)",
            before_content, re.DOTALL,
        )
        if info_match:
            result["信息来源"] = info_match.group(1).strip()
            # 清理可能的 &nbsp;
            result["信息来源"] = re.sub(r"&nbsp;", "", result["信息来源"]).strip()

        return result

    @staticmethod
    def _extract_title_from_content(html: str) -> str:
        """从 div-article2 中提取项目名称(居中的h1或p标签)."""
        cs_start = html.find("cs_xq_content")
        if cs_start < 0:
            return ""
        content_html = html[cs_start:]

        # 方法1: 找 h1 (gchxr用)
        h1_match = re.search(
            r"<h1[^>]*>\s*(.*?)\s*</h1>",
            content_html, re.DOTALL | re.IGNORECASE,
        )
        if h1_match:
            title = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
            if title and len(title) > 4:
                return title

        # 方法2: 找 style="text-align: center; font-size: 18px" 的 p (gczb用)
        p_match = re.search(
            r'<p[^>]*text-align:\s*center[^>]*font-size:\s*1[68]px[^>]*>\s*(.*?)\s*</p>',
            content_html, re.DOTALL | re.IGNORECASE,
        )
        if p_match:
            title = re.sub(r"<[^>]+>", "", p_match.group(1)).strip()
            if title and len(title) > 4:
                return title

        # 方法3: 从纯文本中提取 - 找第一条有意义行(适用于gcgs等嵌入式HTML)
        ctext = SectionCrawler._extract_content_text(html)
        # 去除开头残留 "cs_xq_content">"
        ctext = re.sub(r'^\s*cs_xq_content[^>]*>\s*', '', ctext)
        ctext = ctext.strip()
        lines = [l.strip() for l in ctext.split("\n") if l.strip()]
        for line in lines:
            # 跳过日期/时间/编号/空行
            if re.match(r'^[\d\s\-/:]+$', line):
                continue
            if re.match(r'^[（(]?招标编号', line):
                continue
            if re.match(r'^公示发布时间', line):
                continue
            if re.match(r'^公告发布时间', line):
                continue
            if len(line) > 5:
                return line[:200]
        return ""

    @staticmethod
    def _extract_content_text(html: str) -> str:
        """从 cs_xq_content > div-article2 提取正文纯文本.

        只提取内容区域, 避免页面头尾的 JS/CSS 垃圾。
        """
        # 找到 cs_xq_content 位置
        cs_start = html.find("cs_xq_content")
        if cs_start < 0:
            # fallback: 整页
            body = re.sub(r"<[^>]+>", " ", html)
            body = html_unescape(body)
            body = re.sub(r"\s{3,}", "\n", body)
            return body.strip()[:10000]

        # 从 cs_xq_content 开始
        after_cs = html[cs_start:]

        # 找到 div-article2 内容结束点(下一个大的结构边界)
        # 结束标记: 页面 footer 或 script 块
        end_markers = [
            r'山西省行政审批服务管理局',
            r'投诉举报热线',
            r'备案号：晋ICP备',
        ]
        end_pos = len(after_cs)
        for marker in end_markers:
            pos = after_cs.find(marker)
            if 0 < pos < end_pos:
                end_pos = pos

        content_html = after_cs[:end_pos]

        # 去除HTML标签
        text = re.sub(r"<script[^>]*>.*?</script>", " ", content_html,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_unescape(text)

        # 规范化空白
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 去除开头 "cs_xq_content">" 等 div 标签残留
        text = re.sub(r'^\s*cs_xq_content[^>]*>\s*', '', text)
        text = re.sub(r'^\s*div-title2[^>]*>\s*', '', text)
        text = text.strip()
        # 移除过多连续空行
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)

        return text[:10000]

    @staticmethod
    def _extract_from_body_text(body_text: str, label_pattern: str) -> str:
        """从正文纯文本中用标签模式提取字段值（fallback）。

        label_pattern: 如 r'招标编号[：:]' 或 r'招\s*标\s*人[：:]'
        返回标签后到下一个字段边界前的值。
        """
        # 边界: 下一字段标签 或 句号 或 双换行 或 字符串末尾
        boundary = r'(?:地\s*址|电\s*话|联\s*系|电子邮|项目名|招标项|招标方|招标代|监督部|联系方|办\s*公|传\s*真|邮\s*编|邮\s*箱|开标时|中标人|中标价|。|\n\s*\n|$)'

        m = re.search(
            label_pattern + r'\s*(.{1,200}?)' + boundary,
            body_text, re.DOTALL,
        )
        if m:
            val = m.group(1).strip()
            # 清理尾部标点和空白
            val = re.sub(r'[\s）\)。．，,、;；]+$', '', val).strip()
            # 清理前导
            val = re.sub(r'^[：:为]\s*', '', val)
            if val and len(val) >= 2 and not re.match(r'^[\s或、，。．,（(）)]+$', val):
                return val[:200]
        return ""

    # ===== 栏目1: 招标计划 (zbjh) =====

    def _parse_detail_zbjh(self, html: str, detail_url: str, detail_id: str) -> Dict:
        """招标计划: 表格字段为主."""
        result = {
            "详情页链接": detail_url, "详情ID": detail_id,
            "项目名称": "", "投资项目统一代码": "", "项目类型": "",
            "项目总投资": "", "项目总投资原始文本": "", "招标内容": "",
            "招标方式": "", "招标人名称": "", "行政监督部门": "",
            "发布类型": "", "发布单位": "", "建设地点": "",
            "建设内容及规模": "", "招标公告预计发布时间": "",
            "发布时间": "", "交易场所": "", "列表发布日期": "",
        }

        # 标题: 从title标签提取(去"山西省公共资源交易平台"后缀)
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        if title_match:
            title = html_unescape(title_match.group(1).strip())
            title = re.sub(r"[-–—|]*\s*山西省公共资源交易平台\s*[-–—|]*", "", title).strip()
            if title and len(title) > 2:
                result["项目名称"] = title

        # 发布时间
        pub_info = self._extract_publish_info(html)
        if pub_info["发布时间"]:
            result["发布时间"] = pub_info["发布时间"]

        field_label_map = {
            "投资项目统一代码": "投资项目统一代码",
            "项目名称": "项目名称",
            "项目类型": "项目类型",
            "项目总投资": "项目总投资",
            "招标内容": "招标内容",
            "招标方式": "招标方式",
            "招标人名称": "招标人名称",
            "行政监督部门": "行政监督部门",
            "发布类型": "发布类型",
            "发布单位": "发布单位",
            "建设地点": "建设地点",
            "建设内容及规模": "建设内容及规模",
            "招标公告（资格预审公告） 预计发布时间": "招标公告预计发布时间",
            "招标公告（资格预审公告）预计发布时间": "招标公告预计发布时间",
            "招标公告(资格预审公告) 预计发布时间": "招标公告预计发布时间",
            "招标公告(资格预审公告)预计发布时间": "招标公告预计发布时间",
            "招标公告预计发布时间": "招标公告预计发布时间",
        }

        extractor = TableFieldExtractor(field_label_map)
        try:
            extractor.feed(html)
            table_data = extractor.extract()
            for k, v in table_data.items():
                result[k] = html_unescape(v)
        except Exception:
            pass

        # fallback regex
        if not result.get("项目名称"):
            self._regex_fallback(html, result, field_label_map)

        return self._clean_common(result)

    # ===== 栏目2: 招标/资审公告 (gczb) =====

    def _parse_detail_gczb(self, html: str, detail_url: str, detail_id: str) -> Dict:
        """招标/资审公告."""
        result = {
            "详情页链接": detail_url, "详情ID": detail_id,
            "项目名称": "", "招标编号": "", "发布时间": "",
            "公告发布时间": "", "信息来源": "", "招标项目所在地区": "",
            "监督部门": "", "招标人": "", "招标代理": "",
            "全文文本": "", "交易场所": "", "列表发布日期": "",
        }

        # 标题
        result["项目名称"] = self._extract_title_from_content(html)

        # 发布时间 + 信息来源
        pub_info = self._extract_publish_info(html)
        result["发布时间"] = pub_info["发布时间"]
        result["信息来源"] = pub_info["信息来源"]

        # 公告发布时间
        ann_match = re.search(
            r"公告发布时间[：:]\s*(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)",
            html, re.DOTALL,
        )
        if ann_match:
            result["公告发布时间"] = ann_match.group(1).replace("/", "-")

        # 招标编号
        bh_match = re.search(
            r"招标编号[：:]\s*([^\n<)]+)[)]",
            html, re.DOTALL,
        )
        if bh_match:
            result["招标编号"] = bh_match.group(1).strip()

        # 招标项目所在地区
        area_match = re.search(
            r"招标项目所在地区[：:]\s*([^\n<]+)",
            html, re.DOTALL,
        )
        if area_match:
            result["招标项目所在地区"] = area_match.group(1).strip()

        # 全文文本
        body_text = self._extract_content_text(html)
        result["全文文本"] = body_text

        # === 从全文文本 fallback 提取 ===
        if not result["项目名称"]:
            result["项目名称"] = self._extract_title_from_content(html)
        if not result["招标编号"]:
            result["招标编号"] = self._extract_from_body_text(
                body_text, r'招标编号[：:]'
            )
        if not result["招标项目所在地区"]:
            result["招标项目所在地区"] = self._extract_from_body_text(
                body_text, r'招标项目所在地区[：:]'
            )

        # 监督部门 / 招标人 / 招标代理 — 统一从正文提取(替换HTML提取)
        result["监督部门"] = self._extract_from_body_text(
            body_text, r'监督部门[：:为]'
        )
        result["招标人"] = self._extract_from_body_text(body_text, r'招\s*标\s*人[：:为]')
        # 清理紧凑文本中的 ",招标代理机构为..." 等后续内容
        if result["招标人"]:
            result["招标人"] = re.split(r'[,，]\s*招\s*标\s*代', result["招标人"])[0].strip()
        result["招标代理"] = self._extract_from_body_text(
            body_text, r'(?:招标代理|代理机构)[：:为]'
        )

        return self._clean_common(result)

    # ===== 栏目3: 中标候选人公示 (gchxr) =====

    def _parse_detail_gchxr(self, html: str, detail_url: str, detail_id: str) -> Dict:
        """中标候选人公示."""
        result = {
            "详情页链接": detail_url, "详情ID": detail_id,
            "项目名称": "", "招标编号": "", "发布时间": "",
            "公示发布时间": "", "信息来源": "",
            "公示开始时间": "", "公示结束时间": "",
            "中标候选人表格": "", "监督部门": "",
            "招标人": "", "招标代理": "",
            "全文文本": "", "交易场所": "", "列表发布日期": "",
        }

        # 标题
        result["项目名称"] = self._extract_title_from_content(html)

        # 发布时间 + 信息来源
        pub_info = self._extract_publish_info(html)
        result["发布时间"] = pub_info["发布时间"]
        result["信息来源"] = pub_info["信息来源"]

        # 公示发布时间
        ann_match = re.search(
            r"公示发布时间[：:]\s*(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)",
            html, re.DOTALL,
        )
        if ann_match:
            result["公示发布时间"] = ann_match.group(1).replace("/", "-")

        # 公示时间范围 (HTML优先)
        start_match = re.search(
            r"公示开始时间[：:]\s*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}[\s\d:时]+)",
            html, re.DOTALL,
        )
        if start_match:
            result["公示开始时间"] = start_match.group(1).strip()
        end_match = re.search(
            r"公示结束时间[：:]\s*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}[\s\d:时]+)",
            html, re.DOTALL,
        )
        if end_match:
            result["公示结束时间"] = end_match.group(1).strip()

        # 招标编号 (h2 优先)
        h2_match = re.search(
            r"<h2[^>]*>\s*(?:（|\(?)\s*招标编号[：:]\s*([^）)]+)[）)]?\s*</h2>",
            html, re.DOTALL | re.IGNORECASE,
        )
        if h2_match:
            result["招标编号"] = h2_match.group(1).strip()
        else:
            bh_match = re.search(
                r"招标编号[：:]\s*([^\n<)]+)[)]",
                html, re.DOTALL,
            )
            if bh_match:
                result["招标编号"] = bh_match.group(1).strip()

        # 全文文本
        body_text = self._extract_content_text(html)
        result["全文文本"] = body_text

        # 候选人表格
        table_match = re.search(
            r"(?:中标候选人基本情况|中标候选人名称)(.*?)(?:提出异议|二[、.]\s*提出|$)",
            body_text, re.DOTALL,
        )
        if table_match:
            table_text = table_match.group(0).strip()
            result["中标候选人表格"] = table_text[:3000]

        # === 从全文文本 fallback ===
        if not result["项目名称"]:
            result["项目名称"] = self._extract_from_body_text(
                body_text, r'(?:项目名称|项目编号)[：:]'
            )
            if not result["项目名称"]:
                result["项目名称"] = self._extract_title_from_content(html)

        if not result["招标编号"]:
            result["招标编号"] = self._extract_from_body_text(body_text, r'招标编号[：:]')

        if not result["公示开始时间"] and not result["公示结束时间"]:
            start2 = re.search(
                r"公示开始时间[：:]\s*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}[\s\d:时]+)",
                body_text,
            )
            if start2:
                result["公示开始时间"] = start2.group(1).strip()
            end2 = re.search(
                r"公示结束时间[：:]\s*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}[\s\d:时]+)",
                body_text,
            )
            if end2:
                result["公示结束时间"] = end2.group(1).strip()

        # 公示期限 (另一种格式)
        if not result["公示开始时间"]:
            qx = re.search(r"公示期限[：:]\s*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}[\s\d:时]+)", body_text)
            if qx:
                result["公示开始时间"] = qx.group(1).strip()

        # 监督部门 / 招标人 / 招标代理 — 全部从正文提取
        result["监督部门"] = self._extract_from_body_text(
            body_text, r'(?:监督部门|项目监督人)[：:为]'
        )
        result["招标人"] = self._extract_from_body_text(body_text, r'招\s*标\s*人[：:为]')
        # 招标代理也可能是"代理机构"
        result["招标代理"] = self._extract_from_body_text(
            body_text, r'(?:招标代理[机构]*|代理机构)[：:为]'
        )

        return self._clean_common(result)

    # ===== 栏目4: 中标结果公示 (gcgs) =====

    def _parse_detail_gcgs(self, html: str, detail_url: str, detail_id: str) -> Dict:
        """中标结果公示."""
        result = {
            "详情页链接": detail_url, "详情ID": detail_id,
            "项目名称": "", "招标编号": "", "发布时间": "",
            "公示发布时间": "", "中标人": "", "中标价格": "",
            "中标价格单位": "", "监督部门": "", "招标人": "", "招标代理": "",
            "全文文本": "", "交易场所": "", "列表发布日期": "", "信息来源": "",
        }

        # 标题
        result["项目名称"] = self._extract_title_from_content(html)

        # 发布时间 + 信息来源
        pub_info = self._extract_publish_info(html)
        result["发布时间"] = pub_info["发布时间"]
        result["信息来源"] = pub_info["信息来源"]

        # 公示发布时间
        ann_match = re.search(
            r"公示发布时间[：:]\s*(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)",
            html, re.DOTALL,
        )
        if ann_match:
            result["公示发布时间"] = ann_match.group(1).replace("/", "-")

        # 全文文本
        body_text = self._extract_content_text(html)
        result["全文文本"] = body_text

        # === 所有业务字段从全文文本提取 ===
        if not result["项目名称"]:
            result["项目名称"] = self._extract_title_from_content(html)

        result["招标编号"] = self._extract_from_body_text(body_text, r'招标编号[：:]')
        if not result["招标编号"]:
            # 尝试 项目编号
            result["招标编号"] = self._extract_from_body_text(body_text, r'项目编号[：:]')

        # 中标人
        result["中标人"] = self._extract_from_body_text(body_text, r'中标人[：:]')
        if not result["中标人"]:
            # "选定XXX为该项目的中标人"
            zbr_fallback = re.search(
                r'选定\s*(\S{2,50}?)\s*为该项目的中标人',
                body_text,
            )
            if zbr_fallback:
                result["中标人"] = zbr_fallback.group(1).strip()

        # 中标价格
        price_match = re.search(
            r'(?:中标价格|投标报价)[：:]\s*([\d,.]+\s*(?:万元|元)?)',
            body_text,
        )
        if price_match:
            pv = price_match.group(1).strip()
            if re.search(r'万元', pv):
                result["中标价格"] = re.sub(r'\s*万元', '', pv).strip()
                result["中标价格单位"] = "万元"
            elif re.search(r'元', pv) and not re.search(r'万元', pv):
                result["中标价格"] = re.sub(r'\s*元', '', pv).strip()
                result["中标价格单位"] = "元"
            else:
                result["中标价格"] = pv

        # 监督部门 / 招标人 / 招标代理
        result["监督部门"] = self._extract_from_body_text(
            body_text, r'(?:监督部门|本招标项目的监督部门)[：:为]'
        )
        result["招标人"] = self._extract_from_body_text(body_text, r'招\s*标\s*人[：:为]')
        result["招标代理"] = self._extract_from_body_text(
            body_text, r'(?:招标代理|代理机构)[：:为]'
        )

        return self._clean_common(result)

    # ======================== 辅助 ========================

    @staticmethod
    def _regex_fallback(html: str, result: Dict, field_label_map: Dict[str, str]):
        td_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
        tds = td_pattern.findall(html)

        def clean_td(t):
            c = re.sub(r"<[^>]+>", "", t)
            c = html_unescape(c)
            c = c.replace("\uff1a", ":").replace("\uff08", "(").replace("\uff09", ")")
            c = c.replace("**", "").replace("\u3000", " ").strip()
            return re.sub(r"\s{2,}", " ", c)

        cleaned = [clean_td(t) for t in tds]
        already = {k for k, v in result.items() if v}
        for i, td_text in enumerate(cleaned):
            for label, field_key in field_label_map.items():
                if field_key in already:
                    continue
                cl = label.replace("\uff08", "(").replace("\uff09", ")").replace("\u3000", " ")
                if cl in td_text:
                    for offset in (1, 2):
                        if i + offset < len(cleaned):
                            val = cleaned[i + offset]
                            is_label = any(
                                l.replace("\uff08", "(").replace("\uff09", ")") in val
                                for l in field_label_map if l != label
                            )
                            if val and not is_label:
                                result[field_key] = html_unescape(val)
                                already.add(field_key)
                                break
                    break

    @staticmethod
    def _clean_common(data: Dict) -> Dict:
        for key in data:
            if isinstance(data[key], str):
                value = data[key].strip()
                value = re.sub(r"[ \t]+", " ", value)
                value = re.sub(r"\n{3,}", "\n\n", value)
                data[key] = value
        return data

    # ======================== HTTP ========================

    def _init_session(self):
        self._session = requests.Session()
        # 会话预热: 先访问首页，建立正常浏览会话痕迹
        try:
            headers = self._build_headers(BASE_URL + "/")
            self._session.get(BASE_URL + "/", headers=headers, timeout=15)
            logger.debug("[%s] 会话预热完成", self.name)
        except Exception:
            logger.debug("[%s] 会话预热跳过", self.name)

    def _build_headers(self, referer: str = None) -> Dict:
        headers = dict(BASE_HEADERS)
        headers["User-Agent"] = random.choice(USER_AGENTS)
        # 使用真实Referer链，模拟浏览器导航行为
        if referer:
            headers["Referer"] = referer
        else:
            headers["Referer"] = BASE_URL + "/"
        return headers

    def _fetch_with_retry(self, url: str, referer: str = None) -> Optional[str]:
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            proxy = None
            headers = self._build_headers(referer)
            if self._proxy_pool:
                proxy = self._proxy_pool.get_proxy()

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
                    time.sleep((attempt + 1) * 15)
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
                jitter = wait * 0.3 * (random.random() * 2 - 1)
                time.sleep(wait + jitter)

        logger.error("[%s] 请求最终失败 (%s), 已重试 %d 次: %s",
                     self.name, url[:60], MAX_RETRIES, last_error)
        return None

    def _print_stats(self):
        ps = self._proxy_pool.stats() if self._proxy_pool else {}
        proxy_count = ps.get("available", 0) if isinstance(ps.get("available"), int) else 0
        logger.info("[%s] 统计: 列表=%d页 详情=%d条(成功)/%d条(失败) 收集=%d条 跳过=%d条 代理=%d个",
                     self.name, self._stats["list_pages_crawled"],
                     self._stats["detail_pages_crawled"],
                     self._stats["detail_pages_failed"],
                     self._stats["items_collected"],
                     self._stats["items_skipped_old"],
                     proxy_count)
