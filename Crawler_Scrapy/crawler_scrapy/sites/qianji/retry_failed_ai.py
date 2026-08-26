"""离线重试千极链 JSON 中失败的 AI 提取，不重新请求源站。

同时按当前证据/语义规则复核已应用的 AI 字段；旧规则不再允许的结果会回退
到 payload 快照重新解析出的规则值。脚本只更新 JSON，执行前在站点 state 下
保存原文件备份，不访问数据库。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from itemadapter import ItemAdapter

from crawler_scrapy.ai.html_extractor import AiExtractionConfig
from crawler_scrapy.ai.field_contracts import normalize_contract_data
from crawler_scrapy.ai.provider_profiles import (
    AUTO_PROVIDER,
    PROVIDER_PROFILES,
    resolve_provider,
)
from crawler_scrapy.pipelines import _to_json_compatible
from crawler_scrapy.schemas.notice_fields import (
    ANNOUNCEMENT_SCHEMAS,
    canonicalize_notice_data,
    get_missing_fields,
    normalize_notice_type,
)
from crawler_scrapy.sites.qianji.hybrid_ai import (
    LONG_SECTION_FIELDS,
    QianjiHybridAiExtractionPipeline,
    QianjiHybridAiService,
    candidate_matches_field,
)
from crawler_scrapy.sites.qianji.ai_provider import MODEL, load_project_env
from crawler_scrapy.sites.qianji.parser import QianjiParser
from crawler_scrapy.spiders.qianji import QianjiSpider


def _baseline(record: dict[str, Any], output_root: Path):
    snapshot = record.get("_trace", {}).get("payloadSnapshot", {})
    relative = str(snapshot.get("path") or "")
    if not relative:
        raise ValueError(f"公告 {record.get('公告ID')} 缺少 payloadSnapshot.path")
    payload = json.loads((output_root / relative).read_text(encoding="utf-8"))
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        raise ValueError(f"公告 {record.get('公告ID')} payload 缺少 detail")
    return QianjiParser.parse(str(record.get("公告子类型") or ""), detail)


def _item_from_record(
    record: dict[str, Any], notice_type: str, data: dict[str, Any], raw_text: str
) -> dict[str, Any]:
    trace = record.get("_trace") or {}
    field_meta = dict(trace.get("fieldMeta") or {})
    field_meta.pop("qianjiHybridAi", None)
    return {
        "notice_type": notice_type,
        "notice_subtype": record.get("公告子类型") or "",
        "notice_id": record.get("公告ID") or "",
        "title": record.get("公告标题") or "",
        "data": data,
        "raw_text": raw_text,
        "field_meta": field_meta,
        "missing_fields": get_missing_fields(
            notice_type, data, include_optional=False
        ),
        "extraction_model": "qianji-site-rule-parser",
    }


def _copy_item_to_record(record: dict[str, Any], item: dict[str, Any]) -> None:
    adapter = ItemAdapter(item)
    notice_type = str(adapter.get("notice_type") or "")
    data = dict(adapter.get("data") or {})
    for field in ANNOUNCEMENT_SCHEMAS.get(notice_type, ()):
        record[field] = _to_json_compatible(data.get(field))
    record["缺失字段"] = list(adapter.get("missing_fields") or [])
    record["抽取方式"] = str(adapter.get("extraction_model") or "")
    trace = dict(record.get("_trace") or {})
    trace["fieldMeta"] = _to_json_compatible(adapter.get("field_meta") or {})
    record["_trace"] = trace


def _normalize_record_fields(record: dict[str, Any]) -> None:
    """按当前公共字段契约重新规范化 JSON 中的业务字段。

    离线 AI 重试可能纠正时间、金额或列表字段。无论记录是否需要再次调用
    模型，都在落盘前统一经过与在线 Pipeline 相同的规范化入口，避免历史
    JSON 保留“2026年7月30日9时”等数据库字段不一致的表示。
    """

    notice_type = _notice_type_name(record)
    schema = ANNOUNCEMENT_SCHEMAS.get(notice_type, ())
    normalized = canonicalize_notice_data(
        notice_type,
        normalize_contract_data(
            {field: record.get(field) for field in schema}
        ),
    )
    for field in schema:
        record[field] = _to_json_compatible(normalized.get(field))
    record["缺失字段"] = get_missing_fields(
        notice_type, normalized, include_optional=False
    )


def _backfill_rule_fields(
    record: dict[str, Any], baseline_data: dict[str, Any]
) -> int:
    """用最新规则解析结果补齐旧 JSON 的空字段，不覆盖任何已有值。"""

    schema = ANNOUNCEMENT_SCHEMAS.get(_notice_type_name(record), ())
    fields: list[str] = []
    for field in schema:
        current = record.get(field)
        baseline = baseline_data.get(field)
        if current not in (None, "", [], {}) or baseline in (None, "", [], {}):
            continue
        record[field] = _to_json_compatible(baseline)
        fields.append(field)
    if not fields:
        return 0
    trace = dict(record.get("_trace") or {})
    field_meta = dict(trace.get("fieldMeta") or {})
    previous = field_meta.get("qianjiOfflineRuleBackfill") or {}
    field_meta["qianjiOfflineRuleBackfill"] = {
        "parserVersion": QianjiSpider.parser_version,
        "fields": list(dict.fromkeys([*(previous.get("fields") or []), *fields])),
    }
    trace["fieldMeta"] = field_meta
    record["_trace"] = trace
    return len(fields)


def _reconcile_applied(
    record: dict[str, Any], baseline_data: dict[str, Any], raw_text: str
) -> int:
    ai = (
        record.get("_trace", {})
        .get("fieldMeta", {})
        .get("qianjiHybridAi", {})
    )
    if ai.get("status") not in {"SUCCESS", "PARTIAL"}:
        return 0
    applied = set(ai.get("filledFields") or []) | set(
        ai.get("replacedFields") or []
    )
    candidates = ai.get("candidates") or {}
    invalid: set[str] = set()
    for field in applied:
        candidate = candidates.get(field) or {}
        evidence = [
            str(ref.get("preview") or "")
            for ref in candidate.get("evidenceRefs") or []
        ]
        value = candidate.get("value")
        valid = candidate_matches_field(field, value, evidence, raw_text)
        if valid and field in LONG_SECTION_FIELDS:
            valid = QianjiHybridAiExtractionPipeline._long_section_candidate_is_complete(
                baseline_data.get(field, ""), value
            )
        if not valid:
            invalid.add(field)

    for names_field, prices_field in (
        ("中标候选人名称", "中标候选人报价"),
        ("中标人名称", "中标价"),
    ):
        if names_field not in applied and prices_field not in applied:
            continue
        names = record.get(names_field) or []
        prices = record.get(prices_field) or []
        if names and prices and len(names) != len(prices):
            invalid.update({names_field, prices_field} & applied)

    for field in invalid:
        record[field] = _to_json_compatible(baseline_data.get(field))
        candidate = candidates.get(field)
        if isinstance(candidate, dict):
            candidate["grounded"] = False
            candidate["rejection"] = "POST_AUDIT_SEMANTIC_REJECTED"
    for list_name in ("filledFields", "replacedFields"):
        ai[list_name] = [field for field in ai.get(list_name) or [] if field not in invalid]
    kept = list(ai.get("conflictsKeptByRule") or [])
    ai["conflictsKeptByRule"] = list(dict.fromkeys([*kept, *sorted(invalid)]))
    if invalid:
        previous_rejected = list(ai.get("postAuditRejectedFields") or [])
        ai["postAuditRejectedFields"] = list(
            dict.fromkeys([*previous_rejected, *sorted(invalid)])
        )
    if invalid:
        normalized = canonicalize_notice_data(
            _notice_type_name(record),
            {field: record.get(field) for field in baseline_data},
        )
        record["缺失字段"] = get_missing_fields(
            _notice_type_name(record), normalized, include_optional=False
        )
    return len(invalid)


def _notice_type_name(record: dict[str, Any]) -> str:
    return normalize_notice_type(record.get("公告类型"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--min-interval", type=float, default=3.0)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--provider",
        choices=(AUTO_PROVIDER, *PROVIDER_PROFILES),
        default=AUTO_PROVIDER,
    )
    parser.add_argument(
        "--normalize-only",
        action="store_true",
        help="只用最新规则补齐并规范化 JSON，不调用 AI",
    )
    args = parser.parse_args()

    load_project_env()
    provider, model = resolve_provider(args.provider, args.model)
    api_key = os.getenv(provider.api_key_env, "").strip()
    if not api_key and not args.normalize_only:
        raise SystemExit(f"缺少 {provider.api_key_env}，请在项目 .env 中配置")
    output_root = Path(args.output_root).resolve()
    json_dir = output_root / "qianji" / "json"
    paths = sorted(json_dir.glob("*.json"))
    if not paths:
        raise SystemExit(f"没有找到 JSON：{json_dir}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = output_root / "qianji" / "state" / "ai_retry_backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in paths:
        shutil.copy2(path, backup_dir / path.name)

    config = AiExtractionConfig(
        enabled=not args.normalize_only,
        api_key=api_key,
        base_url=provider.base_url,
        model=model,
        timeout_seconds=max(30.0, args.timeout),
        min_interval_seconds=max(0.0, args.min_interval),
        retry_times=provider.retry_times,
        retry_base_delay_seconds=provider.retry_base_delay,
        retry_max_delay_seconds=provider.retry_max_delay,
        max_input_chars=16000,
        max_output_tokens=2200,
        max_calls=0,
        json_mode=True,
        response_format=provider.response_format,
        enable_thinking=provider.enable_thinking,
        include_optional_fields=True,
    )
    spider = QianjiSpider()
    service = None if args.normalize_only else QianjiHybridAiService(config)
    pipeline = QianjiHybridAiExtractionPipeline(config=config, service=service)
    pipeline.crawler = SimpleNamespace(spider=spider)

    loaded: dict[Path, list[dict[str, Any]]] = {
        path: json.loads(path.read_text(encoding="utf-8")) for path in paths
    }
    failed_jobs = []
    reconciled = 0
    rule_backfilled = 0
    for path, rows in loaded.items():
        for index, record in enumerate(rows):
            notice_type, baseline_data, _, _, raw_text = _baseline(record, output_root)
            reconciled += _reconcile_applied(record, baseline_data, raw_text)
            rule_backfilled += _backfill_rule_fields(record, baseline_data)
            ai = (
                record.get("_trace", {})
                .get("fieldMeta", {})
                .get("qianjiHybridAi", {})
            )
            if ai.get("status") in {"FAILED", "PARTIAL"} and not args.normalize_only:
                item = _item_from_record(record, notice_type, baseline_data, raw_text)
                fields = pipeline._fields(ItemAdapter(item), notice_type)
                failed_jobs.append((path, index, item, notice_type, fields, raw_text))

    def run(job):
        path, index, item, notice_type, fields, raw_text = job
        result = service.review(
            notice_type=notice_type,
            title=str(item.get("title") or ""),
            fields=fields,
            text=raw_text,
            rule_data=dict(item.get("data") or {}),
        )
        return path, index, item, result

    succeeded = failed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(run, job) for job in failed_jobs]
        for future in as_completed(futures):
            path, index, item, result = future.result()
            pipeline._apply_result(result, item)
            _copy_item_to_record(loaded[path][index], item)
            if result.success:
                succeeded += 1
            else:
                failed += 1
            print(
                f"retry {succeeded + failed}/{len(failed_jobs)} "
                f"success={succeeded} failed={failed}",
                flush=True,
            )

    for path, rows in loaded.items():
        for record in rows:
            _normalize_record_fields(record)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(_to_json_compatible(rows), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    print(
        json.dumps(
            {
                "backup": str(backup_dir),
                "normalizeOnly": args.normalize_only,
                "postAuditRejected": reconciled,
                "ruleBackfilled": rule_backfilled,
                "retried": len(failed_jobs),
                "retrySucceeded": succeeded,
                "retryFailed": failed,
            },
            ensure_ascii=False,
        )
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
