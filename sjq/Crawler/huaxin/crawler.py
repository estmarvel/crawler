"""
=============================================================================
华新阳光采购平台 (ygcgpt.com) - 核心爬虫
=============================================================================
4 个栏目对应网站导航 + 栏目内子类型区分 + 附件下载 + 天启IP池并发
=============================================================================
"""

import csv
import io
import logging
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape as html_unescape
from threading import Lock
from urllib.parse import urlparse, urlunparse
from typing import Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup
import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_crawler_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _crawler_root not in sys.path:
    sys.path.insert(0, _crawler_root)
from proxy_pool import ProxyPool, ProxyPoolEmptyError

try:
    from . import settings as config
except ImportError:
    import settings as config

logger = logging.getLogger(__name__)


def _safe_int(value, default: int = 0) -> int:
    """把接口返回的 total/pages 等值安全转为 int，避免字符串/None 导致分页判断失效。"""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                     HTML / Text 工具函数                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _clean_html(html: str) -> str:
    """清洗成单行文本：用于最终字段输出。"""
    if not html:
        return ""
    text = html_unescape(str(html))
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;|&ensp;|&emsp;|\xa0', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _clean_html_keep_lines(html: str) -> str:
    """
    清洗成保留换行的文本：用于正文字段抽取。
    之前把 HTML 全部压成一行后，_extract_by_label 会在“招标”等词处误截断，
    例如“招标内容与范围”只抽到“本”。这里保留 p/br/div/li/tr 等块级换行。
    """
    if not html:
        return ""
    text = html_unescape(str(html))
    text = re.sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
    text = re.sub(r'(?i)</\s*(p|div|li|tr|h1|h2|h3|h4|h5|h6)\s*>', '\n', text)
    text = re.sub(r'(?i)<\s*(p|div|li|tr|h1|h2|h3|h4|h5|h6)[^>]*>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;|&ensp;|&emsp;|\xa0', ' ', text)
    lines = [re.sub(r'[ \t]+', ' ', x).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]
    return '\n'.join(lines)


_COMMON_LABELS = [
    "项目规模", "建设规模", "工程规模", "招标编号", "项目编号", "招标内容与范围", "招标范围", "招标内容",
    "获取时间", "获取方法", "获取方式", "发售时间", "文件售价", "标书售价",
    "递交截止时间", "递交方法", "递交方式", "递交地址", "开标时间", "开标方式", "开标地址",
    "开启时间", "开启方式", "开启地点", "投标保证金", "保证金", "联系人", "联 系 人",
    "电话", "电 话", "联系方式", "地址", "详细地址", "邮箱", "电子邮件", "开户行名称", "开户银行", "账号",
    "招标人", "招标代理机构", "监督部门", "行政监督部门",
    "中标人", "中标人名称", "中标价格", "中标价", "中标金额",
    "公示开始时间", "公示结束时间", "发布日期", "浏览次数", "一、评标情况", "一、中标人信息",
]


def _line_is_numbered_boundary(line: str) -> bool:
    """2.2、4、4.1、七、这类章节/小节边界；001第一标段不算。"""
    if not line:
        return False
    return bool(re.match(r'^\s*(?:\d+(?:\.\d+)*[、.．]\s*|[一二三四五六七八九十]+[、.．]\s*)', line))


def _line_starts_common_label(line: str) -> bool:
    if not line:
        return False
    for lbl in _COMMON_LABELS:
        if re.match(r'^\s*(?:\d+(?:\.\d+)*\s*)?' + re.escape(lbl) + r'\s*[：:]', line):
            return True
    return False


def _extract_by_label(text: str, labels: List[str]) -> str:
    """
    从保留换行的正文中按“标签：值”抽取。
    - 支持“2.1 项目规模：xxx”这种带小节号的行；
    - 支持值跨多行，例如电话下一行还有财务电话；
    - 遇到下一个章节号或常见标签时停止，避免“项目规模”串到“2.2 招标编号”。
    """
    if not text:
        return ""
    text = _clean_html_keep_lines(text)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for idx, line in enumerate(lines):
        for lbl in labels:
            # 允许前面有“2.1 ”、“4.2 ”等编号
            m = re.search(r'(?:^|\s)(?:\d+(?:\.\d+)*\s*)?' + re.escape(lbl) + r'\s*[：:]\s*(.*)$', line)
            if not m:
                continue
            first = m.group(1).strip()
            chunks = [first] if first else []
            for nxt in lines[idx + 1:]:
                # 下一行是新章节或新标签，停止；但允许“0351-xxx（财务）”这种电话续行。
                if _line_is_numbered_boundary(nxt) or _line_starts_common_label(nxt):
                    break
                chunks.append(nxt.strip())
            val = re.sub(r'\s+', ' ', ' '.join(chunks)).strip()
            if val:
                return val
    return ""



def _sanitize_phone_text(value: str) -> str:
    """只保留电话字段本身，防止把正文/招标编号等内容拼进来。"""
    if not value:
        return ""
    val = re.sub(r'\s+', ' ', str(value)).strip()
    # 常见越界标记，先硬截断。
    stop_markers = [
        "公示开始时间", "公示结束时间", "发布日期", "浏览次数",
        "招标编号", "招标项目编号", "确定", "中标人如下", "中标候选人",
        "一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、", "十、",
    ]
    cut = len(val)
    for mk in stop_markers:
        idx = val.find(mk)
        if idx > 0:
            cut = min(cut, idx)
    val = val[:cut].strip()
    # 从开头连续取“电话样式”字符；中文只允许财务/传真/总机等电话说明。
    m = re.match(r'^[\d\s、,，;；/\\\-—()（）＋+]*(?:财务|传真|总机|电话|转)?[\d\s、,，;；/\\\-—()（）＋+]*(?:（财务）|\(财务\))?', val)
    if m and m.group(0).strip():
        val = m.group(0).strip()
    return val.strip(' ：:，,；;')


def _is_phone_continuation_line(line: str) -> bool:
    """判断电话下一行是否只是电话号码续行，如 0351-2221744（财务）。"""
    if not line:
        return False
    x = re.sub(r'\s+', '', str(line)).strip()
    if not x or len(x) > 35:
        return False
    if any(k in x for k in ["招标编号", "项目", "确定", "中标", "公示", "评标", "招标人", "代理机构", "联系人", "地址", "邮箱", "开户", "账号"]):
        return False
    # 去掉允许的电话说明后，剩余必须主要是数字和分隔符。
    y = re.sub(r'(财务|传真|总机|电话|转|（|）|\(|\))', '', x)
    return bool(re.fullmatch(r'[\d、,，;；/\\\-—＋+]+', y)) and bool(re.search(r'\d{3,}', y))


def _extract_project_no_and_bid_no(d: dict, text: str) -> str:
    """
    合并“招标项目编号/项目编号”和“招标编号”。

    修复点：
    - 旧逻辑把正文压缩后直接取 label 后的连续英文数字，容易把
      “2.3 招标内容与范围”拼到 HXZB 编号后，形成 HXZB-FW202606082.3。
    - 新逻辑只提取明确的编号模式，并对最终结果去重：
        E 开头项目编号：E1401005129000002378
        华新招标编号：HXZB-FW20260608 / HXCG-HW20260515 等
    """
    vals: List[str] = []

    code_patterns = [
        r'E\d{19}',                    # 招标项目编号，如 E1401005129000002378，固定 E+19位数字
        r'[A-Z]{2,10}-[A-Z]{2}\d{8}',  # 招标编号，如 HXZB-FW20260608、HXCG-HW20260515
    ]

    def add_code(v):
        if v is None:
            return
        raw = FieldExtractors._s(v) if 'FieldExtractors' in globals() else _code(v)
        if not raw:
            return
        raw = _clean_html_keep_lines(raw)
        compact = re.sub(r'\s+', '', raw)
        for pat in code_patterns:
            for m in re.finditer(pat, compact):
                code = m.group(0).strip(' ，,。；;：:（）()')
                if code and code not in vals:
                    vals.append(code)

    # 接口字段优先。
    add_code(d.get("diyProjectNo"))
    add_code(d.get("purDiyCode"))

    # 再从正文中抽取。这里不再直接取“招标编号：”后所有字符，避免章节号污染。
    plain = _clean_html_keep_lines(text or "")
    add_code(plain)

    return "；".join(vals)


def _extract_open_place_only(text: str) -> str:
    """只抽页面明确写出的开标/开启地点，不用“递交地址”兜底。"""
    return _extract_by_label(text, ["开标地点", "开标地址", "开启地点"])

def _extract_section(text: str, start_labels: List[str], stop_labels: Optional[List[str]] = None) -> str:
    """
    抽取一个章节正文，例如：
    start=投标人资格要求，stop=招标文件的获取。
    若起始行只是标题，则不把标题本身放入结果；若起始行有“标签：值”，保留值。
    """
    if not text:
        return ""
    stop_labels = stop_labels or []
    text = _clean_html_keep_lines(text)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    start_idx = -1
    start_label = ""
    for i, line in enumerate(lines):
        for lbl in start_labels:
            if lbl in line:
                start_idx = i
                start_label = lbl
                break
        if start_idx >= 0:
            break
    if start_idx < 0:
        return ""

    chunks = []
    first_line = lines[start_idx]
    m = re.search(re.escape(start_label) + r'\s*[：:]\s*(.*)$', first_line)
    if m and m.group(1).strip():
        chunks.append(m.group(1).strip())

    for line in lines[start_idx + 1:]:
        if any(lbl in line for lbl in stop_labels):
            break
        chunks.append(line)
    return re.sub(r'\s+', ' ', ' '.join(chunks)).strip()


def _extract_block_after(text: str, start_labels: List[str], stop_labels: Optional[List[str]] = None) -> str:
    """从某个联系人块开始抽到下一个主体块，保留换行，方便继续提取地址/联系人/电话。"""
    if not text:
        return ""
    stop_labels = stop_labels or []
    lines = [x.strip() for x in _clean_html_keep_lines(text).splitlines() if x.strip()]
    start_idx = -1
    for i, line in enumerate(lines):
        if any(lbl in line for lbl in start_labels):
            start_idx = i
            break
    if start_idx < 0:
        return ""
    chunks = []
    for line in lines[start_idx:]:
        if chunks and any(lbl in line for lbl in stop_labels):
            break
        chunks.append(line)
    return "\n".join(chunks)


def _extract_contact_field(contact_text: str, party: str, field: str) -> str:
    """
    从“联系方式”文本中按主体抽取地址/联系人/电话。

    关键修正：
    1. 先切“五、联系方式/联系方式”块，避免电话字段吃到正文开头的“公示开始时间”。
    2. 招标人块只到“招标代理机构”之前，不能把代理电话填给招标人。
    3. 代理机构块到邮箱/开户行/账号/公示开始时间/一、评标情况等边界前结束。
    4. 电话优先只取“电话：”当前行及紧邻的电话号码续行。
    """
    if not contact_text:
        return ""

    text = _clean_html_keep_lines(contact_text)
    lines = [x.strip() for x in text.splitlines() if x.strip()]

    # 如果文本里有“联系方式”章节，优先从该章节开始，避免从整篇正文误抽。
    start_idx = 0
    for i, line in enumerate(lines):
        if re.search(r'(?:^[五四三二一十]+[、.．]\s*)?联系方式', line):
            start_idx = i + 1
            break
    lines = lines[start_idx:]

    # 通用正文结束边界：遇到这些说明已经不是联系方式块。
    hard_stop = re.compile(r'^(?:公示开始时间|公示结束时间|发布日期|浏览次数|一[、.．]|二[、.．]|三[、.．]|四[、.．]|五[、.．]|六[、.．]|七[、.．]|八[、.．]|九[、.．]|十[、.．]|十一[、.．]|十二[、.．])')

    def find_block(starts, stops):
        begin = -1
        for i, line in enumerate(lines):
            if any(k in line for k in starts):
                begin = i
                break
        if begin < 0:
            return ""
        chunks = []
        for line in lines[begin:]:
            if chunks and (any(k in line for k in stops) or hard_stop.search(line)):
                break
            chunks.append(line)
        return "\n".join(chunks)

    if party == "agent":
        block = find_block(["招标代理机构", "代理机构"], ["邮箱", "电子邮件", "开户行名称", "开户银行", "账号", "账户"])
        if not block:
            # 如果没有显式代理块，不再回退到整篇正文，防止误抽。
            return ""
    else:
        block = find_block(["招标人", "采购人"], ["招标代理机构", "代理机构"])
        if not block:
            return ""

    block_lines = [x.strip() for x in block.splitlines() if x.strip()]

    def line_value(labels, allow_phone_continuation=False):
        for idx, line in enumerate(block_lines):
            for lbl in labels:
                m = re.search(re.escape(lbl) + r'\s*[：:]\s*(.*)$', line)
                if m:
                    val = m.group(1).strip()
                    chunks = [val] if val else []
                    if allow_phone_continuation:
                        for nxt in block_lines[idx + 1:]:
                            # 只允许纯电话/传真/财务电话续行；遇到任何新标签或正文立刻停止。
                            if _line_starts_common_label(nxt) or hard_stop.search(nxt):
                                break
                            if _is_phone_continuation_line(nxt):
                                chunks.append(nxt.strip())
                                continue
                            break
                    val = re.sub(r'\s+', ' ', ' '.join(chunks)).strip()
                    return _sanitize_phone_text(val) if allow_phone_continuation else val
        return ""

    if field == "name":
        return line_value(["招标代理机构", "代理机构"] if party == "agent" else ["招标人", "采购人"])
    if field == "address":
        return line_value(["招标代理机构地址", "招标人地址", "详细地址", "地址", "地 址"])
    if field == "contact":
        return line_value(["招标代理机构联系人", "招标人联系人", "联系人", "联 系 人"])
    if field == "phone":
        return line_value(["招标代理机构联系方式", "招标人联系方式", "联系方式", "电话", "电 话"], allow_phone_continuation=True)
    return ""



def _collect_label_value(lines: List[str], idx: int, label_regex: str, stop_regex: Optional[str] = None) -> str:
    """从第 idx 行的“标签：值”开始，收集可能跨行的值。"""
    line = lines[idx].strip()
    m = re.search(label_regex + r'\s*[：:]\s*(.*)$', line)
    if not m:
        return ""
    chunks = [m.group(1).strip()] if m.group(1).strip() else []
    stop_regex = stop_regex or r'^(?:\d+[、.．]|[一二三四五六七八九十]+[、.．]|服务周期|服务期限|工期|质量|项目负责人|项目经理|项目总监|相关证书|资格能力|中标候选人|投标报价|报价|二、|三、|四、|五、)'
    for nxt in lines[idx + 1:]:
        nxt = nxt.strip()
        if not nxt:
            continue
        if re.search(stop_regex, nxt):
            break
        chunks.append(nxt)
    return re.sub(r'\s+', ' ', ' '.join(chunks)).strip()


def _extract_hxr_name_price_pairs(text: str) -> Tuple[List[str], List[str]]:
    """
    从候选人公示“一、评标情况”中提取候选人名称和报价。

    修正点：
    - 按“001第一标段/002第二标段/...”切块，保留同一候选人在不同标段重复出现的情况；
    - 一个标段内可能有多行“xxx投标报价：...”，要全部保留；
    - 报价不是简单数字时也完整保留。
    """
    if not text:
        return [], []

    # 注意：不能直接用 _extract_section，因为它会把多行压成一行，导致无法按标段切块。
    raw_lines = [x.strip() for x in _clean_html_keep_lines(text).splitlines() if x.strip()]
    start_idx = 0
    for i, line in enumerate(raw_lines):
        if "评标情况" in line:
            start_idx = i + 1
            break
    end_idx = len(raw_lines)
    for i in range(start_idx, len(raw_lines)):
        if any(lbl in raw_lines[i] for lbl in ["提出异议", "其他公示内容", "监督部门", "联系方式"]):
            end_idx = i
            break
    lines = raw_lines[start_idx:end_idx] or raw_lines

    # 标段行，如：001第一标段：、002 第二标段：
    sec_pat = re.compile(r'^(\d{3}[^：:\n]*?标段)\s*[：:]?\s*$')
    indices = []
    for i, line in enumerate(lines):
        m = sec_pat.search(line)
        if m:
            indices.append((i, m.group(1).strip()))

    blocks = []
    if indices:
        for pos, (idx, sec_name) in enumerate(indices):
            end = indices[pos + 1][0] if pos + 1 < len(indices) else len(lines)
            blocks.append((sec_name, lines[idx + 1:end]))
    else:
        blocks.append(("", lines))

    names: List[str] = []
    prices: List[str] = []
    name_re = re.compile(r'(?:推荐\s*)?(?:第[一二三123]名\s*)?中标候选人(?:名称)?|第[一二三123]中标候选人(?:名称)?')
    # 带前缀的报价行：省管河道...投标报价：54900元/处；或 投标报价：xxx
    price_re = re.compile(r'(.{0,40}?(?:投标报价|投标总报价|响应报价|投标价格|报价))\s*[：:]\s*(.+)$')

    for sec_name, blines in blocks:
        cand_name = ""
        quote_lines: List[str] = []

        for line in blines:
            if re.search(r'中标候选人(基本情况|按照|响应|资格能力|公示)', line):
                continue
            m_name = re.search(r'(?:' + name_re.pattern + r')\s*[：:]\s*(.+)$', line)
            if m_name and not cand_name:
                cand_name = re.sub(r'\s+', ' ', m_name.group(1)).strip(' ；;，,')
                continue

            m_price = price_re.search(line)
            if m_price:
                label = m_price.group(1).strip()
                value = m_price.group(2).strip()
                if label and value:
                    quote_lines.append(f"{label}：{value}")

        if cand_name:
            names.append(f"{sec_name}：{cand_name}" if sec_name else cand_name)
        if quote_lines:
            prices.append(f"{sec_name}：" + "；".join(quote_lines) if sec_name else "；".join(quote_lines))
        elif cand_name:
            prices.append("")

    # 兜底：没有标段块但有常规“推荐中标候选人名称/投标报价”。
    if not names:
        for i, line in enumerate(lines):
            if re.search(r'中标候选人(基本情况|按照|响应|资格能力|公示)', line):
                continue
            m = re.search(r'(?:' + name_re.pattern + r')\s*[：:]\s*(.+)$', line)
            if not m:
                continue
            name = re.sub(r'\s+', ' ', m.group(1)).strip(' ；;，,')
            if not name:
                continue
            quote = ""
            qlines = []
            for nxt in lines[i + 1:i + 12]:
                if re.search(r'^(?:服务周期|服务期限|工期|质量|项目负责人|项目经理|项目总监|相关证书|资格能力|二[、.．]|三[、.．]|四[、.．]|五[、.．]|推荐\s*中标候选人|中标候选人名称)', nxt):
                    break
                mp = price_re.search(nxt)
                if mp:
                    qlines.append(f"{mp.group(1).strip()}：{mp.group(2).strip()}")
            quote = "；".join(qlines)
            names.append(name)
            prices.append(quote)

    return names, prices



def _extract_intro_before_section(text: str, stop_labels: List[str]) -> str:
    """提取结果公示正文开头说明：从正文开头到“一、中标人信息”等章节前。"""
    if not text:
        return ""
    lines = [x.strip() for x in _clean_html_keep_lines(text).splitlines() if x.strip()]
    chunks: List[str] = []
    for line in lines:
        if any(lbl in line for lbl in stop_labels):
            break
        # 跳过明显的页面头部噪声
        if any(noise in line for noise in ["发布日期", "浏览次数", "我要申请"]):
            continue
        chunks.append(line)
    return re.sub(r'\s+', ' ', ' '.join(chunks)).strip()


def _extract_result_name_price_pairs(text: str) -> Tuple[List[str], List[str]]:
    """
    从结果公示“一、中标人信息”中提取中标人和中标价格。

    适配页面格式：
        一、中标人信息
        004第四标段：
        中标人：濮阳市宇飞石油机械设备有限公司
        中标价格：444000元

    同时兼容多标段、多中标人。提不到就返回空列表，不捏造。
    """
    if not text:
        return [], []
    section = _extract_section(text, ["中标人信息"], ["其他公示内容", "监督部门", "联系方式"]) or text
    lines = [x.strip() for x in _clean_html_keep_lines(section).splitlines() if x.strip()]
    names: List[str] = []
    prices: List[str] = []

    def _collect_after_label(idx: int, first_value: str, stop_pat: str) -> str:
        chunks = [first_value.strip()] if first_value and first_value.strip() else []
        for nxt in lines[idx + 1:]:
            if re.search(stop_pat, nxt):
                break
            chunks.append(nxt.strip())
        return re.sub(r'\s+', ' ', ' '.join(chunks)).strip(' ；;，,')

    stop_for_name = r'^(?:中标价格|中标价|中标金额|成交金额|二[、.．]|三[、.．]|四[、.．]|其他公示内容|监督部门|联系方式|\d{3}[^：:]*标段\s*[：:])'
    stop_for_price = r'^(?:中标人|中标单位|中标人名称|二[、.．]|三[、.．]|四[、.．]|其他公示内容|监督部门|联系方式|\d{3}[^：:]*标段\s*[：:])'

    for i, line in enumerate(lines):
        if "中标人信息" in line:
            continue
        m_name = re.search(r'(?:^|\s)(?:中标人|中标单位|中标人名称|中标供应商)\s*[：:]\s*(.*)$', line)
        if m_name:
            val = _collect_after_label(i, m_name.group(1), stop_for_name)
            if val and val not in names:
                names.append(val)
            continue

        m_price = re.search(r'(?:^|\s)(?:中标价格|中标价|中标金额|成交金额)\s*[：:]\s*(.*)$', line)
        if m_price:
            val = _collect_after_label(i, m_price.group(1), stop_for_price)
            if val and val not in prices:
                prices.append(val)
            continue

    return names, prices


def _expand_percent_price_text(price_text: str, source_text: str) -> str:
    """
    结果公示中，接口有时只给 dealPrice=68、unit=%，但正文里写的是
    “参照……标准的68%计取”。当价格只有百分比时，尝试回到正文中找完整报价说明。
    找不到完整说明时保留原值，不捏造。
    """
    if not price_text or not source_text:
        return price_text or ""

    lines = [x.strip() for x in _clean_html_keep_lines(source_text).splitlines() if x.strip()]
    out: List[str] = []

    for item in str(price_text).split("\n"):
        item = item.strip()
        if not item:
            continue

        # 只对“68% / 45.5%”这种短百分比值扩展；金额类不动。
        m = re.fullmatch(r'\d+(?:\.\d+)?\s*%', item)
        if not m:
            out.append(item)
            continue

        pct = re.sub(r'\s+', '', item)
        best = ""
        for line in lines:
            compact = re.sub(r'\s+', '', line)
            if pct not in compact:
                continue
            if any(k in line for k in ["参照", "标准", "计取", "费率", "下浮", "收费", "报价"]):
                # 去掉前面的“中标价格/投标报价”标签，但保留完整说明。
                val = re.sub(r'^.*?(?:中标价格|中标价|中标金额|投标报价|报价)\s*[：:]\s*', '', line).strip()
                best = val or line
                break
        out.append(best if best else item)

    return "\n".join(out)


def _extract_result_other_content(text: str) -> str:
    """提取结果公示“二、其他公示内容”章节。"""
    sec = _extract_section(text, ["其他公示内容"], ["监督部门", "联系方式"])
    return sec


def _extract_result_supervision(text: str) -> str:
    """提取结果公示“三、监督部门”章节中的监督部门名称。"""
    sec = _extract_section(text, ["监督部门"], ["联系方式"])
    if not sec:
        return ""
    val = _extract_by_label(sec, ["本项目监督部门为", "监督部门", "监督部门为"])
    if val:
        return val.rstrip('。')
    sec = re.sub(r'^本项目监督部门为\s*[：:]\s*', '', sec).strip()
    return sec.rstrip('。')

def _proxy_ip(proxy: dict) -> str:
    """从 proxy 字典提取 IP:port 用于日志"""
    if not proxy: return "直连"
    for v in proxy.values():
        if v:
            return v.replace("http://", "").replace("https://", "")
    return "unknown"


def _frontend_base_url() -> str:
    """
    前端页面地址和接口 Origin 可能不是同一个。
    例如本项目接口/Origin 可能配置为 https://www.ygcgpt.com:9998，
    但前端可访问路由是 https://www.ygcgpt.com/#/...。

    优先使用 settings.py 中显式配置的 FRONTEND_BASE_URL / WEB_BASE_URL / SITE_BASE_URL；
    若没有配置，则从 config.BASE_URL 中去掉端口，只保留 scheme + hostname。
    """
    for name in ("FRONTEND_BASE_URL", "WEB_BASE_URL", "SITE_BASE_URL"):
        val = getattr(config, name, "")
        if val:
            return str(val).rstrip("/")

    base = str(getattr(config, "BASE_URL", "") or "").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme and parsed.hostname:
        return urlunparse((parsed.scheme, parsed.hostname, "", "", "", "")).rstrip("/")
    return base


def _code(v) -> str:
    """把接口返回的数字/字符串代码统一成字符串；None 返回空。"""
    if v is None:
        return ""
    return str(v).strip()

def _format_range(start, end, fmt: str = "%s 至 %s") -> str:
    start_s, end_s = _code(start), _code(end)
    if start_s and end_s:
        return fmt % (start_s, end_s)
    return start_s or end_s

def _strip_html_fields(*vals) -> str:
    """把多个 HTML/文本字段合并成保留换行的纯文本，用于兜底正则抽取。"""
    return "\n".join(_clean_html_keep_lines(v) for v in vals if v)

def _service_prefix(service_type: int) -> str:
    """和公共 JS fileobtain(t,e,n,r) 的 n → 服务名前缀保持一致。"""
    return {
        1: "common-service",
        2: "purchase",
        3: "user",
        4: "pay",
        5: "bidding",
        6: "web",
        7: "scatter",
    }.get(service_type, "common-service")

def _normalize_frontend_url(raw_url: str) -> str:
    """还原前端 window.location.protocol + data.url / data.downloadUrl 的效果。"""
    if not raw_url:
        return ""
    raw_url = str(raw_url).strip()
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url
    if raw_url.startswith("//"):
        return f"{config.BASE_URL.split(':', 1)[0]}:{raw_url}"
    if raw_url.startswith("/"):
        return f"{config.BASE_URL.rstrip('/')}{raw_url}"
    return f"{config.BASE_URL.rstrip('/')}/{raw_url.lstrip('/')}"


def _announcement_detail_url(ann_id) -> str:
    ann_id = _code(ann_id)
    return f"{_frontend_base_url()}/#/biddingdetails?annId={ann_id}" if ann_id else ""

def _ensure_output_fields(fields: List[str]) -> List[str]:
    fields = list(fields or [])
    if "详情页面" not in fields:
        fields.append("详情页面")
    return fields

def _clean_output_row(row: dict) -> dict:
    """删除内部控制字段，避免 _subtype_ 出现在最终 JSON/CSV。"""
    return {k: v for k, v in row.items() if k not in {"_subtype_"}}


def _js_subtypes(sec_def: dict) -> List[str]:
    """按前端 JS 逻辑修正子类型列表。

    - 候选公示：JS 中 annClassification=2 统一进入 candidatenotice，
      不拆“定标候选人”。
    - 结果公示：JS 列表前缀只区分 annNature=1/4/5，详情页统一进入
      dealnoticenotice；不包含“合同/履约”独立模板。这里保留：
        zbjg = 中标结果
        gzjg = 更正/撤销中标结果
      并强制过滤掉旧配置中的 htly。
    """
    key = sec_def.get("key")
    if key == "hxr":
        return ["hxr"]
    if key == "gs":
        return ["zbjg", "gzjg"]
    return list(sec_def.get("subtypes", []))


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    附件下载器                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class AttachmentDownloader:
    """
    附件逻辑严格对齐公共 JS：

        fileobtain(t, fileId, n)
        n=5 -> bidding
        GET /bidding/file/query/{fileId}
        t=false -> 打开 data.url
        t=true  -> 打开 data.downloadUrl

    爬虫保留 preview_url 和 download_url：
    - preview_url：与前端 fileobtain(false, fileId, 5) 页面点击行为一致。
    - download_url：用于爬虫实际下载解析，若为空再退回 preview_url。
    """

    def __init__(self, token: str, proxy: Optional[dict] = None, service_type: int = 5):
        self._token = token
        self._proxy = proxy
        self._service_type = service_type

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.verify = False
        s.headers.update({
            "authentication": self._token,
            "Origin": config.BASE_URL,
            "Referer": f"{config.BASE_URL}/",
            "User-Agent": config.USER_AGENTS[0],
        })
        return s

    def _query_file_info(self, file_id: str) -> Optional[dict]:
        """按 JS 的 /{service}/file/query/{fileId} 查询文件元信息。"""
        if not file_id:
            return None
        prefix = _service_prefix(self._service_type)
        url = f"{config.API_BASE}/{prefix}/file/query/{file_id}"
        try:
            r = self._session().get(url, timeout=30, proxies=self._proxy)
            if r.status_code != 200:
                return None
            j = r.json()
            if j.get("code") == 200 and j.get("data"):
                return j["data"]
            return None
        except Exception:
            return None

    def query_file_urls(self, file_id: str) -> Dict[str, str]:
        """
        返回前端预览地址和下载地址。
        不捏造：接口查不到时返回空字符串。
        """
        info = self._query_file_info(file_id)
        if not info:
            return {
                "preview_url": "",
                "download_url": "",
                "raw": {},
            }

        return {
            "preview_url": _normalize_frontend_url(info.get("url") or ""),
            "download_url": _normalize_frontend_url(info.get("downloadUrl") or ""),
            "raw": info,
        }

    def download_text_from_urls(self, preview_url: str, download_url: str) -> Optional[str]:
        """
        实际下载解析时优先使用 downloadUrl；没有 downloadUrl 才使用前端点击用的 url。
        """
        file_url = download_url or preview_url
        if not file_url:
            return None

        try:
            s = self._session()
            r = s.get(file_url, timeout=60, proxies=self._proxy)
            if r.status_code != 200 or len(r.content) < 20:
                return None
            return _extract_file_text(r.content, r.headers.get("Content-Type", ""))
        except Exception:
            return None

    def download_text(self, file_id: str) -> Optional[str]:
        urls = self.query_file_urls(file_id)
        return self.download_text_from_urls(urls.get("preview_url", ""), urls.get("download_url", ""))

    def collect(self, detail: dict) -> List[Dict[str, str]]:
        """
        收集详情中的附件。
        详情页 JS 明确使用 forms.fileId/forms.fileName；
        同时保留对 pdfFile、bidAnnouncementSectionDOS 的兼容，但不会捏造不存在的附件。
        """
        results, seen = [], set()

        candidates = []
        for fid_key in ("fileId", "pdfFile"):
            fid = detail.get(fid_key) or ""
            if fid:
                candidates.append((detail.get("fileName", "附件"), fid))

        for sec in (detail.get("bidAnnouncementSectionDOS") or []):
            fid2 = sec.get("fileId") or sec.get("pdfFile") or ""
            if fid2:
                candidates.append((sec.get("fileName") or sec.get("sectionName") or sec.get("sectionCode") or "标段附件", fid2))

        for name, fid in candidates:
            if not fid or fid in seen:
                continue
            seen.add(fid)
            urls = self.query_file_urls(str(fid))
            txt = self.download_text_from_urls(urls.get("preview_url", ""), urls.get("download_url", ""))
            results.append({
                "name": str(name),
                "fid": str(fid),
                "preview_url": urls.get("preview_url", ""),
                "download_url": urls.get("download_url", ""),
                "text": txt or "",
            })

        return results


def _extract_file_text(data: bytes, ct: str) -> str:
    if data[:4] == b"%PDF" or "pdf" in ct: return _pdf_text(data)
    if ct.startswith("text/") or (data[:2000].count(b'\x00') < len(data[:2000]) * 0.05):
        return data.decode("utf-8", errors="replace")[:50000]
    if ct.startswith("image/"): return "【图片附件，需手动查看】"
    return ""


def _pdf_text(data: bytes) -> str:
    texts = []
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pg in pdf.pages:
                t = pg.extract_text()
                if t: texts.append(t)
        if texts: return "\n".join(texts)
    except Exception: pass
    try:
        from PyPDF2 import PdfReader
        for pg in PdfReader(io.BytesIO(data)).pages:
            t = (pg.extract_text() or "").strip()
            if t: texts.append(t)
        if texts: return "\n".join(texts)
    except Exception: pass
    return f"【PDF文件，大小{len(data)}字节，需手动查看】"


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                   4 栏目内的子类型识别                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _detect_subtype(section_key: str, title: str, detail: Optional[dict] = None) -> str:
    """
    子类型判断按前端详情页 JS 对齐：
    - 列表栏目由 annClassifications 决定；
    - 详情模板由 annClassification/templateType/announcementType/annNature 决定；
    - 标题只作为字段缺失时的兜底。
    """
    d = detail or {}
    title = title or ""

    ann_cls = _code(d.get("annClassification"))
    template_type = _code(d.get("templateType"))
    announcement_type = _code(d.get("announcementType"))
    ann_nature = _code(d.get("annNature"))

    # 招标公告/资格预审栏目：前端 annClassification=1。
    if section_key == "zbgg_zys":
        # JS：announcementType == 3 使用 beforehandnotice，即资格预审公告。
        if announcement_type == "3":
            return "zbys"
        # 兜底：部分备用接口可能字段不完整，只能看标题。
        if not announcement_type and "资格预审" in title:
            return "zbys"
        # invitationnotice/open announcement/stop/custom 都仍属于招标公告大类；
        # 现有字段 schema 没有单独 stop/custom 类型，因此归入 zbgg，缺失字段留空。
        return "zbgg"

    # 候选公示栏目：前端 annClassification=2，详情页统一使用 candidatenotice 模板。
    # JS 中没有“定标候选人”独立模板/独立子类型；标题即使出现“定标候选人”，
    # 也仍然按候选公示 hxr 保存，避免拆出 dbhxr。
    if section_key == "hxr" or ann_cls == "2":
        return "hxr"

    # 结果公示栏目：前端 annClassification=3，详情页统一使用 dealnoticenotice 模板。
    # 列表前缀只按 annNature 区分：1=中标结果，4=更正中标结果，5=撤销中标结果。
    # JS 中没有“合同/履约”独立结果公示模板，因此不再拆 htly。
    #
    # 实际数据里部分正常标题形如“(004标段)中标结果公示”，如果只看 annNature，
    # 容易把“004标段”相关记录误分进更正结果。因此这里先看标题语义：
    # 标题明确“更正/撤销”才进 gzjg；标题明确“中标结果公示”且不含更正/撤销，进 zbjg。
    if section_key == "gs" or ann_cls == "3":
        title_has_correction = any(kw in title for kw in ["更正中标结果", "撤销中标结果", "更正结果", "撤销结果", "更正", "撤销"])
        title_has_normal_result = "中标结果" in title or "中标人" in title
        if title_has_correction:
            return "gzjg"
        if title_has_normal_result:
            return "zbjg"
        if ann_nature in {"4", "5"}:
            return "gzjg"
        return "zbjg"

    return ""



# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                   各子类型字段提取器                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class FieldExtractors:

    @staticmethod
    def _s(v) -> str:
        return str(v).strip() if v is not None and v != "" else ""

    @staticmethod
    def _fmt(v, fmt: str = "yyyy-MM-dd HH:mm") -> str:
        if not v:
            return ""
        # 不在这里强行格式化，保留接口原值，避免误改；仅做字符串化。
        return FieldExtractors._s(v)

    @staticmethod
    def _html(v) -> str:
        return _clean_html(v or "")

    @staticmethod
    def _plain(d: dict) -> str:
        """
        前端详情页标准模板大量字段不是 annContent，而是结构化字段。
        这里把 JS 模板中出现的主要字段都合并成纯文本，供兜底正则使用。
        """
        parts = [
            d.get("annContent"), d.get("annContent2"),
            d.get("bidCondition"),
            d.get("projectOverview"), d.get("bidOverview"),
            d.get("bidQualification"), d.get("consortiumQualification"),
            d.get("acquisitionWay"), d.get("acquisitionOther"),
            d.get("submitWay"), d.get("submitAddress"),
            d.get("openAddress"), d.get("evaluationMethod"),
            d.get("ensureForm"), d.get("objectionWay"),
            d.get("reviewSituation"), d.get("otherAnnContent"),
            d.get("otherContent"), d.get("terminationReason"),
            d.get("mediaName"), d.get("supervisionUnitName"),
            d.get("bidContactInformation"), d.get("contactInformation"),
        ]

        # 中标结果列表字段
        for x in (d.get("bidAnnDealDOS") or []):
            parts.append("中标人：%s 中标价格：%s%s" % (
                FieldExtractors._s(x.get("dealName")),
                FieldExtractors._s(x.get("dealPrice")),
                FieldExtractors._deal_price_unit(x.get("dealPriceUnit")),
            ))

        return _strip_html_fields(*parts)

    @staticmethod
    def _announcement_type(v) -> str:
        return {
            "1": "公开招标",
            "2": "邀请招标",
            "3": "公开预审",
            "4": "其他",
        }.get(_code(v), "")

    @staticmethod
    def _open_way(v) -> str:
        return {
            "1": "远程开标",
            "2": "非远程开标",
        }.get(_code(v), "")

    @staticmethod
    def _open_way_pre(v) -> str:
        return {
            "1": "远程开启",
            "2": "非远程开启",
        }.get(_code(v), "")

    @staticmethod
    def _deal_price_unit(v) -> str:
        return {
            "1": "元",
            "2": "万元",
            "3": "（单价）",
            "4": "%",
            "5": "（其他）",
        }.get(_code(v), "")

    @staticmethod
    def _join_html(*vals) -> str:
        return "\n".join(_clean_html(v) for v in vals if _clean_html(v))

    @staticmethod
    def _contact_text(d: dict) -> str:
        # 联系方式需要保留换行。若专门的联系方式字段为空，则从 annContent 中兜底提取。
        contact = _strip_html_fields(d.get("bidContactInformation"), d.get("contactInformation"))
        return contact or _strip_html_fields(d.get("annContent"), d.get("annContent2"))

    @staticmethod
    def _label_or(txt: str, labels: List[str], *fallbacks) -> str:
        val = _extract_by_label(txt, labels)
        if val:
            return val
        for f in fallbacks:
            if f:
                return FieldExtractors._s(f)
        return ""

    @staticmethod
    def _get_price_text(d: dict) -> str:
        way = _code(d.get("documentPriceWay"))
        if way == "1":
            return "免费"
        if way == "3":
            price = FieldExtractors._s(d.get("documentPrice"))
            return f"每标段 {price}元(人民币)" if price else ""
        if way == "2":
            groups: Dict[str, List[str]] = {}
            for sec in (d.get("bidAnnouncementSectionDOS") or []):
                price = FieldExtractors._s(sec.get("documentPrice"))
                code = FieldExtractors._s(sec.get("sectionCode"))
                if price:
                    groups.setdefault(price, []).append(code)
            return "；".join(
                f"{'、'.join([c for c in codes if c])}标段 {price}元(人民币)"
                for price, codes in groups.items()
            )
        return ""

    # ──── 招标计划 ────
    # 分类代码映射 (来自前端 Vue 源码 projectTypefn)
    _PROJECT_TYPE_MAP = {
        "01": "工业", "02": "国土资源", "03": "房屋市政", "04": "交通",
        "05": "水利", "06": "农业", "07": "广播电视", "08": "能源",
        "09": "文物保护", "10": "林业",
    }
    _TENDER_CONTENT_MAP = {"1": "勘察", "2": "设计", "3": "施工", "4": "监理", "5": "主要设备", "6": "重要材料"}

    @staticmethod
    def extract_zbjh(detail: dict, fields: List[str], atts: List[dict]) -> dict:
        r, d = {f: "" for f in fields}, detail
        r["项目性质"] = "依法"
        r["招标方式"] = {"1": "公开招标", "2": "邀请招标"}.get(_code(d.get("tenderMode")), "")
        r["项目名称"] = FieldExtractors._s(d.get("projectName") or d.get("planTitle"))
        pt_code = FieldExtractors._s(d.get("projectType"))
        r["项目类型"] = FieldExtractors._PROJECT_TYPE_MAP.get(pt_code, pt_code)
        val = FieldExtractors._s(d.get("contributionScale"))
        r["项目总投资"] = (val + "万元") if val else ""
        tc_raw = FieldExtractors._s(d.get("tenderContent"))
        if tc_raw:
            items = [FieldExtractors._TENDER_CONTENT_MAP.get(c.strip(), c.strip()) for c in tc_raw.split(";") if c.strip()]
            r["招标内容"] = "、".join(items)
        r["招标人名称"] = FieldExtractors._s(d.get("legalPerson"))
        r["行政监督部门"] = FieldExtractors._s(d.get("superviseDeptName"))
        r["建设地点"] = FieldExtractors._s(d.get("projectAddress"))
        r["建设内容及规模"] = FieldExtractors._s(d.get("projectScale"))
        r["招标公告（资格预审公告）预计发布时间"] = FieldExtractors._s(d.get("noticePlanSendTime"))
        r["发布日期"] = FieldExtractors._s(d.get("releaseTime") or d.get("createTime"))
        r["发布网站"] = "华新阳光采购平台"
        # 前端 JS 路由使用列表页记录的 annId 作为 planid；
        # 详情接口返回的 planId 可能是 p2047... 这类业务编号，不能拿来拼前端路由。
        route_plan_id = FieldExtractors._s(d.get("_route_planid") or d.get("annId") or d.get("id") or d.get("planId"))
        r["详情页面"] = f"{_frontend_base_url()}/#/biddingplan?planid={route_plan_id}" if route_plan_id else ""
        if d.get("fileId"):
            r["建设内容及规模"] = (r["建设内容及规模"] or "") + f"\n【附件: {d.get('fileName','附件')}】"
        FieldExtractors._append_att(r, atts)
        return r

    # ──── 资格预审 ────
    @staticmethod
    def extract_zbys(detail: dict, fields: List[str], atts: List[dict]) -> dict:
        r, d = {f: "" for f in fields}, detail
        txt = FieldExtractors._plain(detail)
        contact = FieldExtractors._contact_text(d)

        r["项目性质"] = "依法"
        r["项目名称"] = FieldExtractors._s(d.get("annTitle") or d.get("purName"))
        r["所属行业"] = FieldExtractors._s(d.get("industryName"))
        r["组织形式"] = FieldExtractors._s(d.get("annNum"))
        r["开标时间"] = FieldExtractors._s(d.get("submitDeadline")) or _extract_by_label(txt, ["开标时间", "开启时间", "递交截止时间", "投标截止时间"])
        r["项目编号/招标编号"] = _extract_project_no_and_bid_no(d, txt)
        r["项目类型/行业分类"] = FieldExtractors._s(d.get("classificationName") or d.get("industryName"))
        r["项目总投资/估算金额"] = _extract_by_label(txt, ["项目总投资", "估算金额", "项目估算"])
        r["招标金额"] = _extract_by_label(txt, ["招标金额", "最高限价", "最高投标限价"])
        r["资金来源"] = _extract_by_label(txt, ["资金来源", "项目资金来源"])
        r["项目地点"] = FieldExtractors._s(d.get("administrativeName")) or _extract_by_label(txt, ["项目地点", "建设地点", "工程地点"])
        r["招标人/采购人名称"] = FieldExtractors._s(d.get("bidName"))
        r["招标代理机构(名称)"] = FieldExtractors._s(d.get("companyName")) or _extract_by_label(contact, ["招标代理机构", "招标代理", "代理机构"])
        overview = FieldExtractors._join_html(
            ("项目概况：" + FieldExtractors._s(d.get("projectOverview"))) if d.get("projectOverview") else "",
            ("招标内容与范围：" + FieldExtractors._html(d.get("bidOverview"))) if d.get("bidOverview") else "",
        )
        r["项目概况与招标范围"] = overview or _extract_by_label(txt, ["项目概况", "招标范围", "招标内容与范围"]) or _extract_section(txt, ["项目概况和招标范围", "项目概况与招标范围"], ["申请人资格要求", "投标人资格要求", "资格预审文件的获取", "招标文件的获取"])
        qualification = FieldExtractors._join_html(
            d.get("bidQualification"),
            ("联合体资格：" + FieldExtractors._html(d.get("consortiumQualification"))) if _code(d.get("consortiumAllow")) == "1" else "",
        )
        r["申请人资格要求/投标人资格要求"] = qualification or _extract_section(txt, ["申请人资格要求", "投标人资格要求", "资格要求"], ["资格预审文件的获取", "招标文件的获取", "文件的获取"]) or _extract_by_label(txt, ["资格要求", "投标人资格", "申请人资格"])
        r["预审文件获取时间"] = _format_range(d.get("acquisitionStart"), d.get("acquisitionEnd"))
        r["获取方式"] = FieldExtractors._s(d.get("acquisitionWay")) or _extract_by_label(txt, ["获取方法", "获取方式", "发售方式"])
        r["递交截止时间"] = FieldExtractors._s(d.get("submitDeadline"))
        r["递交方法"] = FieldExtractors._s(d.get("submitWay")) or _extract_by_label(txt, ["递交方法", "递交方式"])
        r["开启时间"] = FieldExtractors._s(d.get("submitDeadline"))
        r["开启方式"] = FieldExtractors._open_way_pre(d.get("openWay"))
        r["开启地点"] = _extract_open_place_only(txt)
        r["评审办法"] = FieldExtractors._s(d.get("evaluationMethod"))
        r["投标保证金方式"] = FieldExtractors._s(d.get("ensureForm")) or _extract_section(txt, ["提交投标保证金的形式", "投标保证金", "保证金"], ["提出异议", "其他公告内容", "监督部门", "联系方式"])
        r["招标人地址"] = _extract_contact_field(contact, "bidder", "address")
        r["招标人联系人"] = _extract_contact_field(contact, "bidder", "contact")
        r["招标人联系方式"] = _extract_contact_field(contact, "bidder", "phone")
        r["招标代理机构"] = FieldExtractors._s(d.get("companyName")) or _extract_by_label(contact, ["招标代理机构", "招标代理"])
        r["招标代理机构地址"] = _extract_contact_field(contact, "agent", "address")
        r["招标代理机构联系人"] = _extract_contact_field(contact, "agent", "contact")
        r["招标代理机构联系方式"] = _extract_contact_field(contact, "agent", "phone")
        r["发布日期"] = FieldExtractors._s(d.get("releaseTime") or d.get("createTime"))
        r["发布网站"] = FieldExtractors._s(d.get("mediaName")) or "华新阳光采购平台"
        r["详情页面"] = _announcement_detail_url(d.get("annId"))
        FieldExtractors._append_att(r, atts)
        return r

    # ──── 招标公告 ────
    @staticmethod
    def extract_zbgg(detail: dict, fields: List[str], atts: List[dict]) -> dict:
        r, d = {f: "" for f in fields}, detail
        txt = FieldExtractors._plain(detail)
        contact = FieldExtractors._contact_text(d)

        r["项目性质"] = "依法"
        r["项目名称"] = FieldExtractors._s(d.get("annTitle") or d.get("purName"))
        r["所属行业"] = FieldExtractors._s(d.get("industryName"))
        r["组织形式"] = FieldExtractors._s(d.get("annNum"))
        r["开标时间"] = FieldExtractors._s(d.get("submitDeadline")) or _extract_by_label(txt, ["开标时间", "开标日期", "递交截止时间", "投标截止时间"])
        r["项目编号/招标编号"] = _extract_project_no_and_bid_no(d, txt)
        r["项目类型/行业分类"] = FieldExtractors._s(d.get("classificationName") or d.get("industryName"))
        r["项目总投资/估算金额"] = _extract_by_label(txt, ["项目总投资", "估算金额"])
        r["招标金额"] = _extract_by_label(txt, ["招标金额", "最高限价", "最高投标限价"])
        r["资金来源"] = _extract_by_label(txt, ["资金来源", "项目资金来源"])
        r["项目地点"] = FieldExtractors._s(d.get("administrativeName")) or _extract_by_label(txt, ["项目地点", "建设地点", "工程地点"])
        r["招标人/采购人名称"] = FieldExtractors._s(d.get("bidName"))
        r["招标代理机构(名称)"] = FieldExtractors._s(d.get("companyName")) or _extract_by_label(contact, ["招标代理机构", "招标代理"])
        r["项目规模"] = _extract_by_label(txt, ["项目规模", "建设规模", "工程规模"])
        r["工期/服务期/供货日期"] = _extract_by_label(txt, ["工期", "服务期", "供货期", "计划工期", "交货期"])
        r["质量要求"] = _extract_by_label(txt, ["质量要求", "质量标准"])
        overview_direct = FieldExtractors._join_html(
            ("项目概况：" + FieldExtractors._s(d.get("projectOverview"))) if d.get("projectOverview") else "",
            FieldExtractors._html(d.get("bidOverview")),
        )
        # 部分接口只在 annContent 中有正文，bidOverview 为空；这时从“招标内容与范围”标签向后抽取。
        if len(overview_direct.strip()) <= 2:
            overview_direct = _extract_by_label(txt, ["招标内容与范围", "招标范围", "招标内容"])
        r["招标内容与范围"] = overview_direct
        qualification_direct = FieldExtractors._join_html(
            d.get("bidQualification"),
            ("联合体资格：" + FieldExtractors._html(d.get("consortiumQualification"))) if _code(d.get("consortiumAllow")) == "1" else "",
        )
        if not qualification_direct:
            qualification_direct = _extract_section(txt, ["投标人资格要求", "申请人资格要求", "资格要求"], ["招标文件的获取", "资格预审文件的获取", "文件的获取"])
        r["申请人资格要求/投标人资格要求"] = qualification_direct
        r["招标文件获取时间"] = _format_range(d.get("acquisitionStart"), d.get("acquisitionEnd"))
        r["获取方式"] = FieldExtractors._s(d.get("acquisitionWay")) or _extract_by_label(txt, ["获取方法", "获取方式", "发售方式"])
        r["递交截止时间"] = FieldExtractors._s(d.get("submitDeadline"))
        r["递交方法"] = FieldExtractors._s(d.get("submitWay")) or _extract_by_label(txt, ["递交方法", "递交方式"])
        r["开启时间"] = FieldExtractors._s(d.get("submitDeadline"))
        r["开启方式"] = FieldExtractors._open_way(d.get("openWay"))
        r["开启地点"] = _extract_open_place_only(txt)
        r["评审办法"] = FieldExtractors._s(d.get("evaluationMethod")) or _extract_by_label(txt, ["评审办法", "评标办法", "综合评估法"])
        r["投标保证金方式"] = FieldExtractors._s(d.get("ensureForm")) or _extract_section(txt, ["提交投标保证金的形式", "投标保证金", "保证金"], ["提出异议", "其他公告内容", "监督部门", "联系方式"])
        r["招标人地址"] = _extract_contact_field(contact, "bidder", "address")
        r["招标人联系人"] = _extract_contact_field(contact, "bidder", "contact")
        r["招标人联系方式"] = _extract_contact_field(contact, "bidder", "phone")
        r["招标代理机构"] = FieldExtractors._s(d.get("companyName")) or _extract_by_label(contact, ["招标代理机构", "招标代理"])
        r["招标代理机构地址"] = _extract_contact_field(contact, "agent", "address")
        r["招标代理机构联系人"] = _extract_contact_field(contact, "agent", "contact")
        r["招标代理机构联系方式"] = _extract_contact_field(contact, "agent", "phone")
        r["发布日期"] = FieldExtractors._s(d.get("releaseTime") or d.get("createTime"))
        r["发布网站"] = FieldExtractors._s(d.get("mediaName")) or "华新阳光采购平台"
        r["详情页面"] = _announcement_detail_url(d.get("annId"))
        FieldExtractors._append_att(r, atts)
        return r

    # ──── 中标候选人 ────
    @staticmethod
    def extract_hxr(detail: dict, fields: List[str], atts: List[dict]) -> dict:
        r, d = {f: "" for f in fields}, detail
        txt = FieldExtractors._plain(detail)
        # 候选公示页面的“五、联系方式”有时在 contactInformation，
        # 有时混在 otherAnnContent/annContent 等正文里，所以 contact 兜底用 txt。
        contact = FieldExtractors._contact_text(d) or _extract_section(txt, ["联系方式"], []) or txt

        r["项目性质"] = "依法"
        r["项目名称"] = FieldExtractors._s(d.get("annTitle") or d.get("purName"))
        r["所属行业"] = FieldExtractors._s(d.get("industryName"))
        r["组织形式"] = FieldExtractors._s(d.get("annNum"))
        # 该类页面 JS 模板未提供固定开标时间字段；页面没有就留空，不猜。
        r["开标时间"] = _extract_by_label(txt, ["开标时间", "开标日期"])
        r["公示时间"] = _format_range(d.get("publicityStart"), d.get("publicityEnd")) or _extract_by_label(txt, ["公示时间", "公示开始时间", "公示期"])
        r["招标编号/项目编号"] = _extract_project_no_and_bid_no(d, txt)

        # JS 模板候选人详情主体是 reviewSituation HTML；若接口有结构化数组则优先用结构化数组。
        cand_list = d.get("bidAnnCandidateDOS") or d.get("candidateDOS") or []
        if cand_list:
            r["中标候选人名称"] = "\n".join(
                FieldExtractors._s(x.get("candidateName") or x.get("bidderName") or x.get("name"))
                for x in cand_list if x
            )
            r["中标候选人报价"] = "\n".join(
                FieldExtractors._s(x.get("bidPrice") or x.get("quote") or x.get("price"))
                for x in cand_list if x
            )
        else:
            names, prices = _extract_hxr_name_price_pairs(txt)
            r["中标候选人名称"] = "\n".join(names)
            r["中标候选人报价"] = "\n".join(prices)

        r["招标人/采购人"] = FieldExtractors._s(d.get("bidName"))
        # 页面示例中招标人块没有地址/联系人/电话，保持为空；不要把代理联系人错填给招标人。
        r["招标人地址"] = _extract_contact_field(contact, "bidder", "address")
        r["招标人联系人"] = _extract_contact_field(contact, "bidder", "contact")
        r["招标人联系方式"] = _extract_contact_field(contact, "bidder", "phone")
        r["招标代理机构"] = FieldExtractors._s(d.get("companyName")) or _extract_by_label(contact, ["招标代理机构", "招标代理"])
        r["招标代理机构地址"] = _extract_contact_field(contact, "agent", "address")
        r["招标代理机构联系人"] = _extract_contact_field(contact, "agent", "contact")
        r["招标代理机构联系方式"] = _extract_contact_field(contact, "agent", "phone")
        r["发布日期"] = FieldExtractors._s(d.get("releaseTime") or d.get("createTime"))
        r["发布网站"] = FieldExtractors._s(d.get("mediaName")) or "华新阳光采购平台"
        r["详情页面"] = _announcement_detail_url(d.get("annId"))
        FieldExtractors._append_att(r, atts)
        return r

    # 注意：按前端 JS 逻辑，候选公示 annClassification=2 统一进入 candidatenotice 模板，
    # 不再单独实现“定标候选人”子类型。

    # ──── 中标结果 ────
    @staticmethod
    def extract_zbjg(detail: dict, fields: List[str], atts: List[dict]) -> dict:
        """
        结果公示按 JS dealnoticenotice 模板提取。

        JS 页面结构：
        - 顶部说明：bidCondition；部分接口可能放在 annContent 开头。
        - 一、中标人信息：bidAnnDealDOS 或正文中的“中标人/中标价格”。
        - 二、其他公示内容：otherContent 或正文章节。
        - 三、监督部门：supervisionUnitName 或正文章节。
        - 四、联系方式：bidContactInformation/contactInformation 或正文联系方式章节。
        """
        r, d = {f: "" for f in fields}, detail
        txt = FieldExtractors._plain(detail)
        contact = FieldExtractors._contact_text(d)

        r["项目性质"] = "依法"
        r["项目名称"] = FieldExtractors._s(d.get("annTitle") or d.get("purName"))
        r["所属行业"] = FieldExtractors._s(d.get("industryName"))
        r["组织形式"] = FieldExtractors._s(d.get("annNum"))
        r["招标方式"] = FieldExtractors._announcement_type(d.get("announcementType"))
        if "招标编号/项目编号" in r:
            r["招标编号/项目编号"] = _extract_project_no_and_bid_no(d, txt)

        # 顶部说明：示例为“xxx（招标编号：...），确定004第四标段的中标人如下:”。
        intro = FieldExtractors._html(d.get("bidCondition"))
        if not intro:
            intro = _extract_intro_before_section(txt, ["中标人信息"])
        r["招标条件/说明"] = intro

        # 一、中标人信息。
        # 接口 bidAnnDealDOS 通常更稳定；但百分比/费率类价格可能被接口拆成“68%”，
        # 页面正文会写完整“参照...68%计取”，因此正文价格更长时优先正文价格。
        deals = d.get("bidAnnDealDOS") or []
        text_names, text_prices = _extract_result_name_price_pairs(txt)
        if deals:
            api_names = [FieldExtractors._s(x.get("dealName")) for x in deals if x.get("dealName")]
            api_prices = [
                (FieldExtractors._s(x.get("dealPrice")) + FieldExtractors._deal_price_unit(x.get("dealPriceUnit"))).strip()
                for x in deals if FieldExtractors._s(x.get("dealPrice"))
            ]
            r["中标人名称"] = "\n".join(api_names or text_names)
            use_text_price = False
            if text_prices:
                if not api_prices:
                    use_text_price = True
                elif sum(len(x) for x in text_prices) > sum(len(x) for x in api_prices) + 5:
                    use_text_price = True
                elif any(k in p for p in text_prices for k in ["参照", "标准", "计取", "费率", "下浮"]):
                    use_text_price = True
            r["中标价格"] = "\n".join(text_prices if use_text_price else api_prices)
        else:
            r["中标人名称"] = "\n".join(text_names)
            r["中标价格"] = "\n".join(text_prices)

        # 百分比/费率类报价：接口可能只给“68%”，这里回正文尝试补全“参照...68%计取”。
        r["中标价格"] = _expand_percent_price_text(r.get("中标价格", ""), txt)

        # 兼容旧字段名：如果 settings.py 仍使用“中标价”，也填上。
        if "中标价" in r:
            r["中标价"] = r.get("中标价格", "")

        # 二、其他公示内容。优先接口字段；没有则从正文章节抽。
        other = FieldExtractors._join_html(d.get("otherContent"), d.get("otherAnnContent"))
        if not other:
            other = _extract_result_other_content(txt)
        media = FieldExtractors._s(d.get("mediaName"))
        if other:
            r["其他公示内容"] = other
        elif media:
            r["其他公示内容"] = "发布媒体：" + media

        # 三、监督部门。
        r["监督部门"] = FieldExtractors._s(d.get("supervisionUnitName")) or _extract_result_supervision(txt)

        # 四、联系方式。页面示例中招标人只有名称，无地址/联系人/电话，不能错填代理信息。
        r["招标人/采购人"] = FieldExtractors._s(d.get("bidName")) or _extract_contact_field(contact, "bidder", "name")
        r["招标人地址"] = _extract_contact_field(contact, "bidder", "address")
        r["招标人联系人"] = _extract_contact_field(contact, "bidder", "contact")
        r["招标人联系方式"] = _extract_contact_field(contact, "bidder", "phone")
        r["招标代理机构"] = FieldExtractors._s(d.get("companyName")) or _extract_contact_field(contact, "agent", "name")
        r["招标代理机构地址"] = _extract_contact_field(contact, "agent", "address")
        r["招标代理机构联系人"] = _extract_contact_field(contact, "agent", "contact")
        r["招标代理机构联系方式"] = _extract_contact_field(contact, "agent", "phone")

        # 兼容旧字段名。
        if "依据文件" in r:
            r["依据文件"] = FieldExtractors._s(d.get("purName"))
        if "依据文号" in r:
            r["依据文号"] = FieldExtractors._s(d.get("diyProjectNo") or d.get("purDiyCode"))

        r["发布日期"] = FieldExtractors._s(d.get("releaseTime") or d.get("createTime"))
        r["发布网站"] = media or "华新阳光采购平台"
        r["详情页面"] = _announcement_detail_url(d.get("annId"))
        FieldExtractors._append_att(r, atts)
        return r

    # ──── 更正/撤销结果公示 ────
    @staticmethod
    def extract_gzjg(detail: dict, fields: List[str], atts: List[dict]) -> dict:
        """JS 对 annClassification=3 的更正/撤销结果仍使用 dealnoticenotice 模板。"""
        r = FieldExtractors.extract_zbjg(detail, fields, atts)
        nature = _code(detail.get("annNature"))
        title = detail.get("annTitle") or ""
        public_type = {"4": "更正中标结果", "5": "撤销中标结果"}.get(nature, "")
        if not public_type:
            for kw in ["更正中标结果", "撤销中标结果", "更正结果", "撤销结果"]:
                if kw in title:
                    public_type = kw
                    break
        if "公共类型" in r:
            r["公共类型"] = public_type
        return r

    # ──── 合同与履约 ────
    @staticmethod
    def extract_htly(detail: dict, fields: List[str], atts: List[dict]) -> dict:
        r, d = {f: "" for f in fields}, detail
        txt = FieldExtractors._plain(detail)
        r["项目名称"] = FieldExtractors._s(d.get("annTitle") or d.get("purName"))
        r["项目编号"] = FieldExtractors._s(d.get("diyProjectNo") or d.get("purDiyCode"))
        r["合同名称"] = _extract_by_label(txt, ["合同名称", "合同", "合同标题"])
        r["招标人名称"] = FieldExtractors._s(d.get("bidName"))
        r["中标人名称"] = _extract_by_label(txt, ["中标人", "中标人名称", "供应商", "乙方"])
        r["合同金额"] = _extract_by_label(txt, ["合同金额", "合同价格", "签约合同价"])
        r["合同期限"] = _extract_by_label(txt, ["合同期限", "合同有效期", "期限"])
        r["合同签署时间"] = _extract_by_label(txt, ["合同签署时间", "签订日期", "签约时间"])
        r["合同主要内容"] = _extract_by_label(txt, ["合同主要内容", "合同内容", "主要条款"]) or FieldExtractors._join_html(d.get("annContent"), d.get("otherContent"))
        r["发布日期"] = FieldExtractors._s(d.get("releaseTime") or d.get("createTime"))
        r["发布网站"] = FieldExtractors._s(d.get("mediaName")) or "华新阳光采购平台"
        r["详情页面"] = _announcement_detail_url(d.get("annId"))
        FieldExtractors._append_att(r, atts)
        return r

    @staticmethod
    def _append_att(r: dict, atts: List[dict]):
        texts = []
        for a in atts or []:
            txt = a.get("text") or ""
            if txt and len(txt) > 20:
                name = a.get("name") or a.get("fid") or "附件"
                texts.append(f"【{name}】\n{txt}")
        if not texts:
            return
        block = "\n\n========== 附件内容 ==========\n" + "\n---\n".join(texts)
        for k in ["公告内容", "建设内容及规模", "项目概况与招标范围", "招标内容与范围", "合同主要内容"]:
            if k in r:
                r[k] = (r[k] or "") + block
                return
        for k, v in r.items():
            if isinstance(v, str) and not v:
                r[k] = block
                return

    _EXTRACTORS = {
        "zbjh": extract_zbjh, "zbys": extract_zbys, "zbgg": extract_zbgg,
        "hxr": extract_hxr, "zbjg": extract_zbjg,
        "gzjg": extract_gzjg,
    }

    @classmethod
    def extract(cls, subtype: str, detail: dict, fields: List[str], atts: List[dict]) -> dict:
        func = cls._EXTRACTORS.get(subtype)
        if func:
            return func(detail, fields, atts)
        return {f: "" for f in fields}



# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                     HTTP 客户端                                            ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class HTTPClient:
    def __init__(self, token: str, proxy: Optional[dict] = None):
        self._session = requests.Session(); self._session.verify = False
        self._session.headers.update({"authentication": token, "Origin": config.BASE_URL,
                                       "Referer": f"{config.BASE_URL}/",
                                       "User-Agent": random.choice(config.USER_AGENTS)})
        self._proxy = proxy
        self.ip_label = _proxy_ip(proxy)

    def post(self, url, json_data, timeout=config.REQUEST_TIMEOUT):
        logger.debug(f"  [POST {self.ip_label}] {url}")
        for attempt in range(config.MAX_RETRIES + 1):
            try:
                return self._session.post(url, json=json_data,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout, proxies=self._proxy)
            except requests.RequestException:
                if attempt < config.MAX_RETRIES:
                    time.sleep(config.RETRY_BACKOFF_BASE ** attempt)
                else: raise

    def get(self, url, timeout=config.REQUEST_TIMEOUT):
        logger.debug(f"  [GET {self.ip_label}] {url}")
        for attempt in range(config.MAX_RETRIES + 1):
            try:
                return self._session.get(url, timeout=timeout, proxies=self._proxy)
            except requests.RequestException:
                if attempt < config.MAX_RETRIES:
                    time.sleep(config.RETRY_BACKOFF_BASE ** attempt)
                else: raise


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                     主爬虫类 HuaxinCrawler                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class HuaxinCrawler:
    def __init__(self, token: str, output_dir: str = "./results"):
        self._token = token; self._output_dir = output_dir
        self._proxy_pool: Optional[ProxyPool] = None
        self._crawled_ids: Set[str] = set(); self._lock = Lock()
        self._results: Dict[str, List[dict]] = {}
        self._field_registry: Dict[str, List[str]] = {}  # subtype → fields

    def _get_proxy(self) -> Optional[dict]:
        if not self._proxy_pool:
            return None
        try:
            return self._proxy_pool.get_proxy()
        except ProxyPoolEmptyError:
            return None

    def _mark_bad_proxy(self, proxy: Optional[dict]):
        if proxy and self._proxy_pool:
            try:
                self._proxy_pool.mark_bad(proxy)
            except Exception:
                pass

    def _section_limit(self) -> int:
        return int(getattr(config, "MAX_RECORDS_PER_SECTION", 50) or 50)

    def _proxy_attempts(self) -> int:
        return max(1, int(getattr(config, "PROXY_RETRY_PER_REQUEST", 3) or 3))

    # ── 招标计划列表 ──
    def _fetch_bid_plan_list(self) -> List[dict]:
        """
        按前端 JS 的真实入口获取招标计划前 N 条：
        1) 列表接口 annClassifications=["4"] 取前 MAX_RECORDS_PER_SECTION 条；
        2) 使用列表记录 annId 作为前端路由 planid 和详情接口参数；
        3) 详情请求使用 ProxyPool，每条失败时换 IP 重试。
        """
        limit = self._section_limit()
        records = self._fetch_ann_list("zbjh")[:limit]
        plan_ids: List[str] = []
        for rec in records:
            pid = rec.get("annId") or rec.get("planId") or rec.get("id")
            if pid:
                plan_ids.append(str(pid))

        all_details: List[dict] = []

        def fetch_plan_detail(plan_id: str) -> Optional[dict]:
            last_error = None
            for attempt in range(self._proxy_attempts()):
                proxy = self._get_proxy()
                ip_str = _proxy_ip(proxy)
                client = HTTPClient(self._token, proxy=proxy)
                try:
                    r = client.get(f"{config.API_BID_PLAN_DETAIL}/{plan_id}?n={random.random()}")
                    if r.status_code != 200:
                        last_error = f"HTTP {r.status_code}"
                        self._mark_bad_proxy(proxy)
                        continue
                    j = r.json()
                    if j.get("code") == 200 and j.get("data"):
                        d = j["data"]
                        # 前端 JS 使用列表 annId 作为 /biddingplan?planid=xxx；
                        # 详情返回的 planId 可能是 p2047... 业务编号，不能替代路由参数。
                        d["_route_planid"] = str(plan_id)
                        d.setdefault("annId", str(plan_id))
                        logger.debug(f"  [招标计划详情] planid={plan_id} IP={ip_str}")
                        return d
                    last_error = j.get("msg") or "data为空"
                except Exception as e:
                    last_error = str(e)
                    self._mark_bad_proxy(proxy)
                time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

            logger.warning(f"  [招标计划详情] planid={plan_id} 失败: {last_error}")
            return None

        for idx, pid in enumerate(plan_ids, 1):
            d = fetch_plan_detail(pid)
            if d:
                all_details.append(d)
            if idx % 10 == 0:
                logger.info(f"  [招标计划] 详情进度: {idx}/{len(plan_ids)} 成功{len(all_details)}")
            time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

        # 注意：招标计划当前全站可能只有十几条。
        # 数据总量不足 MAX_RECORDS_PER_SECTION 是正常情况，不再为了“补满 50 条”继续扫描 ID，
        # 否则会在没有更多数据时长时间请求 1..N 造成看起来卡住。
        # 只有显式开启 ENABLE_BID_PLAN_SCAN_FALLBACK 时，才使用旧的 ID 扫描兜底。
        if not all_details and getattr(config, "ENABLE_BID_PLAN_SCAN_FALLBACK", False):
            logger.warning("[招标计划] 列表接口未取到有效详情，启动 ID 扫描兜底")
            miss = 0
            for bid_id in range(1, config.API_BID_PLAN_SCAN_MAX + 1):
                if len(all_details) >= limit:
                    break
                d = fetch_plan_detail(str(bid_id))
                if d:
                    key = f"zbjh:{bid_id}"
                    if key not in self._crawled_ids:
                        with self._lock:
                            self._crawled_ids.add(key)
                        all_details.append(d)
                    miss = 0
                else:
                    miss += 1
                    if miss >= config.API_BID_PLAN_MISS_LIMIT:
                        break
                time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

        logger.info(f"[招标计划] 目标最多{limit}条，实际成功详情 {len(all_details)} 条；不足{limit}条视为正常结束")
        return all_details[:limit]

    # ── 公告列表（按前端 JS annClassifications 获取） ──
    def _fetch_ann_list(self, section_key: str, max_pages=None) -> List[dict]:
        """
        按前端列表页 JS 对齐获取四个一级栏目各前 N 条：
        POST /bidding/bidAnnouncement/getWebAnnPage
        annClassifications:
          ["1"] 招标公告/资格预审
          ["2"] 候选公示
          ["3"] 结果公示
          ["4"] 招标计划
        列表请求也使用 ProxyPool；每页失败会换 IP 重试。
        """
        if max_pages is None:
            max_pages = config.MAX_LIST_PAGES

        limit = self._section_limit()
        ann_class_map = {
            "zbgg_zys": ["1"],
            "hxr": ["2"],
            "gs": ["3"],
            "zbjh": ["4"],
        }
        ann_classes = ann_class_map.get(section_key)
        if not ann_classes:
            logger.warning(f"[{section_key}] 未配置 annClassifications，跳过")
            return []

        all_records: List[dict] = []

        for page_num in range(1, max_pages + 1):
            if len(all_records) >= limit:
                break

            payload = dict(config.ANNOUNCE_PAYLOAD_TEMPLATE)
            payload["pageNum"] = page_num
            payload["pageSize"] = int(getattr(config, "DETAIL_PAGE_SIZE", 50) or 50)
            payload["annClassifications"] = ann_classes
            if not payload.get("classifications"):
                payload["classifications"] = ["A", "B", "C"]

            page_ok = False
            last_error = None

            for attempt in range(self._proxy_attempts()):
                proxy = self._get_proxy()
                ip_str = _proxy_ip(proxy)
                client = HTTPClient(self._token, proxy=proxy)
                try:
                    resp = client.post(config.API_ANNOUNCE_LIST, payload)
                    if resp.status_code != 200:
                        last_error = f"HTTP {resp.status_code}"
                        self._mark_bad_proxy(proxy)
                        continue

                    j = resp.json()
                    if j.get("code") != 200:
                        last_error = j.get("msg") or str(j)[:200]
                        self._mark_bad_proxy(proxy)
                        continue

                    data = j.get("data") or {}
                    records = data.get("records") or []
                    if not records:
                        logger.info(f"  [{section_key}] p{page_num}: 空列表")
                        return all_records[:limit]

                    added = 0
                    for rec in records:
                        if len(all_records) >= limit:
                            break
                        aid = rec.get("annId")
                        if not aid:
                            continue
                        key = f"{section_key}:{aid}"
                        if key in self._crawled_ids:
                            continue
                        with self._lock:
                            if key in self._crawled_ids:
                                continue
                            self._crawled_ids.add(key)
                        all_records.append(rec)
                        added += 1

                    logger.info(
                        f"  [{section_key}] p{page_num} IP={ip_str}: "
                        f"{len(records)}条→加入{added}条→累计{len(all_records)}/{limit}"
                    )

                    page_ok = True
                    total = _safe_int(data.get("total"), 0)
                    pages = _safe_int(data.get("pages"), 0)
                    page_size = _safe_int(payload.get("pageSize"), limit) or limit

                    # 结束条件必须允许“全站该类别不足 50 条”的正常情况：
                    # 1) 已达到本次目标 limit；
                    # 2) 接口明确返回总数 total，当前累计已覆盖 total；
                    # 3) 接口明确返回总页数 pages，已到最后一页；
                    # 4) 当前页返回条数小于 pageSize，说明后面没有更多记录。
                    if len(all_records) >= limit:
                        return all_records[:limit]
                    if total > 0 and (len(all_records) >= total or page_num * page_size >= total):
                        logger.info(f"  [{section_key}] 接口总数 {total} 条，不足/已达目标，正常结束")
                        return all_records[:limit]
                    if pages > 0 and page_num >= pages:
                        logger.info(f"  [{section_key}] 已到最后一页 {page_num}/{pages}，正常结束")
                        return all_records[:limit]
                    if len(records) < page_size:
                        logger.info(f"  [{section_key}] p{page_num} 返回 {len(records)} 条 < pageSize {page_size}，说明无更多数据，正常结束")
                        return all_records[:limit]
                    break

                except Exception as e:
                    last_error = str(e)
                    self._mark_bad_proxy(proxy)
                    time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

            if not page_ok:
                logger.error(f"  [{section_key}] p{page_num} 失败，已换IP重试{self._proxy_attempts()}次: {last_error}")
                if page_num <= 2:
                    time.sleep(3)
                    continue
                break

            time.sleep(random.uniform(*config.PAGE_TRANSITION_DELAY))

        logger.info(f"[{section_key}] 列表目标前{limit}条，实际 {len(all_records)} 条")
        return all_records[:limit]

    # ── 详情并发 ──
    def _fetch_details(self, records: List[dict], section_key: str) -> Tuple[List[dict], List[str]]:
        """
        普通公告详情按前端详情页 JS 对齐：
        1) GET /bidding/bidAnnouncement/getAnnWebByAnnId?annId={annId}
        2) 如果 data 为空，再 GET /web/inputAnnouncement/getInputAnn?annId={annId}
        3) 成功后再按公共 JS fileobtain 的逻辑收集 fileId 附件。
        """
        all_details, all_att_texts = [], []
        dl = Lock()
        s_fetched = [0]
        s_failed = [0]
        total = len(records)

        api_input_detail = getattr(
            config,
            "API_INPUT_ANN_DETAIL",
            f"{config.API_BASE}/web/inputAnnouncement/getInputAnn"
        )

        def _save_row(detail: dict, atts: List[dict]):
            with dl:
                detail["_attachments"] = atts or []
                all_details.append(detail)
                all_att_texts.extend([a.get("text", "") for a in atts or [] if a.get("text")])
                s_fetched[0] += 1
                if s_fetched[0] % 10 == 0:
                    logger.info(f"  [{section_key}] 进度: {s_fetched[0]}/{total}")

        def _get_detail_by_primary(client: HTTPClient, ann_id: str) -> Optional[dict]:
            r = client.get(f"{config.API_ANNOUNCE_DETAIL}?annId={ann_id}&n={random.random()}")
            if r.status_code != 200:
                return None
            j = r.json()
            if j.get("code") == 200 and j.get("data"):
                return j["data"]
            return None

        def _get_detail_by_backup(client: HTTPClient, ann_id: str) -> Optional[dict]:
            r = client.get(f"{api_input_detail}?annId={ann_id}&n={random.random()}")
            if r.status_code != 200:
                return None
            j = r.json()
            if j.get("code") == 200 and j.get("data"):
                return j["data"]
            return None

        def fetch_one(rec: dict) -> bool:
            ann_id = rec.get("annId")
            if not ann_id:
                return False

            last_error = None
            for attempt in range(self._proxy_attempts()):
                proxy = self._get_proxy()
                ip_str = _proxy_ip(proxy)
                client = HTTPClient(self._token, proxy=proxy)

                try:
                    detail = _get_detail_by_primary(client, str(ann_id))
                    source = "primary"

                    if not detail:
                        detail = _get_detail_by_backup(client, str(ann_id))
                        source = "backup"

                    if not detail:
                        last_error = "data为空"
                        self._mark_bad_proxy(proxy)
                        continue

                    # 保留列表字段，防止备用接口缺少 annId/annClassification/annNature。
                    for k, v in rec.items():
                        detail.setdefault(k, v)
                    detail.setdefault("annId", ann_id)

                    atts = []
                    if getattr(config, "ENABLE_ATTACHMENT_DOWNLOAD", True):
                        try:
                            atts = AttachmentDownloader(self._token, proxy=proxy, service_type=5).collect(detail)
                        except Exception as e:
                            logger.debug(f"  [附件] annId={ann_id} 获取失败: {e}")

                    logger.debug(f"  [详情] annId={ann_id} 来源={source} IP={ip_str}")
                    _save_row(detail, atts)
                    return True

                except Exception as e:
                    last_error = str(e)
                    self._mark_bad_proxy(proxy)

                time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

            logger.warning(f"  [详情] annId={ann_id} 换IP重试{self._proxy_attempts()}次仍失败: {last_error}")
            return False

        def worker(batch_records: List[dict]):
            for rec in batch_records:
                ok = fetch_one(rec)
                if not ok:
                    with dl:
                        s_failed[0] += 1
                time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

        batches = [[] for _ in range(config.CONCURRENT_WORKERS)]
        for i, rec in enumerate(records):
            batches[i % config.CONCURRENT_WORKERS].append(rec)

        logger.info(f"[并发] {section_key}: {config.CONCURRENT_WORKERS}线程×{total}条")
        with ThreadPoolExecutor(max_workers=config.CONCURRENT_WORKERS) as ex:
            futs = [ex.submit(worker, b) for b in batches if b]
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception as e:
                    logger.error(f"Worker异常: {e}")

        logger.info(f"[并发] {section_key} 完成: 成功{s_fetched[0]} 失败{s_failed[0]}")
        id_map = {str(d.get("annId")): d for d in all_details if d.get("annId")}
        ordered = []
        for rec in records:
            d = id_map.get(str(rec.get("annId")))
            if d:
                ordered.append(d)
        return ordered, all_att_texts

    # ── 按子类型分组提取 ──
    def _extract_grouped(self, details: List[dict], section_key: str, att_texts: List[str]) -> List[dict]:
        """
        按子类型分组。子类型依据优先使用详情字段：
        annClassification/templateType/announcementType/annNature；
        标题只做兜底或原业务扩展。
        """
        groups: Dict[str, List[dict]] = {}
        for d in details:
            title = (d.get("annTitle") or "") + (d.get("annLastTitle") or "")
            st = _detect_subtype(section_key, title, d)
            if not st:
                continue
            groups.setdefault(st, []).append(d)

        all_rows = []
        for st, dets in groups.items():
            fields = self._field_registry.get(st, [])
            for d in dets:
                row = FieldExtractors.extract(st, d, fields, d.get("_attachments", []))
                row["_subtype_"] = st
                all_rows.append(row)
            logger.info(f"  [{section_key}] 子类型 {st}: {len(dets)} 条, 字段数 {len(fields)}")
        return all_rows

    # ── 保存 ──
    def _save_csv(self, sec_def: dict, rows: List[dict]):
        """按子类型分别输出CSV（不同字段schema不能混在同一文件）"""
        # 按子类型分组输出
        groups: Dict[str, List[dict]] = {}
        subtypes = _js_subtypes(sec_def) or [sec_def["key"]]
        # sec_def 每个栏目可能有多个子类型，各自独立输出
        # 由于CSV字段不同不能合并，此处按 sec_def 整组输出
        # 对于有子类型的，拆分输出
        all_subtypes = _js_subtypes(sec_def)
        if not all_subtypes:
            all_subtypes = [sec_def["key"]]

        for st_key in all_subtypes:
            st_def = sec_def.get("sub_map", {}).get(st_key, {})
            fn_json = st_def.get("json", f"{sec_def['key']}_{st_key}.json")
            fn_csv = st_def.get("csv", f"{sec_def['key']}_{st_key}.csv")
            flds = _ensure_output_fields(self._field_registry.get(st_key, sec_def.get("fields", [])))
            if not flds:
                continue

            # 筛选属于这个子类型的行
            st_rows = [r for r in rows if r.get("_subtype_", sec_def["key"]) == st_key or
                       (st_key == sec_def["key"] and len(all_subtypes) == 1)]

            # 即使某个 JS 子类型本轮没有数据，也生成空 JSON/CSV，
            # 避免“没有输出中标结果公示文件”的情况。
            json_path = os.path.join(self._output_dir, fn_json)
            csv_path = os.path.join(self._output_dir, fn_csv)
            import json as json_lib
            out_rows = [_clean_output_row(r) for r in st_rows]
            with open(json_path, "w", encoding="utf-8") as f:
                json_lib.dump(out_rows, f, ensure_ascii=False, indent=2)
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=flds)
                w.writeheader()
                for r in out_rows: w.writerow({k: r.get(k, "") for k in flds})
            logger.info(f"[保存] {fn_json}: {len(st_rows)}条")

    def _save_simple(self, sec_def: dict, rows: List[dict]):
        """简单保存: 单一字段schema"""
        json_path = os.path.join(self._output_dir, sec_def["output_json"])
        csv_path = os.path.join(self._output_dir, sec_def["output_csv"])
        fields = _ensure_output_fields(sec_def["fields"])
        import json as json_lib
        out_rows = [_clean_output_row(r) for r in rows]
        with open(json_path, "w", encoding="utf-8") as f:
            json_lib.dump(out_rows, f, ensure_ascii=False, indent=2)
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in out_rows: w.writerow({k: r.get(k, "") for k in fields})
        logger.info(f"[保存] {json_path}: {len(rows)}条")

    # ── 主入口 ──
    def run(self):
        start = time.time()
        self._proxy_pool = ProxyPool()
        os.makedirs(self._output_dir, exist_ok=True)

        # 构建字段注册表 (subtype → field list)
        for sec_def in config.SECTION_DEFS:
            for sk, flds in sec_def.get("field_map", {}).items():
                self._field_registry[sk] = _ensure_output_fields(flds)
            if sec_def.get("fields"):  # 仅非 None/非空列表才注册
                self._field_registry[sec_def["key"]] = _ensure_output_fields(sec_def["fields"])

        logger.info("=" * 60)
        logger.info(f"华新阳光采购平台爬虫 (4栏目×每栏目最多{self._section_limit()}条)")
        logger.info(f"Token: {self._token[:30]}...  并发: {config.CONCURRENT_WORKERS}  每栏目目标: {self._section_limit()}条  IP池: 已启用")
        logger.info("=" * 60)

        grand_total = 0
        for sec_def in config.SECTION_DEFS:
            sk = sec_def["key"]
            logger.info(f"\n{'━'*50}\n▶ {sec_def['name']} ({sk})\n{'━'*50}")

            if sk == "zbjh":
                records = self._fetch_bid_plan_list()
                rows = [FieldExtractors.extract_zbjh(r, sec_def["fields"], []) for r in records]
            else:
                records = self._fetch_ann_list(sk)
                if not records:
                    rows = []
                else:
                    details, att_texts = self._fetch_details(records, sk)
                    rows = self._extract_grouped(details, sk, att_texts)

            self._results[sk] = rows; grand_total += len(rows)

            # 有子类型的栏目 → 按子类型分别保存
            subtypes = _js_subtypes(sec_def)
            if subtypes:
                for st_key in subtypes:
                    st_def = sec_def.get("sub_map", {}).get(st_key, {})
                    st_rows = [r for r in rows if r.get("_subtype_", "") == st_key or
                               (st_key == subtypes[0] and not r.get("_subtype_"))]
                    # 即使某个子类型本轮无数据，也写出空文件，便于核查任务是否跑过。
                    fn_json = st_def.get("json", f"{sk}_{st_key}.json")
                    fn_csv = st_def.get("csv", f"{sk}_{st_key}.csv")
                    flds = _ensure_output_fields(st_def.get("fields", sec_def.get("fields", [])))
                    json_path = os.path.join(self._output_dir, fn_json)
                    csv_path = os.path.join(self._output_dir, fn_csv)
                    import json as json_lib
                    out_rows = [_clean_output_row(r) for r in st_rows]
                    with open(json_path, "w", encoding="utf-8") as f:
                        json_lib.dump(out_rows, f, ensure_ascii=False, indent=2)
                    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                        w = csv.DictWriter(f, fieldnames=flds)
                        w.writeheader()
                        for r in out_rows:
                            w.writerow({k: r.get(k, "") for k in flds})
                    logger.info(f"[保存] {fn_json}: {len(st_rows)}条")
            else:
                self._save_simple(sec_def, rows)

            time.sleep(random.uniform(*config.SECTION_COOLDOWN))

        elapsed = time.time() - start
        logger.info(f"\n{'='*60}\n完成! {elapsed:.0f}s, 总计{grand_total}条\n{'='*60}")
