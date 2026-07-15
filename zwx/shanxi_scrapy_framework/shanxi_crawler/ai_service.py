import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from bs4 import BeautifulSoup

from shanxi_crawler.columns import COLUMNS, DEFAULT_VALUE, NOTICE_REQUIRED_FIELDS, SUPERVISION_REQUIRED_FIELDS
from shanxi_crawler.text_utils import clean_value, is_empty_value, normalize_text

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

AI_RECORDS = []
_LAST_AI_CALL_TS = 0.0


class TokenCounter:
    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.call_count = 0

    def add(self, usage):
        if not usage:
            return
        if hasattr(usage, "prompt_tokens"):
            self.prompt_tokens += usage.prompt_tokens or 0
            self.completion_tokens += usage.completion_tokens or 0
            self.total_tokens += usage.total_tokens or 0
        else:
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            self.total_tokens += usage.get("total_tokens", 0)
        self.call_count += 1


TOKEN_COUNTER = TokenCounter()


def get_ai_client():
    if OpenAI is None:
        raise RuntimeError("openai 包未安装，无法调用 AI")
    api_key = os.getenv("DMX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("请先设置环境变量 DMX_API_KEY")
    return OpenAI(api_key=api_key, base_url=os.getenv("AI_BASE_URL", "https://vip.dmxapi.com/v1"))


def sleep_before_ai_call():
    global _LAST_AI_CALL_TS
    min_interval = float(os.getenv("AI_MIN_INTERVAL_SECONDS", "1.0"))
    now = time.time()
    elapsed = now - _LAST_AI_CALL_TS
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _LAST_AI_CALL_TS = time.time()


def ai_retry_sleep(attempt: int):
    base = float(os.getenv("AI_RETRY_BASE_SECONDS", "3"))
    max_seconds = float(os.getenv("AI_RETRY_MAX_SECONDS", "30"))
    time.sleep(min(base * (2 ** attempt), max_seconds))


def missing_fields_for_row(row: dict) -> List[str]:
    types = str(row.get("公告类型", ""))
    missing = []
    if "招标公告" in types or "资格预审公告" in types:
        for field in NOTICE_REQUIRED_FIELDS:
            if is_empty_value(row.get(field, DEFAULT_VALUE)):
                missing.append(field)
    if "中标候选人公示" in types or "中标结果公示" in types:
        for field in SUPERVISION_REQUIRED_FIELDS:
            if is_empty_value(row.get(field, DEFAULT_VALUE)) and field not in missing:
                missing.append(field)
    return missing


def ai_extract_fields(text: str, fields: list, project_name: str = "", retries: int = 2) -> dict:
    if not text or not fields:
        return {f: "" for f in fields}
    try:
        clean = BeautifulSoup(text, "html.parser").get_text(separator="\n")
        clean = normalize_text(clean)
    except Exception:
        clean = normalize_text(str(text))

    client = get_ai_client()
    fields_str = "、".join(fields)
    model = os.getenv("AI_MODEL", "glm-4.6-thinking")
    system_prompt = (
        "你是招标公告字段提取助手，只返回 JSON 对象，不要解释。"
        "字段找不到返回空字符串。"
        "开标时间从开标时间及地点/递交截止时间提取；标书发售时间从招标文件的获取/获取时间提取；"
        "招标人和招标代理机构字段从联系方式章节提取；"
        "监督部门字段只从监督部门/监督单位章节提取，不能把异议联系人当监督部门联系人；"
        "公告内容返回正文全文并去掉页脚备案号。"
    )
    user_prompt = f"项目名称：{project_name}\n需要提取的字段：{fields_str}\n\n公告文本：\n{clean[:12000]}"

    for attempt in range(retries + 1):
        raw = ""
        try:
            sleep_before_ai_call()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=4000,
            )
            TOKEN_COUNTER.add(resp.usage)
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
            for f in fields:
                result.setdefault(f, "")
            return result
        except json.JSONDecodeError as e:
            AI_RECORDS.append({"project": project_name, "status": f"JSON解析失败: {e}", "fields": fields, "values": {}, "link": ""})
        except Exception as e:
            AI_RECORDS.append({"project": project_name, "status": f"AI调用失败: {e}", "fields": fields, "values": {}, "link": ""})
        if attempt < retries:
            ai_retry_sleep(attempt)
    return {f: "" for f in fields}


def supplement_row_with_ai(row: dict, detail_text: str, link: str = "") -> dict:
    if os.getenv("ENABLE_AI", "false").lower() != "true":
        return row
    if not os.getenv("DMX_API_KEY", "").strip() or OpenAI is None:
        return row

    fields = missing_fields_for_row(row)
    if not fields:
        return row

    project_name = row.get("项目名称", "")
    result = ai_extract_fields(detail_text, fields, project_name=project_name)
    values = {}
    success_any = False
    for field in fields:
        val = clean_value(result.get(field, ""))
        values[field] = val
        if not is_empty_value(val):
            row[field] = val
            success_any = True
    AI_RECORDS.append({
        "project": project_name,
        "link": link,
        "fields": fields,
        "status": "成功" if success_any else "失败",
        "values": values,
    })
    return row


def write_ai_report(log_dir: str = "logs"):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    path = Path(log_dir) / f"{datetime.now().strftime('%Y%m%d')}_ai_report.log"
    success = sum(1 for r in AI_RECORDS if r.get("status") == "成功")
    failed = sum(1 for r in AI_RECORDS if str(r.get("status", "")).startswith("失败"))
    with path.open("w", encoding="utf-8") as f:
        f.write("山西省公共资源交易平台 AI兜底与OCR报告\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"使用模型: {os.getenv('AI_MODEL', 'glm-4.6-thinking')}\n")
        f.write("=" * 60 + "\n\n")
        f.write("【汇总】\n")
        f.write(f"  处理记录数 : {len(AI_RECORDS)}\n")
        f.write(f"  成功        : {success}\n")
        f.write(f"  失败        : {failed}\n")
        f.write("\n【Token消耗】\n")
        f.write(f"  AI调用次数  : {TOKEN_COUNTER.call_count}\n")
        f.write(f"  输入tokens  : {TOKEN_COUNTER.prompt_tokens:,}\n")
        f.write(f"  输出tokens  : {TOKEN_COUNTER.completion_tokens:,}\n")
        f.write(f"  合计tokens  : {TOKEN_COUNTER.total_tokens:,}\n\n")
        f.write("【详细记录】\n")
        for r in AI_RECORDS:
            f.write("-" * 40 + "\n")
            f.write(f"项目: {r.get('project', '')}\n")
            f.write(f"链接: {r.get('link', '')}\n")
            f.write(f"目标字段: {', '.join(r.get('fields', []))}\n")
            f.write(f"结果: {r.get('status', '')}\n")
            for field, val in r.get("values", {}).items():
                show = str(val)[:150] + "..." if len(str(val)) > 150 else str(val)
                f.write(f"  - {field}: {show}\n")
    return str(path)
