"""审计 SXZWFW 工程建设输出的分类、字段、正文、快照和附件清单。"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from crawler_scrapy.sites.sxzwfw import config
from crawler_scrapy.storage.source_snapshots import load_record_html


def _text(value: Any) -> str:
    return str(value or "").strip()


def _business_name(value: Any) -> str:
    text = _text(value)
    # 候选人扁平字段按框架约定可写为“标段：公司”。部分源站标段名称是
    # “001+完整项目名”且没有“标段”后缀，三位标段码仍是可靠边界。
    if "：" in text and re.match(r"^\d{3}", text):
        return text.split("：", 1)[1].strip()
    return text


def audit_record(record: Mapping[str, Any], output_root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    subtype = _text(record.get("公告子类型"))
    parts = subtype.split(".")
    trace = record.get("_trace") if isinstance(record.get("_trace"), Mapping) else {}
    field_meta = trace.get("fieldMeta") if isinstance(trace.get("fieldMeta"), Mapping) else {}
    body = _text(record.get("公告正文"))
    title = _text(record.get("公告标题"))
    section = _text(field_meta.get("source_section"))
    schema_subtype = _text(field_meta.get("schema_notice_subtype"))

    if len(parts) != 3 or parts[0] != "engineering":
        errors.append(f"公告子类型格式错误：{subtype!r}")
    else:
        if parts[1] != section:
            errors.append("公告子类型源栏目与 fieldMeta.source_section 不一致")
        if parts[2] != schema_subtype:
            errors.append("公告子类型 Schema 后缀与 fieldMeta 不一致")
    if section not in config.ENGINEERING_SECTION_CHANNELS:
        errors.append(f"未知工程建设源栏目：{section!r}")
    else:
        channel_id, label = config.ENGINEERING_SECTION_CHANNELS[section]
        if _text(field_meta.get("source_channel_id")) != channel_id:
            errors.append("源 channelId 不一致")
        if _text(field_meta.get("source_notice_type")) != label:
            errors.append("源信息类型名称不一致")

    if not _text(record.get("公告ID")):
        errors.append("公告ID为空")
    if not title:
        errors.append("公告标题为空")
    if not _text(record.get("详情页链接")):
        errors.append("详情页链接为空")
    if not body:
        errors.append("公告正文为空")
    try:
        raw_html = load_record_html(record, output_root)
    except ValueError as exc:
        raw_html = ""
        errors.append(str(exc))
    if not raw_html:
        errors.append("HTML快照内容为空")

    snapshot_value = _text(record.get("HTML快照路径"))
    snapshot_path = output_root / snapshot_value if snapshot_value else None
    if not snapshot_value or snapshot_path is None or not snapshot_path.is_file():
        errors.append("HTML快照不存在")

    if schema_subtype == "gzjg" and _text(record.get("公告类型")) != "CORRECTION":
        errors.append("更正/终止/废标公告未导出为 CORRECTION")
    for key in ("中标候选人名称", "中标人名称"):
        values = record.get(key) or []
        if not isinstance(values, list):
            values = [values]
        for value in values:
            name = _business_name(value)
            if name and name not in body:
                errors.append(f"{key}无法在正文复核：{name}")

    attachments = record.get("附件") or []
    if not isinstance(attachments, list):
        errors.append("附件不是数组")
    else:
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, Mapping):
                errors.append(f"附件[{index}]不是对象")
                continue
            url = _text(attachment.get("file_url"))
            parsed = urlsplit(url)
            if not _text(attachment.get("file_name")):
                errors.append(f"附件[{index}]文件名为空")
            if not url or parsed.scheme != "https" or parsed.hostname != "prec.sxzwfw.gov.cn":
                errors.append(f"附件[{index}]URL不是源站HTTPS地址")

    if section in {"hxr", "gs"}:
        key = "中标候选人名称" if section == "hxr" else "中标人名称"
        if not record.get(key):
            warnings.append(f"{key}为空，需核对正文是否只在附件中公布")
    return {
        "noticeId": _text(record.get("公告ID")),
        "title": title,
        "section": section,
        "subtype": subtype,
        "errors": errors,
        "warnings": warnings,
    }


def audit_output(output_root: Path, expected_per_section: int = 0) -> dict[str, Any]:
    json_dir = output_root / "sxzwfw" / "json"
    if not json_dir.is_dir():
        raise FileNotFoundError(f"SXZWFW JSON目录不存在：{json_dir}")
    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    missing_by_section: dict[str, Counter[str]] = {}
    body_lengths: dict[str, list[int]] = {}
    for path in sorted(json_dir.glob("*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"{path} 不是JSON数组")
        for row in rows:
            if not isinstance(row, Mapping):
                results.append({"noticeId": "", "section": "", "errors": ["记录不是对象"], "warnings": []})
                continue
            result = audit_record(row, output_root)
            results.append(result)
            counts[result["section"]] += 1
            section = result["section"]
            body_lengths.setdefault(section, []).append(len(_text(row.get("公告正文"))))
            missing = missing_by_section.setdefault(section, Counter())
            for field in row.get("缺失字段") or []:
                missing[_text(field)] += 1
    mismatches = {}
    if expected_per_section > 0:
        mismatches = {
            section: {"expected": expected_per_section, "actual": counts.get(section, 0)}
            for section in config.DEFAULT_SECTIONS
            if counts.get(section, 0) != expected_per_section
        }
    quality = {}
    for section, count in sorted(counts.items()):
        lengths = body_lengths.get(section, [])
        quality[section] = {
            "records": count,
            "bodyLength": {
                "min": min(lengths) if lengths else 0,
                "max": max(lengths) if lengths else 0,
                "average": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            },
            "missingFieldCounts": dict(missing_by_section.get(section, Counter()).most_common()),
        }
    return {
        "outputRoot": str(output_root),
        "recordCount": len(results),
        "sectionCounts": dict(sorted(counts.items())),
        "qualityBySection": quality,
        "sectionCountMismatches": mismatches,
        "errorCount": sum(len(row["errors"]) for row in results),
        "warningCount": sum(len(row["warnings"]) for row in results),
        "recordsWithErrors": [row for row in results if row["errors"]],
        "recordsWithWarnings": [row for row in results if row["warnings"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--expected-per-section", type=int, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = audit_output(args.output_root.expanduser().resolve(), args.expected_per_section)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(f"{serialized}\n", encoding="utf-8")
    print(serialized)
    return 1 if report["errorCount"] or report["sectionCountMismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
