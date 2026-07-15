import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from bs4 import BeautifulSoup

from sxbid_crawler.columns import COLUMNS, DEFAULT_VALUE, NOTICE_REQUIRED_FIELDS, SUPERVISION_REQUIRED_FIELDS
from sxbid_crawler.text_utils import clean_value, is_empty_value, normalize_text

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

AI_RECORDS = []
_LAST_AI_CALL_TS = 0.0

AI_EXTRACTABLE_FIELDS = {
    "项目名称",
    "所属行业",
    "组织形式",
    "开标时间",
    "标书发售时间",
    "公告内容",
    "招标人",
    "招标人地址",
    "招标人联系人",
    "招标人联系方式",
    "招标代理机构",
    "招标代理机构地址",
    "招标代理机构联系人",
    "招标代理机构联系方式",
    "监督部门",
    "监督部门地址",
    "监督部门联系人",
    "监督部门联系方式",
    "依据文件",
    "依据文号",
}



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
    notice_type = str(row.get("公告类型", ""))
    required_fields = []

    if "招标公告" in notice_type or "资格预审公告" in notice_type:
        required_fields.extend(NOTICE_REQUIRED_FIELDS)

    if "中标候选人公示" in notice_type or "中标结果公示" in notice_type:
        required_fields.extend(SUPERVISION_REQUIRED_FIELDS)

    missing = []
    for field in required_fields:
        if (
            field in AI_EXTRACTABLE_FIELDS
            and field not in missing
            and is_empty_value(row.get(field, DEFAULT_VALUE))
        ):
            missing.append(field)

    return missing


def ai_extract_fields(
    text: str,
    fields: list,
    project_name: str = "",
    retries: int = 2,
) -> dict:
    requested_fields = [
        field
        for field in dict.fromkeys(fields)
        if field in AI_EXTRACTABLE_FIELDS
    ]

    if not text or not requested_fields:
        return {field: "" for field in requested_fields}

    try:
        clean = BeautifulSoup(text, "html.parser").get_text(separator="\n")
        clean = normalize_text(clean)
    except Exception:
        clean = normalize_text(str(text))

    client = get_ai_client()
    model = os.getenv("AI_MODEL", "glm-4.6-thinking")
    fields_json = json.dumps(requested_fields, ensure_ascii=False)

    system_prompt = (
        "你是招标公告字段提取助手。"
        "只能依据用户提供的公告原文提取，禁止推测、补写、纠错或改变标点。"
        "字段值必须逐字复制原文；原文没有就返回空字符串。"
        "只返回JSON对象，不要解释，不要Markdown。"
        "JSON只能包含用户要求的字段，不能增加其他字段。"
        "监督部门信息只能来自监督部门章节，不能使用异议联系人。"
    )

    user_prompt = (
        f"项目名称：{project_name}\n"
        f"只提取这些字段：{fields_json}\n\n"
        f"公告原文：\n{clean[:12000]}"
    )

    for attempt in range(retries + 1):
        try:
            sleep_before_ai_call()
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=3000,
            )

            TOKEN_COUNTER.add(response.usage)
            raw = (response.choices[0].message.content or "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("AI返回结果不是JSON对象")

            # 只保留明确请求的字段，丢弃模型额外输出。
            return {
                field: parsed.get(field, "")
                for field in requested_fields
            }

        except json.JSONDecodeError as exc:
            AI_RECORDS.append({
                "project": project_name,
                "status": f"JSON解析失败: {exc}",
                "fields": requested_fields,
                "values": {},
                "link": "",
            })
        except Exception as exc:
            AI_RECORDS.append({
                "project": project_name,
                "status": f"AI调用失败: {exc}",
                "fields": requested_fields,
                "values": {},
                "link": "",
            })

        if attempt < retries:
            ai_retry_sleep(attempt)

    return {field: "" for field in requested_fields}


def _compact_evidence(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _value_exists_in_source(value: str, source_text: str) -> bool:
    compact_value = _compact_evidence(value)
    compact_source = _compact_evidence(source_text)

    if not compact_value:
        return False

    return compact_value in compact_source


def supplement_row_with_ai(
    row: dict,
    detail_text: str,
    link: str = "",
) -> dict:
    if os.getenv("ENABLE_AI", "false").lower() != "true":
        return row

    if not os.getenv("DMX_API_KEY", "").strip() or OpenAI is None:
        return row

    fields = missing_fields_for_row(row)
    if not fields:
        return row

    project_name = row.get("项目名称", "")
    result = ai_extract_fields(
        detail_text,
        fields,
        project_name=project_name,
    )

    accepted_values = {}
    rejected_values = {}

    for field in fields:
        value = clean_value(result.get(field, ""))

        if is_empty_value(value):
            accepted_values[field] = DEFAULT_VALUE
            continue

        # AI必须逐字引用原文，否则拒绝，避免出现“2.层”之类的改写。
        if not _value_exists_in_source(value, detail_text):
            rejected_values[field] = value
            accepted_values[field] = DEFAULT_VALUE
            continue

        # 再次保证只补空字段，绝不覆盖正则结果。
        if is_empty_value(row.get(field, DEFAULT_VALUE)):
            row[field] = value
            accepted_values[field] = value

    success_count = sum(
        not is_empty_value(value)
        for value in accepted_values.values()
    )

    if success_count == len(fields):
        status = "成功"
    elif success_count:
        status = "部分成功"
    else:
        status = "失败"

    AI_RECORDS.append({
        "project": project_name,
        "link": link,
        "fields": fields,
        "status": status,
        "values": accepted_values,
        "rejected_values": rejected_values,
        "remaining_fields": missing_fields_for_row(row),
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
