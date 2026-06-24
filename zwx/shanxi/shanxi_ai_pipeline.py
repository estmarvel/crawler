#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
山西省公共资源交易平台：最终版（当前公告 + 历史公告全部在“全部=11”里处理）

最终规则
--------
1. 当前公告固定从“全部”抓，channelId=11
2. 对当前公告逐条：
   抓详情页 → AI分类 → AI提字段 → 写 CSV
3. 只要 CSV 里找不到这个项目：
   - 直接在“全部=11”里按项目名搜索历史公告
   - 搜到的所有历史公告逐条继续：
     抓详情页 → AI分类 → AI提字段 → 写入同一行
4. 不做分层历史回溯
5. 不按频道拆分历史搜索
6. 当前公告和历史公告，只要走了 AI，就全部计入 token

"""

import re
import json
import time
import random
import traceback
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple, Set

import requests
import pandas as pd
from bs4 import BeautifulSoup
from openai import OpenAI


# ============================================================
# 1. 基础配置
# ============================================================

BASE_URL = "https://prec.sxzwfw.gov.cn"
LIST_URL = "https://prec.sxzwfw.gov.cn/queryContent-jyxx.jspx"

# 当前公告 + 历史搜索，都统一在“全部”里跑
ALL_CHANNEL_ID = 11
ALL_CHANNEL_NAME = "全部"

CRAWL_DAYS = 1

OUTPUT_DIR = Path("/home/intsig/zwx/shanxi/output")
LOG_DIR = Path("/home/intsig/zwx/shanxi/log")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

TODAY_STR = datetime.now().strftime("%Y%m%d")
CSV_PATH = OUTPUT_DIR / f"{TODAY_STR}_山西省.csv"
AI_REPORT_LOG_PATH = LOG_DIR / f"{TODAY_STR}_AI_report.log"

# ========== AI配置 ==========
AI_API_KEY = "sk-b5WCpX7UhAjiFXrc6BjrttdAmNgkNPVO2K8aDPM51gvfHVtr"
AI_BASE_URL = "https://vip.dmxapi.com/v1"
AI_MODEL = "gpt-4o-mini"

SESSION = requests.Session()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://prec.sxzwfw.gov.cn/jyxx/index.jhtml",
    "Content-Type": "application/x-www-form-urlencoded",
}

COLUMNS = [
    "项目名称", "交易地区", "统一代码", "项目类型", "招标内容",
    "招标人名称", "发布类型", "建设地点", "建设内容及规模",
    "项目总投资", "招标方式", "行政监督部门", "发布单位",
    "招标公告发布日期", "招标文件获取时间", "投标人资格要求", "招标公告链接",
    "变更公告链接", "中标候选人名称", "中标候选人公示链接",
    "中标人", "中标结果公示链接", "终止公告链接", "公告历史"
]

VALID_TYPES = {
    "plan": "招标计划",
    "notice": "招标公告/资格预审/资审公告",
    "amendment": "变更/更正/澄清/延期公告",
    "candidate": "中标候选人公示",
    "result": "中标结果公示",
    "terminate": "终止/废标/撤销公告",
    "skip": "控制价公告/无须入库公告",
    "unknown": "无法判断",
}

TYPE_FIELD_MAP = {
    "plan": [
        "项目名称", "统一代码", "项目类型", "招标内容", "招标人名称",
        "发布类型", "建设地点", "建设内容及规模", "项目总投资",
        "招标方式", "行政监督部门", "发布单位", "招标公告发布日期"
    ],
    "notice": [
        "项目名称", "招标公告发布日期", "招标文件获取时间", "投标人资格要求"
    ],
    "amendment": [
        "项目名称"
    ],
    "candidate": [
        "项目名称", "中标候选人名称"
    ],
    "result": [
        "项目名称", "中标人"
    ],
    "terminate": [
        "项目名称"
    ],
    "skip": [
        "项目名称"
    ],
    "unknown": [
        "项目名称"
    ],
}

TYPE_LINK_COLUMN = {
    "notice": "招标公告链接",
    "amendment": "变更公告链接",
    "candidate": "中标候选人公示链接",
    "result": "中标结果公示链接",
    "terminate": "终止公告链接",
}


# ============================================================
# 2. 通用工具
# ============================================================

def sleep_random(min_seconds: float = 0.8, max_seconds: float = 1.8) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def normalize_url(link: str) -> str:
    if not link:
        return ""
    if not link.startswith("http"):
        link = BASE_URL + link
    link = link.replace("http://prec.sxzwfw.gov.cn:80", "https://prec.sxzwfw.gov.cn")
    link = link.replace("http://prec.sxzwfw.gov.cn", "https://prec.sxzwfw.gov.cn")
    return link


def get(url: str, retries: int = 3) -> Optional[requests.Response]:
    for i in range(retries):
        try:
            resp = SESSION.get(url, headers=HEADERS, timeout=20)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp
            print(f"  [GET异常 {i + 1}/{retries}] status={resp.status_code} {url}")
        except Exception as e:
            print(f"  [GET失败 {i + 1}/{retries}] {e} | {url}")
        time.sleep(2)
    return None


def post_req(url: str, data: Dict[str, Any], retries: int = 3, headers: Optional[Dict[str, str]] = None) -> Optional[requests.Response]:
    h = headers if headers else HEADERS
    for i in range(retries):
        try:
            resp = SESSION.post(url, headers=h, data=data, timeout=30)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp
            print(f"  [POST异常 {i + 1}/{retries}] status={resp.status_code} {url}")
        except Exception as e:
            print(f"  [POST失败 {i + 1}/{retries}] {e} | {url}")
        time.sleep(3)
    return None


def html_to_text(html: str, max_chars: int = 16000) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    clean = "\n".join(lines)

    stop_keywords = ["晋ICP备", "网站标识码", "行政审批服务管理局", "投诉举报热线"]
    for kw in stop_keywords:
        pos = clean.find(kw)
        if pos != -1:
            clean = clean[:pos]

    return clean[:max_chars]


def clean_title(title: str) -> str:
    suffixes = [
        "中标结果公示澄清", "中标结果公示", "中标候选人公示",
        "变更公告", "更正公告", "变更公示", "更正公示",
        "澄清公告", "澄清答疑", "延期公告", "二次延期公告",
        "招标计划", "招标控制价变更", "招标控制价", "控制价变更", "控制价",
        "二次重新招标公告", "三次重新招标公告",
        "二次招标公告", "三次招标公告",
        "招标公告", "预审公告", "资审公告",
        "撤销公告", "招标公告撤销公告", "招标撤销公告",
        "终止公告", "废标公告", "其他公告",
    ]
    t = (title or "").strip()
    for s in suffixes:
        if t.endswith(s):
            t = t[:-len(s)].strip()
            break
    return t or title


def parse_location(loc_text: str) -> str:
    loc_text = re.sub(r"交易场所[：:]", "", loc_text or "").strip()
    if "省本级" in loc_text or loc_text == "省":
        return "山西省本级"
    m = re.search(r"([^\s]+市)", loc_text)
    if m:
        return m.group(1)
    return loc_text or "暂无"


def empty_row(project_name: str, location: str = "暂无") -> Dict[str, str]:
    row = {col: "暂无" for col in COLUMNS}
    row["项目名称"] = project_name or "暂无"
    row["交易地区"] = location or "暂无"
    return row


def append_history(existing: str, raw_title: str, link: str) -> str:
    raw_title = str(raw_title or "").strip()
    link = str(link or "").strip()
    if not raw_title and not link:
        return existing or "暂无"

    part = f"{raw_title}|{link}"
    if not existing or existing == "暂无":
        return part

    parts = [p.strip() for p in str(existing).split("；") if p.strip()]
    if part not in parts:
        parts.append(part)
    return "；".join(parts) if parts else "暂无"


def safe_json_loads(raw: str) -> Dict[str, Any]:
    if not raw:
        raise ValueError("empty model response")

    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def values_preview(data: Dict[str, str], max_len: int = 240) -> str:
    parts = []
    for k, v in data.items():
        v = str(v or "").strip()
        if not v:
            continue
        v = re.sub(r"\s+", " ", v)
        if len(v) > 60:
            v = v[:60] + "..."
        parts.append(f"{k}={v}")
    text = "；".join(parts)
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def dedupe_items(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    result = []
    for item in items:
        key = item.get("link") or item.get("raw_title")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def merge_link_value(existing: str, new_value: str) -> str:
    if not new_value or new_value == "暂无":
        return existing or "暂无"
    if not existing or existing == "暂无":
        return new_value
    parts = [p.strip() for p in str(existing).split("；") if p.strip()]
    for nv in [p.strip() for p in str(new_value).split("；") if p.strip()]:
        if nv not in parts:
            parts.append(nv)
    return "；".join(parts) if parts else "暂无"


# ============================================================
# 3. Token 统计与 AI 报告
# ============================================================

@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_usage(cls, usage: Any) -> "TokenUsage":
        if not usage:
            return cls()
        if hasattr(usage, "prompt_tokens"):
            return cls(
                prompt_tokens=usage.prompt_tokens or 0,
                completion_tokens=usage.completion_tokens or 0,
                total_tokens=usage.total_tokens or 0,
            )
        if isinstance(usage, dict):
            return cls(
                prompt_tokens=usage.get("prompt_tokens", 0) or 0,
                completion_tokens=usage.get("completion_tokens", 0) or 0,
                total_tokens=usage.get("total_tokens", 0) or 0,
            )
        return cls()

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
        )


class TokenCounter:
    def __init__(self) -> None:
        self.classify_usage = TokenUsage()
        self.extract_usage = TokenUsage()
        self.classify_calls = 0
        self.extract_calls = 0

    def add_classify(self, usage: TokenUsage) -> None:
        self.classify_usage = self.classify_usage + usage
        self.classify_calls += 1

    def add_extract(self, usage: TokenUsage) -> None:
        self.extract_usage = self.extract_usage + usage
        self.extract_calls += 1

    @property
    def total_usage(self) -> TokenUsage:
        return self.classify_usage + self.extract_usage

    @property
    def total_calls(self) -> int:
        return self.classify_calls + self.extract_calls


TOKEN_COUNTER = TokenCounter()


@dataclass
class AIReportRecord:
    source_mode: str
    anchor_project: str
    raw_title: str
    project_name: str
    channel_name: str
    link: str
    ai_type: str
    fields_expected: str
    fields_success: str
    fields_missing: str
    status: str
    classify_prompt_tokens: int
    classify_completion_tokens: int
    classify_total_tokens: int
    extract_prompt_tokens: int
    extract_completion_tokens: int
    extract_total_tokens: int
    error: str
    values_preview: str


class AIReport:
    def __init__(self) -> None:
        self.records: List[AIReportRecord] = []

    def add(self, record: AIReportRecord) -> None:
        self.records.append(record)

    def write(self) -> None:
        self._write_log()

    def _write_log(self) -> None:
        success_count = sum(1 for r in self.records if r.status == "成功")
        partial_count = sum(1 for r in self.records if r.status == "部分成功")
        failed_count = sum(1 for r in self.records if r.status == "失败")
        skipped_count = sum(1 for r in self.records if r.status.startswith("跳过"))

        current_count = sum(1 for r in self.records if r.source_mode == "current")
        history_count = sum(1 for r in self.records if r.source_mode == "history")

        total = TOKEN_COUNTER.total_usage

        with open(AI_REPORT_LOG_PATH, "w", encoding="utf-8") as f:
            f.write("山西省公共资源交易平台 AI 报告\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"使用模型: {AI_MODEL}\n")
            f.write("=" * 90 + "\n\n")

            f.write("【汇总】\n")
            f.write(f"  AI处理公告数      : {len(self.records)}\n")
            f.write(f"  当前公告          : {current_count}\n")
            f.write(f"  历史回溯公告      : {history_count}\n")
            f.write(f"  成功              : {success_count}\n")
            f.write(f"  部分成功          : {partial_count}\n")
            f.write(f"  失败              : {failed_count}\n")
            f.write(f"  跳过              : {skipped_count}\n\n")

            f.write("【Token 统计】\n")
            f.write("  说明              : 当前公告与历史回溯公告，只要走 AI 就全部计入 token。\n")
            f.write(f"  分类调用次数      : {TOKEN_COUNTER.classify_calls}\n")
            f.write(f"  分类输入tokens    : {TOKEN_COUNTER.classify_usage.prompt_tokens:,}\n")
            f.write(f"  分类输出tokens    : {TOKEN_COUNTER.classify_usage.completion_tokens:,}\n")
            f.write(f"  分类合计tokens    : {TOKEN_COUNTER.classify_usage.total_tokens:,}\n")
            f.write(f"  提取调用次数      : {TOKEN_COUNTER.extract_calls}\n")
            f.write(f"  提取输入tokens    : {TOKEN_COUNTER.extract_usage.prompt_tokens:,}\n")
            f.write(f"  提取输出tokens    : {TOKEN_COUNTER.extract_usage.completion_tokens:,}\n")
            f.write(f"  提取合计tokens    : {TOKEN_COUNTER.extract_usage.total_tokens:,}\n")
            f.write(f"  AI总调用次数      : {TOKEN_COUNTER.total_calls}\n")
            f.write(f"  AI总输入tokens    : {total.prompt_tokens:,}\n")
            f.write(f"  AI总输出tokens    : {total.completion_tokens:,}\n")
            f.write(f"  AI总计tokens      : {total.total_tokens:,}\n\n")

            f.write("【逐条明细】\n")
            f.write("-" * 90 + "\n")
            for i, r in enumerate(self.records, 1):
                f.write(f"[{i}] {r.raw_title}\n")
                f.write(f"  来源模式    : {r.source_mode}\n")
                f.write(f"  归属项目    : {r.anchor_project}\n")
                f.write(f"  AI项目名    : {r.project_name}\n")
                f.write(f"  来源频道    : {r.channel_name}\n")
                f.write(f"  链接        : {r.link}\n")
                f.write(f"  AI分类      : {r.ai_type}（{VALID_TYPES.get(r.ai_type, '未知')}）\n")
                f.write(f"  目标字段    : {r.fields_expected}\n")
                f.write(f"  成功字段    : {r.fields_success or '无'}\n")
                f.write(f"  缺失字段    : {r.fields_missing or '无'}\n")
                f.write(f"  状态        : {r.status}\n")
                f.write(
                    "  Token       : "
                    f"分类={r.classify_total_tokens}, 提取={r.extract_total_tokens}, "
                    f"合计={r.classify_total_tokens + r.extract_total_tokens}\n"
                )
                if r.error:
                    f.write(f"  错误        : {r.error}\n")
                if r.values_preview:
                    f.write(f"  字段预览    : {r.values_preview}\n")
                f.write("\n")


# ============================================================
# 4. AI 核心函数
# ============================================================

def get_ai_client() -> OpenAI:
    if not AI_API_KEY or AI_API_KEY == "请替换为你的API_KEY":
        raise RuntimeError("请先在代码顶部把 AI_API_KEY 替换成可用的 API Key。")
    return OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)


def ai_classify(
    raw_title: str,
    detail_text: str,
    channel_name: str = "",
    retries: int = 2,
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    client = client or get_ai_client()

    type_desc = "\n".join([f"- {k}: {v}" for k, v in VALID_TYPES.items()])

    system_prompt = f"""
你是公共资源交易平台公告分类助手。
你只能根据标题、来源频道、详情页正文判断公告类型。

可选 type：
{type_desc}

严格要求：
1. 只返回 JSON 对象，不要代码块，不要解释。
2. 格式必须是：
   {{"type":"notice","confidence":0.95,"reason":"一句话原因"}}
3. type 必须从可选 type 中选择。
4. 控制价、最高投标限价、招标控制价类公告返回 skip。
5. 废标、终止、撤销、流标类公告返回 terminate。
6. 变更、更正、澄清、延期、答疑类公告返回 amendment。
7. 中标候选人公示返回 candidate。
8. 中标结果、成交结果、结果公示返回 result。
9. 招标计划返回 plan。
10. 招标公告、资格预审公告、资审公告返回 notice。
11. 无法判断返回 unknown。
""".strip()

    user_prompt = f"""
来源频道：{channel_name}
公告标题：{raw_title}

详情页正文前 5000 字：
{detail_text[:5000]}
""".strip()

    last_error = ""
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=300,
            )
            usage = TokenUsage.from_usage(resp.usage)
            TOKEN_COUNTER.add_classify(usage)

            raw = resp.choices[0].message.content or ""
            data = safe_json_loads(raw)

            ai_type = str(data.get("type", "unknown")).strip().lower()
            if ai_type not in VALID_TYPES:
                ai_type = "unknown"

            try:
                confidence = float(data.get("confidence", 0))
            except Exception:
                confidence = 0.0

            return {
                "type": ai_type,
                "confidence": confidence,
                "reason": str(data.get("reason", "")),
                "_usage": usage,
            }

        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"    [AI分类失败 {attempt + 1}/{retries + 1}] {last_error}")
            if attempt < retries:
                time.sleep(3)

    return {
        "type": "unknown",
        "confidence": 0.0,
        "reason": f"AI分类失败：{last_error}",
        "_usage": TokenUsage(),
    }


def ai_extract_all(
    detail_text: str,
    ai_type: str,
    fields: Optional[List[str]] = None,
    raw_title: str = "",
    retries: int = 2,
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    client = client or get_ai_client()
    ai_type = ai_type if ai_type in TYPE_FIELD_MAP else "unknown"
    target_fields = fields or TYPE_FIELD_MAP.get(ai_type, ["项目名称"])

    field_json_example = {f: "" for f in target_fields}

    system_prompt = f"""
你是公共资源交易公告字段抽取助手。
你需要从公告详情页正文中，一次性抽取指定字段。

严格规则：
1. 只返回 JSON 对象，不要代码块，不要解释。
2. JSON 的 key 必须和目标字段完全一致。
3. 文中没有明确出现的字段，value 填空字符串 ""，不要猜测。
4. 不要把字段名本身写进 value。
5. 公司名称必须保留完整全称。
6. 时间字段保留完整时间区间。
7. “投标人资格要求”要提取完整资格条件段落。
8. “中标候选人名称”如有多个，用顿号“、”连接。
9. “中标人”如有多个，用顿号“、”连接。
10. 返回 JSON 示例：{json.dumps(field_json_example, ensure_ascii=False)}
""".strip()

    user_prompt = f"""
公告类型：{ai_type}（{VALID_TYPES.get(ai_type, "未知")}）
公告标题：{raw_title}
目标字段：{", ".join(target_fields)}

详情页正文：
{detail_text}
""".strip()

    last_error = ""
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=3000,
            )
            usage = TokenUsage.from_usage(resp.usage)
            TOKEN_COUNTER.add_extract(usage)

            raw = resp.choices[0].message.content or ""
            data = safe_json_loads(raw)

            normalized = {}
            for f in target_fields:
                val = data.get(f, "")
                if val is None:
                    val = ""
                if isinstance(val, (list, tuple)):
                    val = "、".join(str(x).strip() for x in val if str(x).strip())
                else:
                    val = str(val).strip()
                normalized[f] = val

            return {
                "data": normalized,
                "_usage": usage,
                "_error": "",
            }

        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"    [AI提取失败 {attempt + 1}/{retries + 1}] {last_error}")
            if attempt < retries:
                time.sleep(3)

    return {
        "data": {f: "" for f in target_fields},
        "_usage": TokenUsage(),
        "_error": last_error,
    }


# ============================================================
# 5. 列表抓取：当前公告与历史搜索都统一在“全部=11”里跑
# ============================================================

def fetch_list(
    channel_id: Any,
    days: int = CRAWL_DAYS,
    search_title: str = "",
    channel_name: str = "",
    verbose: bool = True,
) -> List[Dict[str, str]]:
    results = []
    page = 1
    prev_url = "https://prec.sxzwfw.gov.cn/jyxx/index.jhtml"

    while True:
        if page == 1:
            url = LIST_URL
        else:
            url = LIST_URL.replace("queryContent-", f"queryContent_{page}-")

        data = {
            "title": search_title,
            "channelId": channel_id,
            "inDates": days,
            "beginTime": "",
            "endTime": "",
            "origin": "",
            "ext": "",
        }

        headers = dict(HEADERS)
        headers["Referer"] = prev_url

        if verbose:
            print(f"  [列表] {channel_name or channel_id} 第 {page} 页...")

        resp = post_req(url, data, headers=headers)
        prev_url = url

        if not resp:
            if verbose:
                print("  [列表] 请求失败")
            break

        if len(resp.text) < 100:
            if verbose:
                print(f"  [列表] 响应内容异常，长度={len(resp.text)}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".cs_two_c_2")
        if not items:
            if page == 1 and verbose:
                print(f"  [列表] 无数据，status={resp.status_code}")
            break

        for item in items:
            link = normalize_url(item.get("href", ""))

            title_el = item.select_one(".cs_bz_cont")
            raw_title = title_el.get_text(strip=True) if title_el else ""
            pure_title = clean_title(raw_title)

            date_el = item.select_one(".cs_bz_cont_1_time")
            date = date_el.get_text(strip=True) if date_el else ""

            loc_spans = item.select(".cs_bz_cont_1 span")
            loc_text = ""
            for sp in loc_spans:
                t = sp.get_text(strip=True)
                if "交易场所" in t:
                    loc_text = t
                    break

            location = parse_location(loc_text)

            results.append({
                "raw_title": raw_title,
                "title": pure_title,
                "link": link,
                "date": date,
                "location": location,
                "channel_name": channel_name,
                "channel_id": str(channel_id),
            })

        count_match = re.search(r",count:\s*(\d+)", resp.text)
        if count_match:
            total = int(count_match.group(1))
            if verbose:
                print(f"  [列表] 总条数={total}，当前已获取={len(results)}")
            if page * 10 >= total:
                break
        else:
            break

        page += 1
        sleep_random()

    if verbose:
        print(f"  [列表] {channel_name or channel_id} 共 {len(results)} 条")
    return results


def fetch_all_current() -> List[Dict[str, str]]:
    return fetch_list(
        channel_id=ALL_CHANNEL_ID,
        days=CRAWL_DAYS,
        search_title="",
        channel_name=ALL_CHANNEL_NAME,
        verbose=True,
    )


def search_history(project_name: str) -> List[Dict[str, str]]:
    """
    历史搜索也统一在“全部=11”里搜，不再分频道。
    """
    return fetch_list(
        channel_id=ALL_CHANNEL_ID,
        days=4000,
        search_title=project_name,
        channel_name=ALL_CHANNEL_NAME,
        verbose=False,
    )


# ============================================================
# 6. DataFrame
# ============================================================

def load_csv() -> pd.DataFrame:
    if CSV_PATH.exists():
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("暂无")
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = "暂无"
        df = df[COLUMNS]
        print(f"[CSV] 加载已有文件：{CSV_PATH}，共 {len(df)} 行")
        return df

    print(f"[CSV] 新建：{CSV_PATH}")
    return pd.DataFrame(columns=COLUMNS)


def save_csv(df: pd.DataFrame) -> None:
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")


def find_row(df: pd.DataFrame, project_name: str) -> int:
    if df.empty or not project_name:
        return -1

    project_name = str(project_name).strip()
    exact = df["项目名称"] == project_name
    if exact.any():
        return int(df[exact].index[0])

    for idx, row in df.iterrows():
        name = str(row["项目名称"]).strip()
        if len(name) >= 5 and len(project_name) >= 5:
            if project_name in name or name in project_name:
                return int(idx)

    return -1


def insert_row(df: pd.DataFrame, row: Dict[str, str]) -> Tuple[pd.DataFrame, int]:
    df = pd.concat([df, pd.DataFrame([row], columns=COLUMNS)], ignore_index=True)
    return df, int(df.index[-1])


def update_history(df: pd.DataFrame, idx: int, new_items: List[Dict[str, str]]) -> pd.DataFrame:
    existing = str(df.at[idx, "公告历史"]) if "公告历史" in df.columns else "暂无"
    for item in new_items:
        existing = append_history(existing, item.get("raw_title", ""), item.get("link", ""))
    df.at[idx, "公告历史"] = existing
    return df


def apply_item_to_row(
    df: pd.DataFrame,
    idx: int,
    item: Dict[str, str],
    ai_type: str,
    extracted: Dict[str, str],
) -> Tuple[pd.DataFrame, List[str], List[str]]:
    expected_fields = TYPE_FIELD_MAP.get(ai_type, ["项目名称"])
    success_fields = []
    missing_fields = []

    existing_name = str(df.at[idx, "项目名称"])
    new_name = str(extracted.get("项目名称", "")).strip() or item.get("title", "")
    if (not existing_name or existing_name == "暂无") and new_name:
        df.at[idx, "项目名称"] = new_name
        success_fields.append("项目名称")
    elif new_name:
        success_fields.append("项目名称")
    else:
        missing_fields.append("项目名称")

    if item.get("location") and item.get("location") != "暂无":
        df.at[idx, "交易地区"] = item["location"]

    for field in expected_fields:
        if field == "项目名称":
            continue
        if field not in COLUMNS:
            continue

        val = str(extracted.get(field, "") or "").strip()
        if val:
            df.at[idx, field] = val
            success_fields.append(field)
        else:
            missing_fields.append(field)

    if ai_type == "notice" and item.get("date"):
        if str(df.at[idx, "招标公告发布日期"]) == "暂无":
            df.at[idx, "招标公告发布日期"] = item["date"]
            if "招标公告发布日期" not in success_fields:
                success_fields.append("招标公告发布日期")
            if "招标公告发布日期" in missing_fields:
                missing_fields.remove("招标公告发布日期")

    link_col = TYPE_LINK_COLUMN.get(ai_type)
    if link_col and item.get("link"):
        existing = str(df.at[idx, link_col])
        df.at[idx, link_col] = merge_link_value(existing, item["link"])
        if link_col not in success_fields:
            success_fields.append(link_col)

    df = update_history(df, idx, [item])

    success_fields = list(dict.fromkeys(success_fields))
    missing_fields = [f for f in missing_fields if f not in success_fields]
    missing_fields = list(dict.fromkeys(missing_fields))

    return df, success_fields, missing_fields


# ============================================================
# 7. 历史回溯：只要表里没有项目，就直接在“全部=11”里搜索历史公告；历史公告也逐条走 AI
# ============================================================

def backfill_history_with_ai(
    df: pd.DataFrame,
    idx: int,
    project_name: str,
    report: AIReport,
    client: OpenAI,
    processed_links: Set[str],
    exclude_links: Optional[Set[str]] = None,
) -> pd.DataFrame:
    exclude_links = exclude_links or set()

    print(f"  [历史回溯-AI] 项目={project_name} | 在“全部”中搜索历史公告")

    history_items = search_history(project_name)
    history_items = dedupe_items(history_items)

    print(f"    搜索命中: {len(history_items)} 条")

    to_process = []
    for h_item in history_items:
        h_link = h_item.get("link", "")
        if not h_link:
            continue
        if h_link in exclude_links:
            continue
        if h_link in processed_links:
            continue
        to_process.append(h_item)

    print(f"    去重并排除当前公告后，待AI处理历史公告: {len(to_process)} 条")

    for h_item in to_process:
        df = process_one_item(
            df=df,
            item=h_item,
            report=report,
            client=client,
            processed_links=processed_links,
            source_mode="history",
            anchor_idx=idx,
            anchor_project_name=project_name,
            allow_history_backfill=False,
        )

    return df


# ============================================================
# 8. 单条处理
# ============================================================

def process_one_item(
    df: pd.DataFrame,
    item: Dict[str, str],
    report: AIReport,
    client: OpenAI,
    processed_links: Set[str],
    source_mode: str = "current",
    anchor_idx: Optional[int] = None,
    anchor_project_name: str = "",
    allow_history_backfill: bool = True,
) -> pd.DataFrame:
    raw_title = item.get("raw_title", "")
    link = item.get("link", "")
    channel_name = item.get("channel_name", "")

    if not link:
        return df

    if link in processed_links:
        print(f"[跳过重复] {raw_title}")
        return df

    print(f"\n[处理-{source_mode}] {raw_title}")
    print(f"  链接: {link}")

    sleep_random()
    resp = get(link)
    if not resp:
        report.add(AIReportRecord(
            source_mode=source_mode,
            anchor_project=anchor_project_name or item.get("title", ""),
            raw_title=raw_title,
            project_name=item.get("title", ""),
            channel_name=channel_name,
            link=link,
            ai_type="unknown",
            fields_expected="",
            fields_success="",
            fields_missing="",
            status="失败",
            classify_prompt_tokens=0,
            classify_completion_tokens=0,
            classify_total_tokens=0,
            extract_prompt_tokens=0,
            extract_completion_tokens=0,
            extract_total_tokens=0,
            error="详情页请求失败",
            values_preview="",
        ))
        processed_links.add(link)
        return df

    detail_text = html_to_text(resp.text)

    cls = ai_classify(
        raw_title=raw_title,
        detail_text=detail_text,
        channel_name=channel_name,
        client=client,
    )
    ai_type = cls["type"]
    cls_usage: TokenUsage = cls["_usage"]

    print(f"  AI分类: {ai_type} | confidence={cls.get('confidence', 0)} | {cls.get('reason', '')}")

    fields = TYPE_FIELD_MAP.get(ai_type, ["项目名称"])
    ext = ai_extract_all(
        detail_text=detail_text,
        ai_type=ai_type,
        fields=fields,
        raw_title=raw_title,
        client=client,
    )
    extracted = ext["data"]
    ext_usage: TokenUsage = ext["_usage"]
    ext_error = ext.get("_error", "")

    print(f"  AI提取字段: {', '.join(fields)}")
    print(f"  提取结果: {values_preview(extracted) or '无'}")

    ai_project_name = (
        str(extracted.get("项目名称", "")).strip()
        or item.get("title", "")
        or clean_title(raw_title)
        or "暂无"
    )

    if anchor_idx is not None:
        idx = anchor_idx
        final_anchor_project = anchor_project_name or str(df.at[idx, "项目名称"])
    else:
        idx = find_row(df, ai_project_name)
        final_anchor_project = ai_project_name

        if idx < 0:
            row = empty_row(ai_project_name, item.get("location", "暂无"))
            df, idx = insert_row(df, row)
            final_anchor_project = ai_project_name

            if allow_history_backfill:
                df = backfill_history_with_ai(
                    df=df,
                    idx=idx,
                    project_name=ai_project_name,
                    report=report,
                    client=client,
                    processed_links=processed_links,
                    exclude_links={link},
                )

    if ai_type == "skip":
        df = update_history(df, idx, [item])
        status = "跳过"
        success_fields = ["项目名称"]
        missing_fields = []
    else:
        df, success_fields, missing_fields = apply_item_to_row(
            df=df,
            idx=idx,
            item=item,
            ai_type=ai_type,
            extracted=extracted,
        )
        if ai_type == "unknown":
            status = "失败"
            if not ext_error:
                ext_error = "AI无法判断公告类型"
        elif success_fields and not missing_fields:
            status = "成功"
        elif success_fields and missing_fields:
            status = "部分成功"
        else:
            status = "失败"

    report.add(AIReportRecord(
        source_mode=source_mode,
        anchor_project=final_anchor_project,
        raw_title=raw_title,
        project_name=ai_project_name,
        channel_name=channel_name,
        link=link,
        ai_type=ai_type,
        fields_expected="、".join(fields),
        fields_success="、".join(success_fields),
        fields_missing="、".join(missing_fields),
        status=status,
        classify_prompt_tokens=cls_usage.prompt_tokens,
        classify_completion_tokens=cls_usage.completion_tokens,
        classify_total_tokens=cls_usage.total_tokens,
        extract_prompt_tokens=ext_usage.prompt_tokens,
        extract_completion_tokens=ext_usage.completion_tokens,
        extract_total_tokens=ext_usage.total_tokens,
        error=ext_error,
        values_preview=values_preview(extracted),
    ))

    save_csv(df)
    report.write()
    processed_links.add(link)
    return df


# ============================================================
# 9. 主程序
# ============================================================

def main() -> None:
    print("=" * 90)
    print("山西省公共资源交易平台：从“全部”频道逐条处理公告（当前+历史全部AI）")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"CRAWL_DAYS: {CRAWL_DAYS}")
    print(f"CSV输出: {CSV_PATH}")
    print(f"AI报告: {AI_REPORT_LOG_PATH}")
    print("=" * 90)

    client = get_ai_client()

    print("初始化 Session...")
    try:
        SESSION.get("https://prec.sxzwfw.gov.cn/jyxx/index.jhtml", headers=HEADERS, timeout=15)
        print("Session 初始化成功")
    except Exception as e:
        print(f"Session 初始化失败，继续尝试：{e}")

    df = load_csv()
    report = AIReport()
    processed_links: Set[str] = set()

    print("\n" + "=" * 90)
    print("从“全部”频道抓取当前公告")
    print("=" * 90)

    current_items = fetch_all_current()
    current_items = dedupe_items(current_items)

    print(f"\n[当前公告总数] {len(current_items)} 条")

    for i, item in enumerate(current_items, 1):
        print(f"\n========== 当前公告 {i}/{len(current_items)} ==========")
        try:
            df = process_one_item(
                df=df,
                item=item,
                report=report,
                client=client,
                processed_links=processed_links,
                source_mode="current",
                anchor_idx=None,
                anchor_project_name="",
                allow_history_backfill=True,
            )
        except KeyboardInterrupt:
            print("\n用户中断，保存当前结果...")
            save_csv(df)
            report.write()
            raise
        except Exception as e:
            err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            print(f"[单条处理异常] {err}")
            report.add(AIReportRecord(
                source_mode="current",
                anchor_project=item.get("title", ""),
                raw_title=item.get("raw_title", ""),
                project_name=item.get("title", ""),
                channel_name=item.get("channel_name", ""),
                link=item.get("link", ""),
                ai_type="unknown",
                fields_expected="",
                fields_success="",
                fields_missing="",
                status="失败",
                classify_prompt_tokens=0,
                classify_completion_tokens=0,
                classify_total_tokens=0,
                extract_prompt_tokens=0,
                extract_completion_tokens=0,
                extract_total_tokens=0,
                error=err[:1000],
                values_preview="",
            ))
            report.write()

    save_csv(df)
    report.write()

    total = TOKEN_COUNTER.total_usage
    print("\n" + "=" * 90)
    print("完成")
    print(f"CSV: {CSV_PATH}")
    print(f"AI报告: {AI_REPORT_LOG_PATH}")
    print(f"总记录数: {len(df)}")
    print(f"AI调用次数: {TOKEN_COUNTER.total_calls}")
    print(f"总输入tokens: {total.prompt_tokens:,}")
    print(f"总输出tokens: {total.completion_tokens:,}")
    print(f"总tokens: {total.total_tokens:,}")
    print("=" * 90)


if __name__ == "__main__":
    main()
