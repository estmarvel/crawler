"""审计国信 e 采 JSON、PDF正文、快照、附件和数据库输入契约。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from crawler_scrapy.schemas.notice_fields import get_notice_fields
from crawler_scrapy.sites.gxebidding import config
from crawler_scrapy.sites.gxebidding.parser import extract_pdf_text
from crawler_scrapy.storage.source_snapshots import (
    load_record_html,
    load_record_payload,
)


EXPECTED_SCHEMA = {
    "tender": "TENDER",
    "change": "CORRECTION",
    "candidate": "CANDIDATE",
    "award": "AWARD",
    "termination": "CORRECTION",
}


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _digest(path: Path, expected: str) -> str:
    algorithm = "sha256" if len(expected) == 64 else "md5"
    digest = hashlib.new(algorithm, usedforsecurity=False)
    with path.open("rb") as file_object:
        while chunk := file_object.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _evidenced(value: object, evidence: str) -> bool:
    compact = _compact(value)
    if not compact:
        return True
    return compact in evidence


def _amount_evidenced(value: object, evidence: str) -> bool:
    """允许数值和单位分处表头/单元格，但两者都必须存在于原文。"""

    compact = _compact(value).replace(",", "").replace("，", "")
    source = evidence.replace(",", "").replace("，", "")
    if not compact:
        return True
    numeric = re.search(r"[-+]?\d+(?:\.\d+)?", compact)
    if not numeric:
        return compact in source
    number = numeric.group()
    normalized_number = number.rstrip("0").rstrip(".") if "." in number else number
    found_number = number in source or normalized_number in source
    unit = compact[numeric.end() :]
    return found_number and (not unit or unit in source)


def _project_evidenced(value: object, evidence: str) -> bool:
    project = re.sub(r"[\[\]【】]", "", _compact(value))
    source = re.sub(r"[\[\]【】]", "", evidence)
    return not project or project in source


def audit(output_root: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    root = output_root.expanduser().resolve()
    site_dir = root / config.PLATFORM_CODE
    json_paths = sorted((site_dir / "json").glob("*.json"))
    errors: list[str] = []
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for path in json_paths:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, list):
            errors.append(f"{path.name}: JSON不是数组")
            continue
        rows.extend(value for value in values if isinstance(value, dict))
        csv_path = site_dir / "csv" / f"{path.stem}.csv"
        if not csv_path.is_file():
            errors.append(f"{path.name}: 缺少对应CSV")
        else:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
                csv_rows = list(csv.DictReader(source))
            if len(csv_rows) != len(values):
                errors.append(
                    f"{path.name}: JSON/CSV数量不同 {len(values)}/{len(csv_rows)}"
                )

    identities = [str(row.get("公告ID") or "") for row in rows]
    duplicates = [value for value, count in Counter(identities).items() if count > 1]
    if duplicates:
        errors.append(f"公告ID重复：{duplicates}")

    subtype_counts = Counter(str(row.get("公告子类型") or "") for row in rows)
    notice_type_counts: Counter[str] = Counter()
    attachment_statuses: Counter[str] = Counter()
    public_type_counts: Counter[str] = Counter()
    parsed = partial = identifier_checked = 0
    candidate_checked = award_checked = 0
    project_name_unproven = identifier_unproven = 0
    quoted_amount_checked = quoted_amount_unproven = 0
    field_coverage: dict[str, Counter[str]] = {}
    for row in rows:
        notice_id = str(row.get("公告ID") or "<missing>")
        subtype = str(row.get("公告子类型") or "")
        subtype_parts = subtype.split(".", 1)
        if (
            len(subtype_parts) != 2
            or subtype_parts[0] not in config.CHANNELS
            or subtype_parts[1] not in config.CATEGORIES
        ):
            errors.append(f"{notice_id}: 非法公告子类型 {subtype}")
            continue
        channel, category = subtype_parts
        notice_type = str(row.get("公告类型") or "")
        notice_type_counts[notice_type] += 1
        if notice_type != EXPECTED_SCHEMA[category]:
            errors.append(
                f"{notice_id}: {subtype}映射为{notice_type}，应为{EXPECTED_SCHEMA[category]}"
            )
        expected_nature = config.CHANNELS[channel]["project_nature"]
        if category in {"tender", "candidate", "award"} and row.get("项目性质") != expected_nature:
            errors.append(f"{notice_id}: 项目性质与频道不一致")
        if category in {"change", "termination"}:
            public_type_counts[str(row.get("公共类型") or "")] += 1
            if not row.get("公告内容"):
                errors.append(f"{notice_id}: 更正/终止公告缺少公告内容")

        row_attachments = row.get("附件")
        attachment_parse_status = ""
        if isinstance(row_attachments, list) and row_attachments:
            attachment_parse_status = str(
                row_attachments[0].get("parse_status") or ""
            )
        required_fields = [
            "平台名称", "平台代码", "公告ID", "公告类型", "公告子类型",
            "公告标题", "解析状态", "内容指纹", "抽取方式",
            "抽取版本", "详情页链接", "HTML快照路径", "HTML快照SHA256", "_trace",
        ]
        if attachment_parse_status != "DOWNLOADED_NO_OCR":
            required_fields.append("公告正文")
        for required in required_fields:
            if row.get(required) in (None, "", []):
                errors.append(f"{notice_id}: 缺少{required}")
        if row.get("平台代码") != config.PLATFORM_CODE:
            errors.append(f"{notice_id}: 平台代码错误")
        parsed += int(row.get("解析状态") == "PARSED")
        partial += int(row.get("解析状态") != "PARSED")
        for field in get_notice_fields(notice_type):
            if field not in row:
                errors.append(f"{notice_id}: Schema字段缺失 {field}")
            elif row.get(field) not in (None, "", [], {}):
                field_coverage.setdefault(notice_type, Counter())[field] += 1

        try:
            payload = load_record_payload(row, root)
            html = load_record_html(row, root)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"{notice_id}: 快照校验失败 {exc}")
            payload, html = None, ""
        if not isinstance(payload, dict):
            errors.append(f"{notice_id}: payload溯源快照缺失")
            payload = {}
        else:
            if payload.get("sourceChannel") != channel or payload.get("sourceCategory") != category:
                errors.append(f"{notice_id}: payload来源分类与公告子类型不一致")
        if "openFileById" not in str(html or ""):
            errors.append(f"{notice_id}: HTML快照不包含源站公开PDF定位信息")
        evidence = _compact(
            f"{row.get('公告正文', '')}\n{row.get('公告标题', '')}\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
        attachments_for_evidence = row.get("附件")
        if isinstance(attachments_for_evidence, list) and attachments_for_evidence:
            relative = str(attachments_for_evidence[0].get("storage_path") or "")
            pdf_path = (root / relative).resolve() if relative else None
            if pdf_path and root in pdf_path.parents and pdf_path.is_file():
                evidence += _compact(
                    extract_pdf_text(pdf_path.read_bytes(), timeout=180, mode="raw")
                )
        project_name = row.get("项目名称")
        if project_name and not _project_evidenced(project_name, evidence):
            project_name_unproven += 1
            errors.append(f"{notice_id}: 项目名称无法在正文或标题中溯源：{project_name}")
        if re.search(r"[\]】](?:（[^）]+）)?$", str(project_name or "")):
            errors.append(f"{notice_id}: 项目名称残留未配对方括号：{project_name}")
        for field in ("项目编号", "招标编号"):
            value = row.get(field)
            if not value:
                continue
            identifier_checked += 1
            if not _evidenced(value, evidence):
                identifier_unproven += 1
                errors.append(f"{notice_id}: {field}无法溯源：{value}")

        if notice_type == "CANDIDATE":
            candidate_checked += 1
            names, amounts = row.get("中标候选人名称"), row.get("中标候选人报价")
            if not isinstance(names, list) or not names:
                errors.append(f"{notice_id}: 未提取候选人")
            elif not isinstance(amounts, list) or len(names) != len(amounts):
                errors.append(f"{notice_id}: 候选人与报价未一一对应")
            for name in names or []:
                if not _evidenced(name, evidence):
                    errors.append(f"{notice_id}: 候选人无法溯源：{name}")
            for amount in amounts or []:
                if not amount:
                    continue
                quoted_amount_checked += 1
                if not _amount_evidenced(amount, evidence):
                    quoted_amount_unproven += 1
                    errors.append(f"{notice_id}: 候选人报价无法溯源：{amount}")
        if notice_type == "AWARD":
            award_checked += 1
            names, amounts = row.get("中标人名称"), row.get("中标价")
            if not isinstance(names, list) or not names:
                errors.append(f"{notice_id}: 未提取中标人")
            elif not isinstance(amounts, list) or len(names) != len(amounts):
                errors.append(f"{notice_id}: 中标人与中标价未一一对应")
            for name in names or []:
                if not _evidenced(name, evidence):
                    errors.append(f"{notice_id}: 中标人无法溯源：{name}")
            for amount in amounts or []:
                if not amount:
                    continue
                quoted_amount_checked += 1
                if not _amount_evidenced(amount, evidence):
                    quoted_amount_unproven += 1
                    errors.append(f"{notice_id}: 中标价无法溯源：{amount}")

        attachments = row.get("附件")
        if not isinstance(attachments, list) or len(attachments) != 1:
            errors.append(f"{notice_id}: 应登记且只登记1份公告PDF")
            continue
        attachment = attachments[0]
        if not isinstance(attachment, dict):
            errors.append(f"{notice_id}: 附件元数据不是对象")
            continue
        status = str(attachment.get("parse_status") or "")
        attachment_statuses[status] += 1
        relative = str(attachment.get("storage_path") or "")
        target = (root / relative).resolve() if relative else None
        if not target or root not in target.parents or not target.is_file():
            errors.append(f"{notice_id}: PDF不存在或路径非法 {relative}")
            continue
        if target.stat().st_size != attachment.get("file_size_bytes"):
            errors.append(f"{notice_id}: PDF大小与元数据不一致")
        expected_hash = str(attachment.get("file_hash") or "").lower()
        if len(expected_hash) not in {32, 64} or _digest(target, expected_hash) != expected_hash:
            errors.append(f"{notice_id}: PDF哈希不一致")
        with target.open("rb") as source:
            if source.read(5) != b"%PDF-":
                errors.append(f"{notice_id}: 附件不是PDF")
        if status == "DOWNLOADED_NO_OCR":
            warnings.append(f"{notice_id}: PDF无有效文字层，需OCR")

    for channel in config.CHANNELS:
        for category in config.CATEGORIES:
            subtype = f"{channel}.{category}"
            count = subtype_counts.get(subtype, 0)
            if count < 10:
                warnings.append(
                    f"{subtype}: 实际仅取得{count}条；可能源站不足10条或跨频道重复被稳定ID去重"
                )

    summary = {
        "jsonFiles": len(json_paths),
        "notices": len(rows),
        "noticeTypeCounts": dict(sorted(notice_type_counts.items())),
        "subtypeCounts": dict(sorted(subtype_counts.items())),
        "publicTypeCounts": dict(sorted(public_type_counts.items())),
        "parsed": parsed,
        "partial": partial,
        "identifierChecked": identifier_checked,
        "identifierUnproven": identifier_unproven,
        "projectNameUnproven": project_name_unproven,
        "quotedAmountChecked": quoted_amount_checked,
        "quotedAmountUnproven": quoted_amount_unproven,
        "candidateChecked": candidate_checked,
        "awardChecked": award_checked,
        "fieldCoverage": {
            notice_type: dict(sorted(coverage.items()))
            for notice_type, coverage in sorted(field_coverage.items())
        },
        "attachmentStatuses": dict(sorted(attachment_statuses.items())),
        "errors": len(errors),
        "warnings": len(warnings),
    }
    return summary, errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计国信e采验证输出")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    args = parser.parse_args(argv)
    summary, errors, warnings = audit(args.output_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for message in errors:
        print(f"[错误] {message}")
    for message in warnings:
        print(f"[警告] {message}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
