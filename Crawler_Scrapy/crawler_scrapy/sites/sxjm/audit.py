"""核对 SXJM 测试输出中的类型、正文、字段和溯源完整性。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from crawler_scrapy.sites.sxjm import config


SCHEMA_CODES = {
    "招标计划": "PLAN",
    "招标公告": "TENDER",
    "中标候选人公示": "CANDIDATE",
    "中标结果公示": "AWARD",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def _title_is_termination(title: str) -> bool:
    return any(keyword in title for keyword in ("终止", "撤销"))


def audit_record(record: Mapping[str, Any], file_name: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    subtype = _text(record.get("公告子类型"))
    parts = subtype.split(".", 1)
    channel, section = parts if len(parts) == 2 else ("", "")
    trace = record.get("_trace") if isinstance(record.get("_trace"), Mapping) else {}
    field_meta = (
        trace.get("fieldMeta") if isinstance(trace.get("fieldMeta"), Mapping) else {}
    )
    payload = trace.get("payload") if isinstance(trace.get("payload"), Mapping) else {}
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else {}
    body = _text(record.get("公告正文"))
    trace_text = _text(trace.get("rawText"))
    title = _text(record.get("公告标题"))
    notice_type = _text(record.get("公告类型")).upper()

    if channel not in config.CHANNELS or section not in config.SECTION_TYPES:
        errors.append(f"无法识别公告子类型 {subtype!r}")
        source_type = ""
        schema_type = ""
    else:
        source_type = config.source_notice_type(section)
        schema_type = config.schema_notice_type(section)
        if field_meta.get("source_notice_type") != source_type:
            errors.append("fieldMeta.source_notice_type 与栏目不一致")
        if field_meta.get("schema_notice_type") != schema_type:
            errors.append("fieldMeta.schema_notice_type 与公共 Schema 不一致")

    announcement_type = _text(field_meta.get("source_announcement_type"))
    expected_code = SCHEMA_CODES.get(schema_type, "")
    termination = section == "zzgg" or _title_is_termination(title)
    if termination:
        expected_code = "TERMINATION"
    if expected_code and notice_type != expected_code:
        errors.append(f"公告类型应为 {expected_code}，实际为 {notice_type or '(空)'}")

    if not _text(record.get("公告ID")):
        errors.append("公告ID为空")
    if not _text(record.get("详情页链接")):
        errors.append("详情页链接为空")
    if not title:
        errors.append("公告标题为空")
    if section != "zbjh" and not body:
        errors.append("非招标计划公告正文为空")
    if body != trace_text:
        errors.append("公告正文与 _trace.rawText 不一致")
    if not _text(trace.get("rawHtml")) and section != "zbjh":
        errors.append("_trace.rawHtml 为空")
    if not detail:
        errors.append("_trace.payload.detail 为空")
    if not announcement_type:
        errors.append("缺少请求 announcement_type 溯源")

    project_name = _text(record.get("项目名称"))
    source_project_name = _text(detail.get("project_name"))
    if project_name and not any(
        project_name in source for source in (body, title, source_project_name)
    ):
        warnings.append("项目名称不能在正文、标题或详情 project_name 中复核")

    if section in {"hxr", "cjhxr"}:
        names = _values(record.get("中标候选人名称"))
        if not names:
            warnings.append("候选人名称为空，需检查正文是否仅在附件中提供")
        for name in names:
            if name not in body:
                errors.append(f"候选人名称未出现在正文：{name}")
    if section in {"zbjg", "cjgg"}:
        names = _values(record.get("中标人名称"))
        if not names:
            warnings.append("中标/成交人名称为空，需检查正文是否为空模板")
        for name in names:
            if name not in body:
                errors.append(f"中标/成交人名称未出现在正文：{name}")

    attachments = record.get("附件")
    if isinstance(attachments, list):
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, Mapping):
                errors.append(f"附件[{index}]不是对象")
                continue
            if not _text(attachment.get("file_name")):
                errors.append(f"附件[{index}]文件名为空")
            if not _text(attachment.get("file_url")):
                errors.append(f"附件[{index}]URL为空")

    return {
        "file": file_name,
        "noticeId": _text(record.get("公告ID")),
        "title": title,
        "subtype": subtype,
        "sourceNoticeType": source_type,
        "schemaNoticeType": schema_type,
        "announcementType": announcement_type,
        "exportedNoticeType": notice_type,
        "databaseNoticeType": "终止公告" if termination else source_type,
        "errors": errors,
        "warnings": warnings,
    }


def audit_output(output_root: Path, expected_per_feed: int) -> dict[str, Any]:
    json_dir = output_root / "sxjm" / "json"
    if not json_dir.is_dir():
        raise FileNotFoundError(f"SXJM JSON目录不存在：{json_dir}")

    results: list[dict[str, Any]] = []
    feed_counts: Counter[str] = Counter()
    missing_by_feed: dict[str, Counter[str]] = {}
    body_lengths_by_feed: dict[str, list[int]] = {}
    database_types: Counter[str] = Counter()
    for path in sorted(json_dir.glob("*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"{path} 不是JSON数组")
        for row in rows:
            if not isinstance(row, Mapping):
                results.append({
                    "file": path.name,
                    "noticeId": "",
                    "errors": ["公告记录不是对象"],
                    "warnings": [],
                })
                continue
            result = audit_record(row, path.name)
            results.append(result)
            key = ".".join(
                (result["subtype"], result.get("announcementType") or "unknown")
            )
            feed_counts[key] += 1
            database_types[result["databaseNoticeType"]] += 1
            body_lengths_by_feed.setdefault(key, []).append(
                len(_text(row.get("公告正文")))
            )
            missing_counter = missing_by_feed.setdefault(key, Counter())
            for field in row.get("缺失字段") or []:
                missing_counter[_text(field)] += 1

    expected_feeds = {
        f"{channel}.{section}.{announcement_type}": expected_per_feed
        for channel, section, announcement_type in config.feeds(config.DEFAULT_CHANNELS)
    }
    count_errors = {
        key: {"expected": count, "actual": feed_counts.get(key, 0)}
        for key, count in expected_feeds.items()
        if feed_counts.get(key, 0) != count
    }
    error_count = sum(len(result["errors"]) for result in results)
    warning_count = sum(len(result["warnings"]) for result in results)
    quality_by_feed = {}
    for key, count in sorted(feed_counts.items()):
        lengths = body_lengths_by_feed.get(key, [])
        quality_by_feed[key] = {
            "records": count,
            "bodyLength": {
                "min": min(lengths) if lengths else 0,
                "max": max(lengths) if lengths else 0,
                "average": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            },
            "missingFieldCounts": dict(
                missing_by_feed.get(key, Counter()).most_common()
            ),
        }
    return {
        "outputRoot": str(output_root),
        "expectedPerFeed": expected_per_feed,
        "recordCount": len(results),
        "databaseNoticeTypeCounts": dict(sorted(database_types.items())),
        "feedCounts": dict(sorted(feed_counts.items())),
        "qualityByFeed": quality_by_feed,
        "feedCountMismatches": count_errors,
        "errorCount": error_count,
        "warningCount": warning_count,
        "recordsWithErrors": [result for result in results if result["errors"]],
        "recordsWithWarnings": [result for result in results if result["warnings"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--expected-per-feed", type=int, default=5)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit_output(args.output_root.resolve(), args.expected_per_feed)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(f"{serialized}\n", encoding="utf-8")
    print(serialized)
    return 1 if report["errorCount"] or report["feedCountMismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
