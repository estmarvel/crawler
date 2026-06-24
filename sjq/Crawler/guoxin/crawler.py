"""
=============================================================================
国信e采购平台 (gx.e-bidding.org) - 核心爬虫
=============================================================================
流程:
  1. 遍历依法/非依法 2 大类
  2. 每类遍历 5 个 tab（招标公告/变更/中标结果/候选人/终止）
  3. 每个 tab 通过 iframe.html 分页爬取列表
  4. 每条公告: 详情页HTML → 提取 file_id → 下载PDF → pdf2text → 字段提取
  5. 复用父目录天启代理IP池，所有请求走代理
=============================================================================
"""

import csv
import io
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_crawler_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _crawler_root not in sys.path:
    sys.path.insert(0, _crawler_root)
from proxy_pool import ProxyPool, ProxyPoolEmptyError

try:
    from . import settings as config
except ImportError:
    import settings as config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PDF 解析库
# ---------------------------------------------------------------------------
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    from PyPDF2 import PdfReader
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    文本清洗工具                                             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _clean_text(text: str) -> str:
    """清洗文本：合并空白、去除多余换行。"""
    if not text:
        return ""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_by_label(text: str, labels: List[str]) -> str:
    """
    从文本中按标签提取值，支持 "标签：值" 或 "标签: 值" 格式。
    值可跨多行，直到遇到下一个章节号或标签为止。
    """
    if not text:
        return ""
    lines = [x.strip() for x in text.splitlines() if x.strip()]

    # 章节号正则: 一、二、三... 或 1. 2. 或 1、2、
    chapter_pattern = re.compile(
        r'^\s*(?:[一二三四五六七八九十]+[、.．]\s*|'
        r'\d+(?:\.\d+)*[、.．]\s*)'
    )

    for idx, line in enumerate(lines):
        for lbl in labels:
            m = re.search(re.escape(lbl) + r'\s*[：:]\s*(.*)$', line)
            if not m:
                continue
            first = m.group(1).strip()
            chunks = [first] if first else []
            for nxt in lines[idx + 1:]:
                if chapter_pattern.match(nxt):
                    break
                # 遇到下一个常见标签则停止
                if re.search(r'(?:招标编号|项目编号|招标项目所在地区|招标条件|项目概况|投标人资格|'
                             r'招标文件的获取|投标文件的递交|开标时间|监督部门|联系方式|'
                             r'中标人|中标价格|公示时间|公示开始|公示结束|'
                             r'一[、.．]|二[、.．]|三[、.．]|四[、.．]|五[、.．]|'
                             r'六[、.．]|七[、.．]|八[、.．]|九[、.．]|十[、.．])', nxt):
                    break
                chunks.append(nxt.strip())
            val = ' '.join(chunks).strip()
            if val:
                return val
    return ""


def _extract_section(text: str, start_labels: List[str],
                     stop_labels: Optional[List[str]] = None) -> str:
    """抽取一个章节的全部文本（从 start 标签到 stop 标签之间）。"""
    if not text:
        return ""
    stop_labels = stop_labels or []
    lines = [x.strip() for x in text.splitlines() if x.strip()]

    start_idx = -1
    for i, line in enumerate(lines):
        for lbl in start_labels:
            if lbl in line:
                start_idx = i
                break
        if start_idx >= 0:
            break
    if start_idx < 0:
        return ""

    chunks = []
    for line in lines[start_idx:]:
        if chunks and any(lbl in line for lbl in stop_labels):
            break
        chunks.append(line)
    return '\n'.join(chunks)


def _extract_contact_block(text: str, party: str,
                           stop_labels: Optional[List[str]] = None) -> str:
    """
    从文本中提取某个主体的联系方式块。
    如：招标人：xxx → 地址：xxx → 联系人：xxx → 电话：xxx
    """
    if not text:
        return ""
    stop_labels = stop_labels or []
    lines = [x.strip() for x in text.splitlines() if x.strip()]

    start_idx = -1
    for i, line in enumerate(lines):
        if party in line:
            start_idx = i
            break
    if start_idx < 0:
        return ""

    chunks = []
    for line in lines[start_idx:]:
        if chunks and any(lbl in line for lbl in stop_labels):
            break
        chunks.append(line)
    return '\n'.join(chunks)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    PDF 文本提取                                             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _pdf_to_text(pdf_bytes: bytes) -> str:
    """
    将 PDF 字节流转为文本。
    先用 pdfplumber，失败/空则用 pdftotext，最后用 PyPDF2。
    """
    if HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                texts = []
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        texts.append(t)
                result = '\n'.join(texts)
                if result.strip():
                    return result
        except Exception as e:
            logger.debug(f"pdfplumber 提取失败: {e}")

    # pdftotext 作为后备（处理 pdfplumber 无法提取的 PDF，如含数字签名的终止公告）
    try:
        proc = subprocess.run(
            ['pdftotext', '-layout', '-', '-'],
            input=pdf_bytes,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            text = proc.stdout.decode('utf-8', errors='replace')
            if text.strip():
                return text
    except Exception as e:
        logger.debug(f"pdftotext 提取失败: {e}")

    if HAS_PYPDF2:
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            texts = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    texts.append(t)
            result = '\n'.join(texts)
            if result.strip():
                return result
        except Exception as e:
            logger.debug(f"PyPDF2 提取失败: {e}")

    return ""


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    字段提取器                                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class FieldExtractor:
    """从 PDF 提取的文本中，按字段集提取结构化数据。"""

    @staticmethod
    def extract(text: str, fields: List[str],
                list_meta: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        主入口：根据字段集从文本中提取所有字段。
        list_meta: 从列表页带来的元数据（标题、招标方式、发布日期等）
        """
        result = {}
        for field in fields:
            result[field] = ""

        # 清洗文本
        text = _clean_text(text)

        # ── 从列表页填充元数据 ──
        if list_meta:
            result["项目名称"] = list_meta.get("title", "")
            result["发布日期"] = list_meta.get("publish_date", "")
            result["发布网站"] = config.PUBLISH_SITE
            result["详情页面"] = list_meta.get("detail_url", "")
            # 项目性质：依法/非依法
            result["项目性质"] = list_meta.get("project_nature", "")
            # 招标方式
            result["招标方式"] = list_meta.get("tender_method", "")
            # 报名截止时间
            result["递交截止时间"] = list_meta.get("deadline", "")

        # ── 标题行信息 ──
        # 招标编号/项目编号
        bid_no = _extract_by_label(text, ["招标编号", "项目编号", "招标项目编号"])
        if bid_no:
            result["项目编号/招标编号"] = bid_no
            result["招标编号/项目编号"] = bid_no

        # 项目地点
        location = _extract_by_label(text, ["招标项目所在地区", "项目地点", "建设地点", "项目所在地区"])
        if location:
            result["项目地点"] = location

        # ── 一、招标条件 ──
        sec1 = _extract_section(
            text,
            start_labels=["一、招标条件", "一、招标条件", "一 招标条件", "1.招标条件", "1、招标条件"],
            stop_labels=["二、", "二  ", "2.", "2、"]
        )
        if sec1:
            result["招标条件/说明"] = sec1
            # 资金来源
            fund = _extract_by_label(sec1, ["资金来源", "项目资金来源", "建设资金"])
            if fund:
                result["资金来源"] = fund
            # 招标人
            procurer = _extract_by_label(sec1, ["招标人", "招标人为", "招标单位", "采购人", "项目业主"])
            if procurer:
                result["招标人/采购人名称"] = procurer
                result["招标人/采购人"] = procurer

        # ── 二、项目概况 ──
        sec2 = _extract_section(
            text,
            start_labels=["二、项目概况", "二、项目概况与招标范围", "二 项目概况"],
            stop_labels=["三、", "三  ", "3.", "3、"]
        )
        if sec2:
            result["项目概况与招标范围"] = sec2
            # 项目规模
            scale = _extract_by_label(sec2, ["项目规模", "建设规模", "工程规模", "规模"])
            if scale:
                result["项目规模"] = scale
            # 工期
            period = _extract_by_label(sec2, ["工期", "服务期", "供货日期", "计划工期", "服务期限", "交货期"])
            if period:
                result["工期/服务期/供货日期"] = period
            # 质量要求
            quality = _extract_by_label(sec2, ["质量要求", "质量标准"])
            if quality:
                result["质量要求"] = quality
            # 招标内容与范围
            scope = _extract_by_label(sec2, ["招标内容与范围", "招标范围", "招标内容", "招标范围及内容"])
            if scope:
                result["招标内容与范围"] = scope

        # ── 三、投标人资格要求 ──
        sec3 = _extract_section(
            text,
            start_labels=["三、投标人资格要求", "三 投标人资格要求"],
            stop_labels=["四、", "四  ", "4.", "4、"]
        )
        if sec3:
            result["申请人资格要求/投标人资格要求"] = sec3

        # ── 四、招标文件的获取 ──
        sec4 = _extract_section(
            text,
            start_labels=["四、招标文件的获取", "四 招标文件的获取"],
            stop_labels=["五、", "五  ", "5.", "5、"]
        )
        if sec4:
            get_time = _extract_by_label(sec4, ["获取时间", "发售时间", "获取招标文件时间", "获取文件时间"])
            if get_time:
                result["招标文件获取时间"] = get_time
                result["预审文件获取时间"] = get_time
            get_method = _extract_by_label(sec4, ["获取方式", "获取方法", "获取招标文件方式", "招标文件获取方式"])
            if get_method:
                result["获取方式"] = get_method

        # ── 五、投标文件的递交 ──
        sec5 = _extract_section(
            text,
            start_labels=["五、投标文件的递交", "五 投标文件的递交"],
            stop_labels=["六、", "六  ", "6.", "6、"]
        )
        if sec5:
            deadline = _extract_by_label(sec5, ["递交截止时间", "投标截止时间", "截止时间", "递交投标文件截止时间"])
            if deadline:
                result["递交截止时间"] = deadline
            method = _extract_by_label(sec5, ["递交方式", "递交方法", "递交地点", "递交地址"])
            if method:
                result["递交方法"] = method

        # ── 六、开标时间及地点 ──
        sec6 = _extract_section(
            text,
            start_labels=["六、开标时间及地点", "六 开标时间及地点"],
            stop_labels=["七、", "七  ", "7.", "7、"]
        )
        if sec6:
            open_time = _extract_by_label(sec6, ["开标时间", "开启时间"])
            if open_time:
                result["开标时间"] = open_time
                result["开启时间"] = open_time
            open_method = _extract_by_label(sec6, ["开标方式", "开启方式"])
            if open_method:
                result["开启方式"] = open_method
            open_place = _extract_by_label(sec6, ["开标地点", "开标地址", "开启地点"])
            if open_place:
                result["开启地点"] = open_place

        # ── 七、保证金 / 评审办法 ──
        sec7 = _extract_section(
            text,
            start_labels=["七、", "七 "],
            stop_labels=["八、", "八  ", "8.", "8、"]
        )
        if sec7:
            guarantee = _extract_by_label(sec7, ["投标保证金", "保证金", "保证金金额", "保证金方式"])
            if guarantee:
                result["投标保证金方式"] = guarantee
            review = _extract_by_label(sec7, ["评审办法", "评标办法", "评标方法", "评审方法"])
            if review:
                result["评审办法"] = review

        # ── 八、监督部门 ──
        sec8 = _extract_section(
            text,
            start_labels=["八、", "八 ", "九、", "九 ", "监督部门"],
            stop_labels=["九、联系方式", "十、联系方式", "九 联系方式", "十 联系方式",
                         "九、", "九 ", "十、", "十 "]
        )
        if sec8:
            supervisor = _extract_by_label(sec8, ["监督部门", "本招标项目的监督部门", "行政监督部门", "监督机构"])
            if supervisor:
                result["监督部门"] = supervisor

        # ── 联系方式（招标人 + 代理机构） ──
        contact_sec = _extract_section(
            text,
            start_labels=["联系方式", "九、联系方式", "十、联系方式",
                          "十一、联系方式", "十二、联系方式"]
        )
        if not contact_sec:
            contact_sec = _extract_section(
                text,
                start_labels=["九、", "十、", "十一、", "十二、"]
            )
            if "联系方式" not in contact_sec:
                contact_sec = ""

        if contact_sec:
            # 招标人
            bidder_block = _extract_contact_block(
                contact_sec,
                party="招标人",
                stop_labels=["招标代理", "招标代理机构", "代理机构", "代理公司"]
            )
            if bidder_block:
                result["招标人地址"] = _extract_by_label(bidder_block, ["地址", "地 址", "联系地址", "详细地址"])
                result["招标人联系人"] = _extract_by_label(bidder_block, ["联系人", "联 系 人"])
                result["招标人联系方式"] = _extract_by_label(bidder_block, ["电话", "电 话", "联系电话", "联系方式"])

            # 招标代理机构
            agent_block = _extract_contact_block(
                contact_sec,
                party="招标代理",
                stop_labels=["招标人", "监督部门", "联系方式", "九、", "十、"]
            )
            if not agent_block:
                agent_block = _extract_contact_block(
                    contact_sec,
                    party="代理机构",
                    stop_labels=["招标人", "监督部门"]
                )
            if agent_block:
                result["招标代理机构"] = _extract_by_label(agent_block, ["名称", "招标代理机构"])
                result["招标代理机构地址"] = _extract_by_label(agent_block, ["地址", "地 址", "联系地址"])
                result["招标代理机构联系人"] = _extract_by_label(agent_block, ["联系人", "联 系 人"])
                result["招标代理机构联系方式"] = _extract_by_label(agent_block, ["电话", "电 话", "联系电话", "联系方式"])

            # 招标代理机构名称兜底
            if not result.get("招标代理机构"):
                agent_name = _extract_by_label(contact_sec, ["招标代理机构", "代理机构"])
                if agent_name:
                    result["招标代理机构"] = agent_name
                    result["招标代理机构(名称)"] = agent_name

        # ── 中标结果相关字段 ──
        # 中标人/中标候选人
        winner = _extract_by_label(text, ["中标人", "中标人名称", "中标单位", "中标供应商"])
        if winner:
            result["中标人名称"] = winner
        price = _extract_by_label(text, ["中标价格", "中标价", "中标金额", "成交金额", "投标报价", "中标候选人报价"])
        if price:
            result["中标价格"] = price
            result["中标候选人报价"] = price
        candidate = _extract_by_label(text, ["中标候选人", "中标候选人名称", "第一中标候选人"])
        if candidate:
            result["中标候选人名称"] = candidate

        # 公示时间
        pub_time = _extract_by_label(text, ["公示时间", "公示期", "公示开始时间", "公告期限"])
        if pub_time:
            result["公示时间"] = pub_time

        # 其他公示内容
        other = _extract_by_label(text, ["其他公示内容", "其他补充事宜", "其他内容"])
        if other:
            result["其他公示内容"] = other

        # ── 列表页元数据补充 ──
        if list_meta:
            result["招标方式"] = list_meta.get("tender_method", "")
            result["招标编号/项目编号"] = result.get("招标编号/项目编号") or result.get("项目编号/招标编号") or ""

        # ── 只保留目标字段集中的字段 ──
        filtered = {}
        for f in fields:
            filtered[f] = result.get(f, "")
        return filtered


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    核心爬虫类                                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class GuoxinCrawler:
    """国信e采购平台爬虫。"""

    def __init__(self, output_dir: Optional[str] = None):
        self._output_dir = output_dir or config.RESULTS_DIR
        self._pool: Optional[ProxyPool] = None
        self._session: Optional[requests.Session] = None
        self._lock = Lock()
        self._stats = {"total_items": 0, "success": 0, "failed": 0}

    # ── 代理 / 会话管理 ──

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.verify = False
            self._session.headers.update({
                "User-Agent": random.choice(config.USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            })
        return self._session

    def _get_proxy(self) -> Dict[str, str]:
        if self._pool is None:
            self._pool = ProxyPool()
        return self._pool.get_proxy()

    def _mark_bad_proxy(self, proxy: Dict[str, str]):
        if self._pool:
            self._pool.mark_bad(proxy)

    # ── HTTP 请求 ──

    def _request(self, url: str, max_retries: int = None) -> Optional[requests.Response]:
        """
        通过代理请求 URL，代理失败时自动换 IP 重试。
        """
        if max_retries is None:
            max_retries = config.PROXY_RETRY_PER_REQUEST
        session = self._get_session()

        for attempt in range(max_retries):
            proxy = None
            try:
                proxy = self._get_proxy()
                resp = session.get(url, timeout=config.REQUEST_TIMEOUT, proxies=proxy)
                if resp.status_code == 200:
                    return resp
                logger.warning(f"HTTP {resp.status_code} for {url[:80]}, retry {attempt + 1}")
                self._mark_bad_proxy(proxy)
            except ProxyPoolEmptyError:
                logger.error("代理池枯竭，无法继续")
                raise
            except Exception as e:
                logger.warning(f"请求失败 [{type(e).__name__}]: {e}, retry {attempt + 1}")
                if proxy:
                    try:
                        self._mark_bad_proxy(proxy)
                    except Exception:
                        pass
            time.sleep(random.uniform(0.5, 1.5))
        return None

    # ── 列表页解析 ──

    def _fetch_iframe_page(self, category_id: str, tender_method: str,
                           tab_name: str, page: int) -> Optional[str]:
        """获取 iframe.html 列表页 HTML。"""
        url = config.IFRAME_URL_TEMPLATE.format(
            category_id=category_id,
            tender_method=tender_method,
            tab_name=tab_name,
            page=page,
        )
        resp = self._request(url)
        if resp:
            resp.encoding = 'utf-8'
            return resp.text
        return None

    def _get_total_pages(self, html: str) -> int:
        """从 HTML 中提取总页数。"""
        m = re.search(r'共\s*<label>\s*(\d+)\s*</label>\s*页', html)
        if m:
            return int(m.group(1))
        return 1

    def _parse_list_items(self, html: str) -> List[Dict[str, str]]:
        """
        解析 iframe.html 中的公告列表。
        返回每条公告的元数据：title, detail_url, bid_no, tender_method, deadline, publish_date
        """
        items = []
        soup = BeautifulSoup(html, 'html.parser')

        # 找到所有公告链接
        for a_tag in soup.find_all('a', class_='clearfix'):
            try:
                href = a_tag.get('href', '')
                title = a_tag.get('title', '')
                if not href or not title:
                    continue

                # 提取招标编号
                bid_no = ""
                bid_no_dd = a_tag.find('dd', string=re.compile(r'招标编号'))
                if bid_no_dd:
                    span = bid_no_dd.find('span')
                    if span:
                        bid_no = span.text.strip()

                # 提取招标方式
                tender_method = ""
                method_dd = a_tag.find('dd', string=re.compile(r'招标方式'))
                if method_dd:
                    span = method_dd.find('span')
                    if span:
                        tender_method = span.text.strip()

                # 提取报名截止时间
                deadline = ""
                deadline_dd = a_tag.find('dd', string=re.compile(r'报名截止时间'))
                if deadline_dd:
                    span = deadline_dd.find('span')
                    if span:
                        deadline = span.text.strip()

                # 提取发布日期
                publish_date = ""
                date_div = a_tag.find('div', class_='newsDate')
                if date_div:
                    inner_div = date_div.find('div')
                    if inner_div:
                        publish_date = inner_div.text.strip()

                items.append({
                    "title": title,
                    "detail_url": href,
                    "bid_no": bid_no,
                    "tender_method": tender_method,
                    "deadline": deadline,
                    "publish_date": publish_date,
                })
            except Exception as e:
                logger.debug(f"解析列表项异常: {e}")

        return items

    # ── 详情页处理 ──

    def _extract_file_id(self, html: str) -> Optional[Tuple[str, str]]:
        """
        从详情页 HTML 中提取 PDF 的 (fileType, file_id)。
        fileType 不同页面类型不同: 2=招标公告, 3=变更, 4=中标结果, 5=候选人, 6=终止
        """
        patterns = [
            r'openFileById\?fileType%3D(\d+)%26id%3D([a-f0-9]+)',
            r'openFileById\?fileType=(\d+)&id=([a-f0-9]+)',
        ]
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                return (m.group(1), m.group(2))
        return None

    def _download_pdf(self, file_type: str, file_id: str) -> Optional[bytes]:
        """通过代理下载 PDF 文件。"""
        url = config.PDF_DOWNLOAD_URL.format(file_type=file_type, file_id=file_id)
        resp = self._request(url)
        if resp and resp.content:
            # 验证是否为 PDF
            if resp.content[:4] == b'%PDF':
                return resp.content
            logger.warning(f"下载的文件不是 PDF: file_id={file_id}")
        return None

    def _process_detail(self, item: Dict[str, str], fields: List[str],
                        index: int, total: int, section_name: str = "") -> Optional[Dict[str, str]]:
        """
        处理单条公告详情：
        1. GET 详情页 HTML → 提取 fileType + file_id
        2. 下载 PDF → 转文本
        3. 解析字段
        """
        detail_url = item["detail_url"]
        logger.info(f"[{index}/{total}] 处理: {item['title'][:50]}...")

        # 获取详情页 HTML
        html_resp = self._request(detail_url)
        if not html_resp:
            logger.warning(f"无法获取详情页: {detail_url}")
            return None

        html_resp.encoding = 'utf-8'
        html = html_resp.text

        # 提取 fileType + file_id
        fid_info = self._extract_file_id(html)
        if not fid_info:
            logger.warning(f"未找到 PDF file_id: {detail_url}")
            return None

        file_type, file_id = fid_info

        # 下载 PDF
        pdf_bytes = self._download_pdf(file_type, file_id)
        if not pdf_bytes:
            logger.warning(f"PDF 下载失败: fileType={file_type}, file_id={file_id}")
            return None

        # PDF → 文本
        text = _pdf_to_text(pdf_bytes)
        if not text:
            logger.warning(f"PDF 文本提取为空: file_id={file_id}")
            return None

        # 提取字段
        list_meta = {
            "title": item["title"],
            "tender_method": item["tender_method"],
            "deadline": item["deadline"],
            "publish_date": item["publish_date"],
            "detail_url": detail_url,
            "project_nature": section_name,  # 依法/非依法
        }
        result = FieldExtractor.extract(text, fields, list_meta)

        with self._lock:
            self._stats["success"] += 1

        return result

    # ── 保存结果 ──

    def _save_results(self, results: List[Dict[str, str]],
                      fields: List[str],
                      output_dir: str,
                      json_name: str, csv_name: str):
        """保存 JSON 和 CSV 文件。"""
        os.makedirs(output_dir, exist_ok=True)

        # JSON
        json_path = os.path.join(output_dir, json_name)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info(f"JSON 已保存: {json_path} ({len(results)} 条)")

        # CSV
        csv_path = os.path.join(output_dir, csv_name)
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in results:
                writer.writerow(row)
        logger.info(f"CSV 已保存: {csv_path} ({len(results)} 条)")

    # ── 爬取入口 ──

    def run(self):
        """主入口：依次爬取依法和非依法。"""
        for sec in config.SECTION_DEFS:
            self._crawl_section(sec)

        logger.info(f"爬取完成! 总计: {self._stats['total_items']}, "
                    f"成功: {self._stats['success']}, "
                    f"失败: {self._stats['failed']}")

    def _crawl_section(self, sec: Dict):
        """
        爬取一个大类（依法/非依法）。
        遍历 5 个 categoryId，每个 tab 分页爬取。
        """
        logger.info(f"{'='*60}")
        logger.info(f"开始爬取: {sec['name']} (tenderMethod={sec['tender_method']})")
        logger.info(f"{'='*60}")

        section_output = os.path.join(self._output_dir, sec["output_dir"])

        for cat_id, cat_info in config.CATEGORY_MAP.items():
            logger.info(f"\n--- {sec['name']} > {cat_info['name']} (categoryId={cat_id}) ---")

            # 1. 先获取第一页，确定总页数
            first_html = self._fetch_iframe_page(
                cat_id, sec["tender_method"], cat_info["name"], page=1
            )
            if not first_html:
                logger.warning(f"无法获取第一页，跳过: {cat_info['name']}")
                continue

            total_pages = self._get_total_pages(first_html)
            logger.info(f"总页数: {total_pages}")

            # 2. 收集所有列表条目（受 MAX_ITEMS_PER_CATEGORY 限制）
            max_items = getattr(config, 'MAX_ITEMS_PER_CATEGORY', None)
            all_items = []
            all_items.extend(self._parse_list_items(first_html))

            for page in range(2, total_pages + 1):
                if max_items and len(all_items) >= max_items:
                    break
                time.sleep(random.uniform(*config.PAGE_TRANSITION_DELAY))
                html = self._fetch_iframe_page(
                    cat_id, sec["tender_method"], cat_info["name"], page=page
                )
                if not html:
                    logger.warning(f"第 {page} 页获取失败，跳过")
                    continue
                items = self._parse_list_items(html)
                all_items.extend(items)
                logger.info(f"第 {page}/{total_pages} 页: 本页 {len(items)} 条，累计 {len(all_items)} 条")

            if max_items and len(all_items) > max_items:
                all_items = all_items[:max_items]

            total = len(all_items)
            logger.info(f"[{cat_info['name']}] 共收集 {total} 条公告")

            with self._lock:
                self._stats["total_items"] += total

            # 3. 并发处理详情
            fields = cat_info["fields"]
            results: List[Dict[str, str]] = []

            with ThreadPoolExecutor(max_workers=config.CONCURRENT_WORKERS) as executor:
                futures = {}
                for i, item in enumerate(all_items, 1):
                    future = executor.submit(
                        self._process_detail, item, fields, i, total, sec["name"]
                    )
                    futures[future] = i

                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except Exception as e:
                        logger.error(f"[{idx}/{total}] 处理异常: {e}")
                        with self._lock:
                            self._stats["failed"] += 1

            # 4. 保存结果
            self._save_results(
                results, fields, section_output,
                cat_info["json"], cat_info["csv"]
            )

            # 栏目间冷却
            time.sleep(random.uniform(*config.CATEGORY_COOLDOWN))