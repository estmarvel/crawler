"""千极链字段质量审核与智谱 GLM-5.2 定向重提取。

该模块面向历史 ``new_output/qianji/json`` 做只读审核。默认只生成报告，
不会覆盖爬取结果，也不会连接数据库。现阶段首先处理项目编号/招标编号：

* 详情 API 的 ``projectCode`` 是源站提供的平台项目代码，但不保证展示在正文；
* 正文的“招标编号/采购编号/代理编号”是招标编号候选；
* 模型必须区分 ``DETAIL_API`` 与 ``BODY`` 两种来源，只能审核给定候选，
  不能生成新编号，也不能把 API-only 字段伪装成正文提取结果。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from crawler_scrapy.sites.bitbid.parser import valid_identifier
from crawler_scrapy.sites.qianji.ai_provider import (
    API_KEY_ENV,
    BASE_URL,
    MODEL,
    load_project_env,
)


DEFAULT_MODEL = MODEL
DEFAULT_BASE_URL = BASE_URL

_LABELS = (
    "招标项目编号",
    "采购项目编号",
    "投资项目统一代码",
    "项目代码",
    "招标编号",
    "采购编号",
    "代理编号",
    "项目编号",
)
_PROJECT_LABELS = frozenset(
    {"招标项目编号", "采购项目编号", "投资项目统一代码", "项目代码"}
)
_TENDER_LABELS = frozenset({"招标编号", "采购编号", "代理编号"})
_HTML_RE = re.compile(r"<[/!A-Za-z][^>]*>|&(?:nbsp|lt|gt|amp);", re.I)


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_identifier(value: Any) -> str:
    """规范比较形式，但不改变最终报告中的源站原值。"""

    return re.sub(r"\s+", "", _text(value)).upper()


@dataclass(frozen=True)
class IdentifierEvidence:
    label: str
    value: str
    line: int
    quote: str


@dataclass
class IdentifierAudit:
    notice_id: str
    title: str
    notice_type: str
    notice_subtype: str
    current_project_number: str
    current_tender_number: str
    api_project_code: str
    api_project_code_in_body: bool = False
    api_project_code_body_labels: list[str] = field(default_factory=list)
    evidence: list[IdentifierEvidence] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def project_candidates(self) -> list[str]:
        if self.api_project_code:
            return unique_identifiers([self.api_project_code])
        values = [self.api_project_code]
        values.extend(
            item.value for item in self.evidence if item.label in _PROJECT_LABELS
        )
        if not self.api_project_code:
            values.extend(
                item.value for item in self.evidence if item.label == "项目编号"
            )
        return unique_identifiers(values)

    @property
    def tender_candidates(self) -> list[str]:
        values = [
            item.value for item in self.evidence if item.label in _TENDER_LABELS
        ]
        # 千极链变更公告常把代理编号写成“项目编号”。当 API 已提供另一个
        # projectCode 时，只将其列为歧义候选，最终仍需模型和证据审核。
        values.extend(
            item.value
            for item in self.evidence
            if item.label == "项目编号"
            and self.api_project_code
            and normalize_identifier(item.value)
            != normalize_identifier(self.api_project_code)
        )
        return unique_identifiers(values)


def unique_identifiers(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _text(raw).strip("：:，,。；;（）() ")
        normalized = normalize_identifier(value)
        if not value or normalized in seen or not valid_identifier(value):
            continue
        seen.add(normalized)
        result.append(value)
    return result


def extract_identifier_evidence(body: str) -> list[IdentifierEvidence]:
    """提取精确标签证据，避免“招标项目编号”被再次匹配成“项目编号”。"""

    result: list[IdentifierEvidence] = []
    seen: set[tuple[str, str, int]] = set()
    labels = "|".join(re.escape(label) for label in _LABELS)
    pattern = re.compile(rf"(?P<label>{labels})\s*[：:]\s*(?P<value>.*)$", re.I)
    # 千极链的暂停/终止模板常使用“ 三、招标编号 ”作为独立小标题，
    # 真正编号位于下一段且标题后没有冒号。只匹配完整标题行，避免正文叙述
    # 中出现“招标编号”四字时误取下一行。
    heading_pattern = re.compile(
        rf"^(?:[一二三四五六七八九十百]+|\d+)\s*[、.．]\s*"
        rf"(?P<label>{labels})\s*[：:]?\s*$",
        re.I,
    )
    lines = str(body or "").splitlines()
    for line_number, line in enumerate(lines, start=1):
        compact = re.sub(r"\s+", " ", line).strip()
        matched = pattern.search(compact) or heading_pattern.fullmatch(compact)
        if not matched:
            continue
        label = matched.group("label")
        value_group = matched.groupdict().get("value") or ""
        value = value_group.strip("：:，,。；;）) ")
        quote = compact
        evidence_line = line_number
        if not value:
            for next_index in range(line_number, min(len(lines), line_number + 2)):
                next_line = re.sub(r"\s+", " ", lines[next_index]).strip()
                if next_line:
                    value = next_line.strip("：:，,。；;）) ")
                    quote = f"{compact} {next_line}".strip()
                    break
        key = (label, normalize_identifier(value), evidence_line)
        if key in seen or not valid_identifier(value):
            continue
        seen.add(key)
        result.append(
            IdentifierEvidence(
                label=label,
                value=value,
                line=evidence_line,
                quote=quote,
            )
        )
    return result


def _payload_detail(record: Mapping[str, Any]) -> Mapping[str, Any]:
    trace = record.get("_trace")
    if not isinstance(trace, Mapping):
        return {}
    payload = trace.get("payload")
    if not isinstance(payload, Mapping):
        return {}
    detail = payload.get("detail")
    return detail if isinstance(detail, Mapping) else {}


def audit_identifiers(record: Mapping[str, Any]) -> IdentifierAudit:
    body = _text(record.get("公告正文") or record.get("公告内容"))
    detail = _payload_detail(record)
    api_project_code = _text(detail.get("projectCode"))
    evidence = extract_identifier_evidence(body)
    api_normalized = normalize_identifier(api_project_code)
    body_labels = list(
        dict.fromkeys(
            item.label
            for item in evidence
            if api_normalized
            and normalize_identifier(item.value) == api_normalized
        )
    )
    audit = IdentifierAudit(
        notice_id=_text(record.get("公告ID")),
        title=_text(record.get("公告标题")),
        notice_type=_text(record.get("公告类型")),
        notice_subtype=_text(record.get("公告子类型")),
        current_project_number=_text(record.get("项目编号")),
        current_tender_number=_text(record.get("招标编号")),
        api_project_code=api_project_code,
        api_project_code_in_body=bool(
            api_normalized
            and api_normalized in normalize_identifier(body)
        ),
        api_project_code_body_labels=body_labels,
        evidence=evidence,
    )

    project = normalize_identifier(audit.current_project_number)
    tender = normalize_identifier(audit.current_tender_number)
    api_project = normalize_identifier(audit.api_project_code)
    project_candidates = {
        normalize_identifier(value) for value in audit.project_candidates
    }
    tender_candidates = {
        normalize_identifier(value) for value in audit.tender_candidates
    }

    if api_project and project != api_project:
        audit.issues.append("PROJECT_NUMBER_DIFFERS_FROM_API_PROJECT_CODE")
    elif project and project not in project_candidates:
        audit.issues.append("PROJECT_NUMBER_WITHOUT_EXACT_EVIDENCE")
    if tender and tender not in tender_candidates:
        audit.issues.append("TENDER_NUMBER_WITHOUT_EXACT_EVIDENCE")
    if not tender and tender_candidates:
        audit.issues.append("TENDER_NUMBER_MISSING_WITH_AVAILABLE_CANDIDATE")
    if len(project_candidates) > 1:
        audit.issues.append("MULTIPLE_PROJECT_NUMBER_CANDIDATES")
    if len(tender_candidates) > 1:
        audit.issues.append("MULTIPLE_TENDER_NUMBER_CANDIDATES")
    if project and tender and project == tender:
        exact_tender = any(
            item.label in _TENDER_LABELS
            and normalize_identifier(item.value) == tender
            for item in audit.evidence
        )
        if not exact_tender:
            audit.issues.append("TENDER_NUMBER_DERIVED_FROM_PROJECT_LABEL")
    if _HTML_RE.search(audit.current_project_number):
        audit.issues.append("PROJECT_NUMBER_CONTAINS_HTML")
    if _HTML_RE.search(audit.current_tender_number):
        audit.issues.append("TENDER_NUMBER_CONTAINS_HTML")
    audit.issues = list(dict.fromkeys(audit.issues))
    return audit


def build_identifier_context(body: str, evidence: Sequence[IdentifierEvidence]) -> str:
    lines = str(body or "").splitlines()
    selected: set[int] = set()
    for item in evidence:
        selected.update(range(max(1, item.line - 2), min(len(lines), item.line + 2) + 1))
    if not selected:
        selected.update(range(1, min(len(lines), 40) + 1))
    return "\n".join(
        f"L{number:04d}: {lines[number - 1]}" for number in sorted(selected)
    )


def build_identifier_messages(
    record: Mapping[str, Any], audit: IdentifierAudit
) -> list[dict[str, str]]:
    body = _text(record.get("公告正文") or record.get("公告内容"))
    context = build_identifier_context(body, audit.evidence)
    candidate_payload = {
        "项目编号候选": audit.project_candidates,
        "招标编号候选": audit.tender_candidates,
    }
    system = """你是千极数采招投标公告的字段审核器，只审核“项目编号”和“招标编号”。
必须遵守：
1. 详情API的projectCode是源站结构化的平台项目代码。存在时将它作为“项目编号”，但必须把来源写成DETAIL_API；正文未出现时不得声称它来自BODY。
2. 招标编号只能从给定招标编号候选中选择；正文“项目编号”与API projectCode不同时，可能是源站对代理编号的称呼，可以作为招标编号，但必须有原文证据。
3. 不得创造、补写、改写任何编号。原文没有就返回空字符串。
4. 源站明确将同一编号同时称为招标编号和招标项目编号时，允许两个字段相同。
5. source只能为DETAIL_API、BODY或MISSING；bodyVisible必须与给出的正文可见性一致。
6. 仅输出JSON，不输出解释或Markdown。"""
    user = f"""公告标题：{audit.title}
公告类型：{audit.notice_type}
公告子类型：{audit.notice_subtype}
详情API projectCode：{audit.api_project_code}
该projectCode是否出现在正文：{str(audit.api_project_code_in_body).lower()}
正文中对应标签：{json.dumps(audit.api_project_code_body_labels, ensure_ascii=False)}
当前项目编号：{audit.current_project_number}
当前招标编号：{audit.current_tender_number}
检测问题：{json.dumps(audit.issues, ensure_ascii=False)}
允许候选：{json.dumps(candidate_payload, ensure_ascii=False)}

原文证据：
{context}

请返回以下JSON结构：
{{
  "项目编号": {{"value": "", "source": "DETAIL_API或BODY或MISSING", "bodyVisible": false, "evidenceLines": [], "decision": "KEEP或REPLACE或DELETE"}},
  "招标编号": {{"value": "", "source": "BODY或MISSING", "bodyVisible": true, "evidenceLines": [], "decision": "KEEP或REPLACE或DELETE"}},
  "warnings": []
}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _result_value(result: Mapping[str, Any], field_name: str) -> str:
    field_result = result.get(field_name)
    if not isinstance(field_result, Mapping):
        raise ValueError(f"模型结果缺少对象字段：{field_name}")
    return _text(field_result.get("value"))


def validate_model_result(
    audit: IdentifierAudit, result: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    project = _result_value(result, "项目编号")
    tender = _result_value(result, "招标编号")
    if audit.api_project_code and normalize_identifier(project) != normalize_identifier(
        audit.api_project_code
    ):
        errors.append("MODEL_PROJECT_NUMBER_VIOLATES_API_LOCK")
    elif project and normalize_identifier(project) not in {
        normalize_identifier(value) for value in audit.project_candidates
    }:
        errors.append("MODEL_PROJECT_NUMBER_NOT_IN_CANDIDATES")
    if tender and normalize_identifier(tender) not in {
        normalize_identifier(value) for value in audit.tender_candidates
    }:
        errors.append("MODEL_TENDER_NUMBER_NOT_IN_CANDIDATES")
    if project and (not valid_identifier(project) or _HTML_RE.search(project)):
        errors.append("MODEL_PROJECT_NUMBER_INVALID")
    if tender and (not valid_identifier(tender) or _HTML_RE.search(tender)):
        errors.append("MODEL_TENDER_NUMBER_INVALID")
    for field_name in ("项目编号", "招标编号"):
        field_result = result.get(field_name)
        if not isinstance(field_result, Mapping):
            continue
        decision = _text(field_result.get("decision")).upper()
        if decision not in {"KEEP", "REPLACE", "DELETE"}:
            errors.append(f"MODEL_{field_name}_DECISION_INVALID")
        evidence_lines = field_result.get("evidenceLines")
        if not isinstance(evidence_lines, list):
            errors.append(f"MODEL_{field_name}_EVIDENCE_LINES_INVALID")
        source = _text(field_result.get("source")).upper()
        allowed_sources = (
            {"DETAIL_API", "BODY", "MISSING"}
            if field_name == "项目编号"
            else {"BODY", "MISSING"}
        )
        if source not in allowed_sources:
            errors.append(f"MODEL_{field_name}_SOURCE_INVALID")
        body_visible = field_result.get("bodyVisible")
        if not isinstance(body_visible, bool):
            errors.append(f"MODEL_{field_name}_BODY_VISIBLE_INVALID")
        if field_name == "项目编号" and audit.api_project_code:
            if source != "DETAIL_API":
                errors.append("MODEL_PROJECT_NUMBER_SOURCE_MUST_BE_DETAIL_API")
            if body_visible is not audit.api_project_code_in_body:
                errors.append("MODEL_PROJECT_NUMBER_BODY_VISIBILITY_INCORRECT")
        if field_name == "招标编号" and tender:
            if source != "BODY" or body_visible is not True:
                errors.append("MODEL_TENDER_NUMBER_SOURCE_MUST_BE_BODY")
    return errors


class ZhipuIdentifierReviewer:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 90.0,
        min_interval: float = 1.5,
        retries: int = 2,
    ) -> None:
        if not api_key:
            raise ValueError(f"未配置 {API_KEY_ENV}")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 由运行环境决定
            raise RuntimeError("缺少 openai 依赖，请安装项目 requirements") from exc
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.min_interval = max(0.0, min_interval)
        self.retries = max(0, retries)
        self._last_call = 0.0

    def review(
        self, record: Mapping[str, Any], audit: IdentifierAudit
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        delay = self.min_interval - (time.monotonic() - self._last_call)
        if delay > 0:
            time.sleep(delay)
        messages = build_identifier_messages(record, audit)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                started = time.monotonic()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=1000,
                    response_format={"type": "json_object"},
                    extra_body={
                        "thinking": {"type": "disabled"},
                        "do_sample": False,
                    },
                )
                self._last_call = time.monotonic()
                content = _text(response.choices[0].message.content)
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError("模型返回的JSON顶层不是对象")
                usage = getattr(response, "usage", None)
                metadata = {
                    "model": self.model,
                    "durationMs": round((time.monotonic() - started) * 1000),
                    "attempt": attempt + 1,
                    "tokenUsage": {
                        "prompt": getattr(usage, "prompt_tokens", None),
                        "completion": getattr(usage, "completion_tokens", None),
                        "total": getattr(usage, "total_tokens", None),
                    },
                }
                return result, metadata
            except Exception as exc:  # noqa: BLE001 - 需要统一记录API/JSON错误
                last_error = exc
                self._last_call = time.monotonic()
                if attempt < self.retries:
                    time.sleep(2**attempt)
        raise RuntimeError(f"智谱字段审核失败：{last_error}") from last_error


def iter_json_records(json_root: Path) -> Iterable[tuple[Path, int, dict[str, Any]]]:
    for path in sorted(json_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            continue
        for index, record in enumerate(payload):
            if isinstance(record, dict):
                yield path, index, record


def run_review(
    *,
    json_root: Path,
    output_path: Path,
    reviewer: ZhipuIdentifierReviewer | None,
    limit: int = 0,
    random_sample: bool = False,
    seed: int = 20260813,
) -> dict[str, Any]:
    records = list(iter_json_records(json_root))
    audited: list[tuple[Path, int, dict[str, Any], IdentifierAudit]] = []
    for path, index, record in records:
        audit = audit_identifiers(record)
        if audit.issues:
            audited.append((path, index, record, audit))
    if random_sample:
        random.Random(seed).shuffle(audited)
    if limit > 0:
        audited = audited[:limit]

    results: list[dict[str, Any]] = []
    for path, index, record, audit in audited:
        row: dict[str, Any] = {
            "sourceFile": path.name,
            "sourceIndex": index,
            "audit": {
                **asdict(audit),
                "project_candidates": audit.project_candidates,
                "tender_candidates": audit.tender_candidates,
            },
            "modelResult": None,
            "modelValidationErrors": [],
            "modelMetadata": None,
        }
        if reviewer is not None:
            try:
                model_result, metadata = reviewer.review(record, audit)
                row["modelResult"] = model_result
                row["modelValidationErrors"] = validate_model_result(
                    audit, model_result
                )
                row["modelMetadata"] = metadata
            except Exception as exc:  # noqa: BLE001 - 单条失败不能中断批次
                row["modelValidationErrors"] = [f"MODEL_CALL_FAILED:{exc}"]
        results.append(row)

    report = {
        "site": "qianji",
        "mode": "identifier_quality_review",
        "model": reviewer.model if reviewer else None,
        "sourceRecords": len(records),
        "flaggedRecords": len(audited),
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp_path.replace(output_path)
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读审核千极链历史JSON中的项目编号和招标编号"
    )
    parser.add_argument(
        "--json-root",
        default="new_output/qianji/json",
        help="千极链JSON目录",
    )
    parser.add_argument(
        "--output",
        default="new_output/qianji/ai_validation/identifier_review.json",
        help="审核报告路径，不会覆盖源JSON",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--random", action="store_true", dest="random_sample")
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="只执行规则审计，不调用智谱 GLM-5.2",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    reviewer = None
    if not args.offline:
        load_project_env()
        reviewer = ZhipuIdentifierReviewer(
            api_key=os.getenv(API_KEY_ENV, ""),
            model=args.model,
        )
    report = run_review(
        json_root=Path(args.json_root),
        output_path=Path(args.output),
        reviewer=reviewer,
        limit=max(0, args.limit),
        random_sample=args.random_sample,
        seed=args.seed,
    )
    print(
        json.dumps(
            {
                "site": report["site"],
                "sourceRecords": report["sourceRecords"],
                "flaggedRecords": report["flaggedRecords"],
                "model": report["model"],
                "output": args.output,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DEFAULT_MODEL",
    "IdentifierAudit",
    "IdentifierEvidence",
    "ZhipuIdentifierReviewer",
    "audit_identifiers",
    "build_identifier_messages",
    "extract_identifier_evidence",
    "run_review",
    "validate_model_result",
]
