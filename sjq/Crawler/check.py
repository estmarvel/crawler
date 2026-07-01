#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
公告详情页可爬性测试脚本（直连版）
=============================================================================
用途：
  - 你手动打开一个公告详情页，复制详情页 URL；
  - 本脚本只测试这个详情页是否能被 requests 抓到有效公告信息；
  - 不读取表格；
  - 不爬列表；
  - 不批量采集；
  - 不使用代理；
  - 可选保存提取到的正文文本，便于人工核对。

运行：
  python check_detail.py --url "公告详情页URL"

带 Cookie：
  python check_detail.py --url "公告详情页URL" --cookie "SESSION=xxx; token=yyy"

保存正文：
  python check_detail.py --url "公告详情页URL" --save-text detail_text.txt

如果详情页只有 PDF：
  python check_detail.py --url "公告详情页URL" --test-pdf
=============================================================================
"""

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
    "Gecko/20100101 Firefox/130.0",
]

REQUEST_TIMEOUT = 25
MAX_RETRIES = 2
REQUEST_DELAY = (0.8, 1.5)

LOGIN_KEYWORDS = [
    "请登录", "登录后", "用户登录", "会员登录", "供应商登录", "统一认证",
    "login", "signin", "sign in", "用户名", "账号", "密码",
    "无权限", "未授权", "会话过期", "登录超时", "请先登录",
]

RISK_KEYWORDS = [
    "验证码", "滑块", "安全验证", "人机验证", "访问过于频繁",
    "captcha", "verify", "waf", "风险", "风控", "防火墙",
]

NOTICE_KEYWORDS = [
    "招标公告", "采购公告", "竞争性谈判公告", "竞争性磋商公告",
    "询价公告", "询比公告", "比选公告", "资格预审公告",
    "中标候选人", "中标结果", "成交公告", "变更公告", "更正公告", "终止公告",
]

FIELD_KEYWORDS = [
    "项目名称", "项目编号", "招标编号", "采购编号", "预算金额", "最高限价",
    "招标人", "采购人", "代理机构", "投标人", "供应商", "开标时间",
    "递交截止", "获取时间", "公告期限", "联系方式", "联系人",
]

API_HINT_KEYWORDS = [
    "ajax", "$.ajax", "axios", "fetch(", "XMLHttpRequest",
    ".json", "/api/", "api/", "detail", "query", "notice", "bulletin", "announcement",
]

PDF_HINT_KEYWORDS = [
    ".pdf", "openFile", "download", "附件", "文件下载",
    "fileId", "attach", "attachment", "downloadFile",
]

CONTENT_SELECTORS = [
    "article",
    ".article",
    ".article-content",
    ".article_detail",
    ".article-detail",
    ".content",
    ".content-box",
    ".contentBox",
    ".detail",
    ".detail-content",
    ".detailContent",
    ".notice-content",
    ".noticeContent",
    ".bulletin-content",
    ".main-content",
    ".mainContent",
    ".TRS_Editor",
    "#Zoom",
    "#zoom",
    "#content",
    "#detail",
    "#printArea",
]

REMOVE_SELECTORS = [
    "script", "style", "noscript", "iframe",
    "nav", "header", "footer",
    ".nav", ".header", ".footer", ".breadcrumb", ".crumb",
    ".share", ".tools", ".toolbar", ".pagination",
]


@dataclass
class RequestAttempt:
    url: str
    status_code: str = ""
    final_url: str = ""
    encoding: str = ""
    error_type: str = ""
    error: str = ""


@dataclass
class DetailProbeResult:
    input_url: str
    final_url: str = ""
    status_code: str = ""
    encoding: str = ""
    page_title: str = ""

    login_detected: str = "否"
    risk_detected: str = "否"

    title_extracted: str = ""
    content_length: int = 0
    field_keyword_hits: int = 0
    notice_keyword_hits: int = 0

    pdf_or_attachment_hint: str = "否"
    pdf_links: List[str] = field(default_factory=list)
    pdf_download_test: str = "未测试"

    api_hint: str = "否"

    conclusion: str = ""
    need_cookie: str = "否"
    suggested_method: str = ""
    reason: str = ""

    extracted_text_preview: str = ""
    attempts: List[Dict[str, str]] = field(default_factory=list)


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, flags=re.I):
        raw = "https://" + raw
    return raw


def fix_response_encoding(resp: requests.Response) -> None:
    if resp is None:
        return

    ctype = resp.headers.get("Content-Type", "") or ""
    m = re.search(r"charset=([\w\-]+)", ctype, flags=re.I)
    if m:
        resp.encoding = m.group(1).strip()
        return

    head = resp.content[:8000].decode("ascii", errors="ignore")
    m = re.search(r"charset\s*=\s*['\"]?([\w\-]+)", head, flags=re.I)
    if m:
        resp.encoding = m.group(1).strip()
        return

    apparent = (resp.apparent_encoding or "").lower()
    if apparent in ("gb2312", "gbk", "gb18030", "big5"):
        resp.encoding = resp.apparent_encoding
    elif resp.encoding and resp.encoding.lower() not in ("iso-8859-1", "ascii"):
        pass
    else:
        resp.encoding = "utf-8"


def classify_exception(e: Exception) -> str:
    msg = str(e).lower()
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return "连接超时"
    if isinstance(e, requests.exceptions.ReadTimeout):
        return "读取超时"
    if isinstance(e, requests.exceptions.SSLError):
        return "TLS/SSL失败"
    if isinstance(e, requests.exceptions.ConnectionError):
        if "connection reset" in msg or "reset by peer" in msg:
            return "连接被重置"
        if "remote end closed connection" in msg:
            return "远端关闭连接"
        if "name resolution" in msg or "temporary failure in name resolution" in msg:
            return "DNS解析失败"
        return "连接失败"
    return type(e).__name__


class DirectRequester:
    def __init__(self, timeout: int, retries: int, cookie: str = "", auth: str = ""):
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.session = requests.Session()
        self.session.verify = False
        self.session.trust_env = False
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "close",
        })
        if cookie:
            self.session.headers["Cookie"] = cookie
        if auth:
            self.session.headers["Authorization"] = auth

    def get(self, url: str, referer: str = "") -> Tuple[Optional[requests.Response], List[RequestAttempt]]:
        attempts = []
        for idx in range(1, self.retries + 1):
            try:
                headers = {}
                if referer:
                    headers["Referer"] = referer
                resp = self.session.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                fix_response_encoding(resp)
                attempts.append(RequestAttempt(
                    url=url,
                    status_code=str(resp.status_code),
                    final_url=resp.url,
                    encoding=resp.encoding or "",
                ))
                if resp.status_code < 500:
                    return resp, attempts
            except Exception as e:
                attempts.append(RequestAttempt(
                    url=url,
                    error_type=classify_exception(e),
                    error=f"{type(e).__name__}: {e}",
                ))
            if idx < self.retries:
                time.sleep(random.uniform(*REQUEST_DELAY))
        return None, attempts


def clean_text(text: str) -> str:
    text = text or ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def soup_text(node) -> str:
    if not node:
        return ""
    for sel in REMOVE_SELECTORS:
        for tag in node.select(sel):
            tag.decompose()
    # 给常见块级标签加换行，避免全部挤成一行
    html = str(node)
    html = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    html = re.sub(r"(?i)</\s*(p|div|li|tr|h1|h2|h3|h4|h5|h6|section|article)\s*>", "\n", html)
    html = re.sub(r"(?i)<\s*(p|div|li|tr|h1|h2|h3|h4|h5|h6|section|article)[^>]*>", "\n", html)
    txt = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    lines = [re.sub(r"[ \t]+", " ", x).strip() for x in txt.splitlines()]
    lines = [x for x in lines if x]
    return clean_text("\n".join(lines))


def get_page_title(soup: BeautifulSoup) -> str:
    candidates = []

    for sel in ["h1", ".title", ".article-title", ".detail-title", ".notice-title", "#title"]:
        tag = soup.select_one(sel)
        if tag:
            t = tag.get_text(" ", strip=True)
            if t:
                candidates.append(t)

    meta = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "title"})
    if meta and meta.get("content"):
        candidates.append(meta["content"].strip())

    if soup.title:
        candidates.append(soup.title.get_text(" ", strip=True))

    for t in candidates:
        t = re.sub(r"\s+", " ", t).strip()
        # 去掉明显站点后缀
        t = re.split(r"[_\-|—]中国政府采购网|[_\-|—]政府采购|[_\-|—]公共资源", t)[0].strip()
        if len(t) >= 6:
            return t
    return candidates[0] if candidates else ""


def extract_main_content(html: str) -> Tuple[str, str]:
    """
    返回：(正文文本, 来源说明)
    """
    soup = BeautifulSoup(html or "", "html.parser")

    best_text = ""
    best_source = "body兜底"

    for sel in CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if not node:
            continue
        txt = soup_text(node)
        if len(txt) > len(best_text):
            best_text = txt
            best_source = sel

    body = soup.body or soup
    body_text = soup_text(body)

    # 如果选择器正文太短，用 body 兜底
    if len(best_text) < 200 and len(body_text) > len(best_text):
        best_text = body_text
        best_source = "body兜底"

    return best_text, best_source


def is_login_text(text: str, url: str = "") -> bool:
    low = (text or "").lower()
    u = (url or "").lower()
    if any(x in u for x in ["login", "signin", "sso", "oauth"]):
        return True
    login_score = sum(1 for k in LOGIN_KEYWORDS if k.lower() in low)
    notice_score = sum(1 for k in NOTICE_KEYWORDS if k.lower() in low)
    return login_score >= 2 and notice_score == 0 or login_score >= 3


def is_risk_text(text: str) -> bool:
    low = (text or "").lower()
    return any(k.lower() in low for k in RISK_KEYWORDS)


def count_hits(text: str, keywords: List[str]) -> int:
    return sum(1 for k in keywords if k in text)


def find_pdf_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    links = []
    seen = set()

    for a in soup.find_all("a"):
        href = a.get("href") or ""
        text = a.get_text(" ", strip=True)
        val = (href + " " + text).lower()
        if any(k.lower() in val for k in PDF_HINT_KEYWORDS):
            full = urljoin(base_url, href) if href else ""
            if full and full not in seen and not full.lower().startswith(("javascript:", "#")):
                links.append(full)
                seen.add(full)

    # 从源码里兜底抓直接 PDF
    for m in re.finditer(r"""(?i)(https?://[^'"\s<>]+?\.pdf|/[^'"\s<>]+?\.pdf|[^'"\s<>]+?\.pdf)""", html or ""):
        full = urljoin(base_url, m.group(1))
        if full not in seen:
            links.append(full)
            seen.add(full)

    return links[:10]


def test_first_pdf(requester: DirectRequester, pdf_links: List[str], referer: str) -> str:
    if not pdf_links:
        return "无PDF链接"
    url = pdf_links[0]
    resp, _ = requester.get(url, referer=referer)
    if not resp:
        return "PDF请求失败"
    ct = resp.headers.get("Content-Type", "")
    head = resp.content[:10]
    if resp.status_code == 200 and (b"%PDF" in head or "pdf" in ct.lower()):
        return f"PDF可下载：{url}"
    if resp.status_code in (401, 403):
        return f"PDF需要登录/无权限：HTTP {resp.status_code}"
    return f"PDF疑似不可直接下载：HTTP {resp.status_code}, Content-Type={ct}"


def probe_detail_page(
    url: str,
    cookie: str = "",
    auth: str = "",
    timeout: int = REQUEST_TIMEOUT,
    retries: int = MAX_RETRIES,
    test_pdf: bool = False,
) -> Tuple[DetailProbeResult, str]:
    url = normalize_url(url)
    result = DetailProbeResult(input_url=url)
    requester = DirectRequester(timeout=timeout, retries=retries, cookie=cookie, auth=auth)

    resp, attempts = requester.get(url)
    result.attempts.extend(asdict(x) for x in attempts)

    if not resp:
        result.conclusion = "未测试-详情页请求失败"
        result.suggested_method = "检查 URL 是否能在服务器环境访问；或加 Cookie 后重试"
        result.reason = "requests 未能打开该详情页"
        return result, ""

    result.final_url = resp.url
    result.status_code = str(resp.status_code)
    result.encoding = resp.encoding or ""

    html = resp.text or ""
    soup = BeautifulSoup(html, "html.parser")
    result.page_title = get_page_title(soup)

    main_text, source = extract_main_content(html)
    text_for_judge = clean_text(result.page_title + "\n" + main_text)

    result.login_detected = "是" if is_login_text(text_for_judge, resp.url) else "否"
    result.risk_detected = "是" if is_risk_text(text_for_judge) else "否"
    result.api_hint = "是" if any(k.lower() in (html or "").lower() for k in API_HINT_KEYWORDS) else "否"
    result.pdf_or_attachment_hint = "是" if any(k.lower() in (html or "").lower() for k in PDF_HINT_KEYWORDS) else "否"

    pdf_links = find_pdf_links(html, resp.url)
    result.pdf_links = pdf_links
    if pdf_links:
        result.pdf_or_attachment_hint = "是"

    if test_pdf:
        result.pdf_download_test = test_first_pdf(requester, pdf_links, referer=resp.url)

    result.title_extracted = result.page_title
    result.content_length = len(main_text)
    result.field_keyword_hits = count_hits(text_for_judge, FIELD_KEYWORDS)
    result.notice_keyword_hits = count_hits(text_for_judge, NOTICE_KEYWORDS)
    result.extracted_text_preview = clean_text(main_text[:800])

    if result.risk_detected == "是":
        result.conclusion = "不可爬/暂不可爬"
        result.need_cookie = "可能"
        result.suggested_method = "人工确认；不要绕过验证码/风控"
        result.reason = "详情页出现验证码/安全验证/风控关键词"
        return result, main_text

    if result.login_detected == "是" and not cookie and not auth:
        result.conclusion = "可爬-需Cookie/登录态"
        result.need_cookie = "是"
        result.suggested_method = "提供浏览器 Cookie/Token 后重新测试"
        result.reason = "详情页疑似登录页或权限页"
        return result, main_text

    if resp.status_code in (401, 403):
        result.conclusion = "可爬-需Cookie/登录态"
        result.need_cookie = "是"
        result.suggested_method = "提供 Cookie/Token 后重新测试"
        result.reason = f"详情页返回 HTTP {resp.status_code}"
        return result, main_text

    if resp.status_code >= 400:
        result.conclusion = "未测试-详情页状态异常"
        result.suggested_method = "检查 URL 是否正确，或换浏览器复制真实详情页链接"
        result.reason = f"详情页返回 HTTP {resp.status_code}"
        return result, main_text

    # 核心判断：
    # 1. 能提取到较长正文；
    # 2. 有公告类关键词；
    # 3. 有字段类关键词；
    if result.content_length >= 300 and result.notice_keyword_hits >= 1 and result.field_keyword_hits >= 2:
        result.conclusion = "可爬-详情HTML可直接提取"
        result.need_cookie = "否" if not (cookie or auth) else "已提供"
        result.suggested_method = "requests + BeautifulSoup 直接抽取详情页标题、正文和字段"
        result.reason = f"详情页可访问，{source} 提取正文 {result.content_length} 字，公告关键词 {result.notice_keyword_hits} 个，字段关键词 {result.field_keyword_hits} 个"
        return result, main_text

    # 有些公告标题不含“招标公告”，但正文很明显
    if result.content_length >= 500 and result.field_keyword_hits >= 3:
        result.conclusion = "可爬-详情HTML可直接提取"
        result.need_cookie = "否" if not (cookie or auth) else "已提供"
        result.suggested_method = "requests + BeautifulSoup 直接抽取详情页标题、正文和字段"
        result.reason = f"详情页可访问，{source} 提取正文 {result.content_length} 字，字段关键词 {result.field_keyword_hits} 个"
        return result, main_text

    if result.pdf_or_attachment_hint == "是" and result.content_length < 300:
        result.conclusion = "可爬-PDF/附件型"
        result.need_cookie = "否" if not (cookie or auth) else "已提供"
        result.suggested_method = "详情页可访问，但正文可能在 PDF/附件中；需要下载附件再提取文本"
        result.reason = "HTML 正文不足，但页面存在 PDF/附件线索"
        return result, main_text

    if result.api_hint == "是" and result.content_length < 300:
        result.conclusion = "条件可爬-需接口分析或浏览器渲染"
        result.need_cookie = "否" if not (cookie or auth) else "已提供"
        result.suggested_method = "用浏览器 F12 Network 找详情接口；若由 JS 渲染再考虑 Playwright"
        result.reason = "详情页 HTML 正文不足，但存在 Ajax/API/JS 线索"
        return result, main_text

    result.conclusion = "不确定-详情正文不足"
    result.need_cookie = "否" if not (cookie or auth) else "已提供"
    result.suggested_method = "人工打开页面对比源码；必要时补充 Cookie 或抓 Network"
    result.reason = f"详情页可访问，但提取正文只有 {result.content_length} 字，关键词不足"
    return result, main_text


def print_result(r: DetailProbeResult, verbose_attempts: bool = False):
    print("\n" + "=" * 80)
    print("公告详情页可爬性判断结果")
    print("=" * 80)
    print(f"输入 URL          : {r.input_url}")
    print(f"最终 URL          : {r.final_url}")
    print(f"HTTP 状态         : {r.status_code}")
    print(f"编码              : {r.encoding}")
    print(f"页面标题          : {r.page_title}")
    print("-" * 80)
    print(f"疑似登录          : {r.login_detected}")
    print(f"疑似验证码/风控   : {r.risk_detected}")
    print(f"提取标题          : {r.title_extracted}")
    print(f"正文长度          : {r.content_length}")
    print(f"公告关键词命中    : {r.notice_keyword_hits}")
    print(f"字段关键词命中    : {r.field_keyword_hits}")
    print(f"PDF/附件线索      : {r.pdf_or_attachment_hint}")
    print(f"PDF链接数量       : {len(r.pdf_links)}")
    if r.pdf_links:
        print(f"首个PDF/附件链接  : {r.pdf_links[0]}")
    print(f"PDF下载测试       : {r.pdf_download_test}")
    print(f"API/JS线索        : {r.api_hint}")
    print("-" * 80)
    print(f"结论              : {r.conclusion}")
    print(f"是否需要Cookie    : {r.need_cookie}")
    print(f"推荐方式          : {r.suggested_method}")
    print(f"判断依据          : {r.reason}")
    print("-" * 80)
    print("正文预览：")
    print(r.extracted_text_preview[:800] or "【无】")

    if verbose_attempts:
        print("-" * 80)
        print("请求记录：")
        for i, a in enumerate(r.attempts, 1):
            print(f"[{i}] url={a.get('url')}")
            if a.get("status_code"):
                print(f"    HTTP={a.get('status_code')} final={a.get('final_url')} encoding={a.get('encoding')}")
            else:
                print(f"    ERROR={a.get('error_type')} {a.get('error')}")

    print("=" * 80 + "\n")


def save_json(r: DetailProbeResult, output: str):
    if not output:
        return
    with open(output, "w", encoding="utf-8") as f:
        json.dump(asdict(r), f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON 结果已保存：{output}")


def save_text(text: str, output: str):
    if not output:
        return
    with open(output, "w", encoding="utf-8") as f:
        f.write(text or "")
    print(f"[OK] 正文文本已保存：{output}")


def parse_args():
    parser = argparse.ArgumentParser(description="公告详情页可爬性测试脚本（直连版）")
    parser.add_argument("--url", default="", help="公告详情页 URL；不传则交互输入")
    parser.add_argument("--cookie", default="", help="可选：登录 Cookie")
    parser.add_argument("--auth", default="", help="可选：Authorization，例如 Bearer xxx")
    parser.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT, help="请求超时时间，默认 25 秒")
    parser.add_argument("--retries", type=int, default=MAX_RETRIES, help="重试次数，默认 2")
    parser.add_argument("--test-pdf", action="store_true", help="尝试请求首个 PDF/附件链接")
    parser.add_argument("--output-json", default="", help="保存 JSON 判断结果")
    parser.add_argument("--save-text", default="", help="保存提取到的正文文本")
    parser.add_argument("--verbose-attempts", action="store_true", help="打印请求记录")
    return parser.parse_args()


def main():
    args = parse_args()
    url = args.url.strip() or input("请输入公告详情页 URL：").strip()

    if not url:
        print("[ERROR] URL 不能为空")
        sys.exit(1)

    print("\n[INFO] 开始测试公告详情页是否能被爬取")
    print("[INFO] 直连低频测试；不读取表格，不爬列表，不批量采集")
    print(f"[INFO] URL: {url}")

    result, text = probe_detail_page(
        url=url,
        cookie=args.cookie,
        auth=args.auth,
        timeout=args.timeout,
        retries=args.retries,
        test_pdf=args.test_pdf,
    )

    print_result(result, verbose_attempts=args.verbose_attempts)
    save_json(result, args.output_json)
    save_text(text, args.save_text)


if __name__ == "__main__":
    main()
