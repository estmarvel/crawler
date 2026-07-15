#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
招采进宝电子招标投标交易平台(山西) - 单页可爬性探测脚本

目的：
1. 测试公开页面/API 是否需要登录 Cookie
2. 测试普通 Session Cookie 是否强依赖
3. 测试详情 API / 搜索 API 是否可直接 requests 获取
4. 低频、单线程、只请求一个页面，降低触发频控风险

默认测试站点：
http://sxty.ebidding.net.cn

运行：
pip install requests beautifulsoup4 lxml
python zcjb_sx_one_page_probe.py
python zcjb_sx_one_page_probe.py --content-id 1251204217046040576
"""

import argparse
import base64
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup


LOGIN_KEYWORDS = [
    "请登录", "用户登录", "平台登录", "login", "用户名", "密码", "验证码"
]

FORBIDDEN_KEYWORDS = [
    "403", "Forbidden", "Access Denied", "访问被拒绝", "访问过于频繁",
    "操作过于频繁", "安全验证", "验证码", "人机验证", "请求异常"
]


def sleep_a_bit(min_s: float = 1.5, max_s: float = 3.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def text_of_response(resp: requests.Response) -> str:
    # requests 有时无法正确判断中文编码，这里优先 apparent_encoding。
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def save_text(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8", errors="ignore")
    return str(path)


def save_json(path: Path, obj: Any) -> str:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def has_any(text: str, keywords) -> bool:
    low = text.lower()
    for kw in keywords:
        if kw.lower() in low:
            return True
    return False


def summarize_http(name: str, resp: Optional[requests.Response], body: str = "", error: str = "", saved_file: str = "") -> Dict[str, Any]:
    return {
        "name": name,
        "ok": bool(resp is not None and resp.status_code == 200),
        "status_code": resp.status_code if resp is not None else None,
        "bytes_len": len(resp.content) if resp is not None else 0,
        "text_len": len(body or ""),
        "has_login_keyword": has_any(body or "", LOGIN_KEYWORDS),
        "has_forbidden_keyword": has_any(body or "", FORBIDDEN_KEYWORDS),
        "error": error,
        "saved_file": saved_file,
    }


def parse_hidden_value(html: str, name_or_class: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one(f'input[name="{name_or_class}"]') or soup.select_one(f'input.{name_or_class}')
    return (node.get("value") or "").strip() if node else ""


def parse_first_content_id_from_list_html(base_url: str, html: str) -> Optional[Dict[str, str]]:
    """
    从服务端已渲染列表里找第一条 a.each 的 contentId。
    页面结构来自保存 HTML：
    <a href=".../detail/index.html?contentId=xxx" class="each">...</a>
    """
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a.each[href*='contentId=']"):
        href = a.get("href", "")
        full_url = urljoin(base_url, href)
        qs = parse_qs(urlparse(full_url).query)
        content_id = (qs.get("contentId") or [""])[0]
        if not content_id:
            continue

        title_node = a.select_one(".each-name span") or a.select_one(".each-name") or a
        title = title_node.get("title") or title_node.get_text(" ", strip=True)

        date_node = a.select_one(".date span") or a.select_one(".date")
        publish_date = date_node.get_text(" ", strip=True) if date_node else ""

        return {
            "content_id": content_id,
            "title": title.strip(),
            "publish_date": publish_date.strip(),
            "detail_url": full_url,
        }
    return None


def parse_search_rows(data: Dict[str, Any], base_url: str, detail_html_path: str) -> list:
    rows = (((data or {}).get("res") or {}).get("rows") or [])
    out = []
    for row in rows:
        cid = str(row.get("id") or "").strip()
        if not cid:
            continue
        detail_path = detail_html_path or "/cms/sx/webfile/detail/index.html"
        out.append({
            "content_id": cid,
            "title": row.get("title") or "",
            "publish_date": row.get("publishDate") or "",
            "detail_url": urljoin(base_url, f"{detail_path}?contentId={cid}"),
            "raw": row,
        })
    return out


def decode_dynamic_res(res: str) -> Optional[Dict[str, Any]]:
    """
    detail.js 里的逻辑是：
    JSON.parse(decodeURIComponent(base64ToUtf8(str)))

    JS 的 base64ToUtf8 本身已经做过一次 decodeURIComponent，
    这里用 Python 兼容处理：base64 -> utf-8 -> unquote，必要时再 unquote。
    """
    if not res:
        return None

    raw_bytes = base64.b64decode(res)
    text = raw_bytes.decode("utf-8", errors="replace")

    candidates = [text]
    try:
        candidates.append(unquote(text))
        candidates.append(unquote(unquote(text)))
    except Exception:
        pass

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue

    return {"_decode_error": True, "_raw_text_preview": text[:1000]}


def extract_detail_summary(decoded: Optional[Dict[str, Any]], content_id: str = "") -> Dict[str, Any]:
    if not decoded:
        return {}

    project = decoded.get("project")
    packages = decoded.get("packages") or []

    titles = []
    category_names = []
    content_text_preview = ""

    for package in packages:
        for cat in package.get("categoryContents") or []:
            category_names.append(cat.get("categoryName") or "")
            for content in cat.get("contents") or []:
                title = content.get("title") or ""
                if title:
                    titles.append({
                        "id": str(content.get("id") or ""),
                        "title": title,
                        "publish_date": content.get("publishDate") or "",
                    })
                if content_id and str(content.get("id") or "") == str(content_id):
                    html = content.get("content") or content.get("txt") or content.get("contentText") or ""
                    content_text_preview = BeautifulSoup(html, "lxml").get_text("\n", strip=True)[:1000]

    return {
        "project": project,
        "category_names": [x for x in dict.fromkeys(category_names) if x],
        "titles": titles[:10],
        "matched_content_preview": content_text_preview,
        "package_count": len(packages),
    }


def post_json(session_like, url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 20) -> requests.Response:
    return session_like.post(url, headers=headers, json=payload, timeout=timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://sxty.ebidding.net.cn", help="站点根地址，例如 http://sxty.ebidding.net.cn 或 http://sx.zcjb.com.cn")
    parser.add_argument("--entry-path", default="/cms/sx/webfile/zdsx=jyxx/index.html?bulletinType=7442510&projectType=7442500&city=", help="交易信息列表入口路径")
    parser.add_argument("--content-id", default="", help="指定一个详情 contentId；不传则从入口页第一条列表自动提取")
    parser.add_argument("--keyword", default="", help="搜索接口测试关键词，默认空字符串")
    parser.add_argument("--page-size", type=int, default=1, help="搜索接口每页条数，测试建议 1")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--out-dir", default="zcjb_probe_output")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    entry_url = urljoin(base_url, args.entry_path)
    detail_page_path = "/cms/sx/webfile/detail/index.html"
    detail_api_url = urljoin(base_url, "/cms/api/dynamicData/queryContent")
    search_api_url = urljoin(base_url, "/cms/api/search/searchKeyWord")

    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)

    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": base_url,
        "Referer": entry_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    page_headers = {
        **headers,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    report: Dict[str, Any] = {
        "target": base_url,
        "entry_url": entry_url,
        "detail_api_url": detail_api_url,
        "search_api_url": search_api_url,
        "probe_results": [],
    }

    # 1. GET 入口页，获取普通 cookie，并尽量解析第一条 contentId。
    entry_html = ""
    first_item = None
    try:
        resp = session.get(entry_url, headers=page_headers, timeout=args.timeout)
        entry_html = text_of_response(resp)
        saved = save_text(out_dir / "01_entry_page.html", entry_html)
        report["probe_results"].append(summarize_http("01_entry_page", resp, entry_html, saved_file=saved))
        report["cookies_after_entry"] = session.cookies.get_dict()
        report["site_id_from_entry"] = parse_hidden_value(entry_html, "chunkSiteId") or "744"
        detail_path_from_entry = parse_hidden_value(entry_html, "detailHtml")
        if detail_path_from_entry:
            detail_page_path = detail_path_from_entry
        report["detail_html_path_from_entry"] = detail_page_path
        first_item = parse_first_content_id_from_list_html(base_url, entry_html)
        report["first_list_item_from_entry"] = first_item
    except Exception as e:
        report["probe_results"].append(summarize_http("01_entry_page", None, error=repr(e)))

    sleep_a_bit()

    # 2. 如果入口页没解析到 contentId，就用参数传入的 contentId。
    content_id = args.content_id or (first_item or {}).get("content_id") or "1251204217046040576"
    report["content_id_used"] = content_id
    detail_page_url = urljoin(base_url, f"{detail_page_path}?contentId={content_id}")
    report["detail_page_url"] = detail_page_url

    # 3. GET 详情 HTML 页面；注意这个页面可能只是壳，真正数据由 queryContent API 返回。
    detail_html = ""
    try:
        resp = session.get(detail_page_url, headers=page_headers, timeout=args.timeout)
        detail_html = text_of_response(resp)
        saved = save_text(out_dir / "02_detail_page_shell_or_rendered.html", detail_html)
        summary = summarize_http("02_detail_page_get", resp, detail_html, saved_file=saved)
        soup = BeautifulSoup(detail_html, "lxml")
        summary["has_rendered_title"] = bool(soup.select_one(".d-detail h2.title"))
        summary["has_rendered_content"] = bool(soup.select_one(".tab-content") and soup.select_one(".tab-content").get_text(strip=True))
        report["probe_results"].append(summary)
    except Exception as e:
        report["probe_results"].append(summarize_http("02_detail_page_get", None, error=repr(e)))

    sleep_a_bit()

    # 4. POST 详情 API（带 Session Cookie）
    detail_payload = {"contentId": content_id, "packageId": None, "categoryId": None}
    detail_api_json = {}
    decoded_detail = None
    try:
        resp = post_json(session, detail_api_url, detail_payload, headers, timeout=args.timeout)
        body = text_of_response(resp)
        saved = save_text(out_dir / "03_detail_api_with_session.json", body)
        summary = summarize_http("03_detail_api_with_session", resp, body, saved_file=saved)

        try:
            detail_api_json = resp.json()
        except Exception:
            detail_api_json = {"_json_parse_error": True, "_body_preview": body[:1000]}

        if isinstance(detail_api_json, dict) and detail_api_json.get("res"):
            decoded_detail = decode_dynamic_res(detail_api_json.get("res"))
            save_json(out_dir / "03_detail_api_decoded.json", decoded_detail)
            summary["has_json_res"] = True
            summary["decode_ok"] = isinstance(decoded_detail, dict) and not decoded_detail.get("_decode_error")
            summary["decoded_saved_file"] = str(out_dir / "03_detail_api_decoded.json")
            report["detail_sample"] = extract_detail_summary(decoded_detail, content_id)
        else:
            summary["has_json_res"] = False
            summary["decode_ok"] = False

        report["probe_results"].append(summary)
    except Exception as e:
        report["probe_results"].append(summarize_http("03_detail_api_with_session", None, error=repr(e)))

    sleep_a_bit()

    # 5. POST 详情 API（无 Cookie 对照）
    try:
        plain = requests.Session()
        resp = post_json(plain, detail_api_url, detail_payload, headers, timeout=args.timeout)
        body = text_of_response(resp)
        saved = save_text(out_dir / "04_detail_api_no_cookie.json", body)
        summary = summarize_http("04_detail_api_no_cookie", resp, body, saved_file=saved)
        try:
            j = resp.json()
            summary["has_json_res"] = bool(isinstance(j, dict) and j.get("res"))
        except Exception:
            summary["has_json_res"] = False
        report["probe_results"].append(summary)
    except Exception as e:
        report["probe_results"].append(summarize_http("04_detail_api_no_cookie", None, error=repr(e)))

    sleep_a_bit()

    # 6. POST 搜索 API（带 Session Cookie）；关键词默认空，pageSize 默认 1，只用于测试接口可达性。
    site_id = report.get("site_id_from_entry") or "744"
    search_payload = {
        "pageNo": 1,
        "pageSize": args.page_size,
        "dto": {
            "siteId": site_id,
            "keyWords": args.keyword,
        }
    }
    try:
        resp = post_json(session, search_api_url, search_payload, headers, timeout=args.timeout)
        body = text_of_response(resp)
        saved = save_text(out_dir / "05_search_api_with_session.json", body)
        summary = summarize_http("05_search_api_with_session", resp, body, saved_file=saved)

        try:
            j = resp.json()
            rows = parse_search_rows(j, base_url, detail_page_path)
            summary["has_rows"] = bool(rows)
            summary["row_count"] = len(rows)
            report["search_rows_sample"] = rows[:3]
        except Exception:
            summary["has_rows"] = False
            summary["row_count"] = 0

        report["probe_results"].append(summary)
    except Exception as e:
        report["probe_results"].append(summarize_http("05_search_api_with_session", None, error=repr(e)))

    # 7. 自动判断
    def find_result(name):
        for item in report["probe_results"]:
            if item["name"] == name:
                return item
        return {}

    detail_session = find_result("03_detail_api_with_session")
    detail_nocookie = find_result("04_detail_api_no_cookie")
    search_session = find_result("05_search_api_with_session")

    report["cookie_judgement"] = (
        "详情 API 带 Cookie 成功、无 Cookie 失败：说明至少详情接口依赖普通 Session Cookie。"
        if detail_session.get("has_json_res") and not detail_nocookie.get("has_json_res")
        else "本次测试中详情 API 不强依赖 Cookie；但正式爬取仍建议用 requests.Session 先访问入口页再请求 API。"
        if detail_session.get("has_json_res") and detail_nocookie.get("has_json_res")
        else "本次未能确认 Cookie 依赖：详情 API 未拿到有效 res，请查看保存的响应文件。"
    )

    any_forbidden = any(x.get("has_forbidden_keyword") or x.get("status_code") in (401, 403, 429) for x in report["probe_results"])
    api_success = bool(detail_session.get("has_json_res") or search_session.get("has_rows"))
    report["anti_crawl_judgement"] = (
        "检测到 401/403/429 或验证码/访问频繁等关键词，疑似存在反爬或访问限制。"
        if any_forbidden
        else "本次少量请求未发现明显强反爬；若 API 为 200 且能解析 res/rows，可先用低频单线程 requests 爬取。"
        if api_success
        else "未明显出现封禁关键词，但 API 数据也未确认成功，需要检查接口参数、域名或是否有额外接口脚本。"
    )

    report_path = out_dir / "probe_report.json"
    save_json(report_path, report)

    print("=" * 80)
    print("招采进宝山西站单页探测完成")
    print("=" * 80)
    print(f"入口页: {entry_url}")
    print(f"详情页: {detail_page_url}")
    print(f"详情API: {detail_api_url}")
    print(f"搜索API: {search_api_url}")
    print(f"使用 contentId: {content_id}")
    print(f"Cookie 判断: {report['cookie_judgement']}")
    print(f"反爬判断: {report['anti_crawl_judgement']}")
    print(f"结果目录: {out_dir.resolve()}")
    print(f"报告文件: {report_path.resolve()}")
    print("=" * 80)


if __name__ == "__main__":
    main()
