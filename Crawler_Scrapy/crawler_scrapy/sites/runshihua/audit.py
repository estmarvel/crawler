"""审计润世和 JSON、溯源快照、附件及关键字段的一致性。"""

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
from crawler_scrapy.sites.runshihua import config
from crawler_scrapy.storage.source_snapshots import (
    load_record_html,
    load_record_payload,
)


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _digest(path: Path, expected: str) -> str:
    algorithm = "sha256" if len(expected) == 64 else "md5"
    digest = hashlib.new(algorithm, usedforsecurity=False)
    with path.open("rb") as file_object:
        while chunk := file_object.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def audit(output_root: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    site_dir = output_root / config.PLATFORM_CODE
    paths = sorted((site_dir / "json").glob("*.json"))
    errors: list[str] = []
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for path in paths:
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
    duplicates = [key for key, count in Counter(identities).items() if count > 1]
    if duplicates:
        errors.append(f"公告ID重复：{duplicates}")

    subtype_counts = Counter(str(row.get("公告子类型") or "") for row in rows)
    attachment_statuses: Counter[str] = Counter()
    parsed = partial = 0
    identifier_checked = 0
    identifier_unproven = 0
    candidate_checked = award_checked = 0
    for row in rows:
        notice_id = str(row.get("公告ID") or "<missing>")
        for required in (
            "平台代码", "公告ID", "公告类型", "公告标题", "公告正文",
            "解析状态", "内容指纹", "抽取版本", "详情页链接", "_trace",
        ):
            if row.get(required) in (None, "", []):
                errors.append(f"{notice_id}: 缺少{required}")
        if row.get("平台代码") != config.PLATFORM_CODE:
            errors.append(f"{notice_id}: 平台代码错误")
        parsed += int(row.get("解析状态") == "PARSED")
        partial += int(row.get("解析状态") != "PARSED")

        notice_type = str(row.get("公告类型") or "")
        for field in get_notice_fields(notice_type):
            if field not in row:
                errors.append(f"{notice_id}: Schema字段缺失 {field}")

        try:
            payload = load_record_payload(row, output_root)
            load_record_html(row, output_root)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"{notice_id}: 快照校验失败 {exc}")
            payload = None
        if not isinstance(payload, dict):
            errors.append(f"{notice_id}: 缺少payload溯源快照")
            payload = {}
        evidence = _compact(
            f"{row.get('公告正文', '')}\n{row.get('公告标题', '')}\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
        for field in ("项目编号", "招标编号"):
            value = _compact(row.get(field))
            if not value:
                continue
            identifier_checked += 1
            if value not in evidence:
                identifier_unproven += 1
                errors.append(f"{notice_id}: {field}无法在正文或payload中溯源：{value}")

        names = row.get("中标候选人名称")
        prices = row.get("中标候选人报价")
        if notice_type == "CANDIDATE":
            candidate_checked += 1
            if not isinstance(names, list) or not names:
                errors.append(f"{notice_id}: 未提取候选人")
            elif not isinstance(prices, list) or len(names) != len(prices):
                errors.append(f"{notice_id}: 候选人与报价没有一一对应")
            for name in names or []:
                if _compact(name) not in evidence:
                    errors.append(f"{notice_id}: 候选人无法在正文中溯源：{name}")

        winners = row.get("中标人名称")
        amounts = row.get("中标价")
        if notice_type == "AWARD":
            award_checked += 1
            if not isinstance(winners, list) or not winners:
                errors.append(f"{notice_id}: 未提取中标人")
            elif not isinstance(amounts, list) or len(winners) != len(amounts):
                errors.append(f"{notice_id}: 中标人与中标价没有一一对应")
            for name in winners or []:
                if _compact(name) not in evidence:
                    errors.append(f"{notice_id}: 中标人无法在正文中溯源：{name}")

        attachments = row.get("附件")
        if not isinstance(attachments, list) or not attachments:
            errors.append(f"{notice_id}: 未登记公告PDF")
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict):
                errors.append(f"{notice_id}: 附件元数据不是对象")
                continue
            status = str(attachment.get("parse_status") or "")
            attachment_statuses[status] += 1
            relative = str(attachment.get("storage_path") or "")
            target = (output_root / relative).resolve() if relative else None
            if not target or output_root.resolve() not in target.parents or not target.is_file():
                errors.append(f"{notice_id}: 附件文件不存在或路径非法 {relative}")
                continue
            if target.stat().st_size != attachment.get("file_size_bytes"):
                errors.append(f"{notice_id}: 附件大小不一致")
            expected_hash = str(attachment.get("file_hash") or "").lower()
            if len(expected_hash) not in {32, 64} or _digest(target, expected_hash) != expected_hash:
                errors.append(f"{notice_id}: 附件哈希不一致")
            if target.read_bytes()[:5] != b"%PDF-":
                errors.append(f"{notice_id}: 附件不是PDF")
            if status == "DOWNLOADED_NO_OCR":
                warnings.append(f"{notice_id}: PDF无有效文字层，需OCR才能读取PDF正文")

    summary = {
        "jsonFiles": len(paths),
        "notices": len(rows),
        "subtypeCounts": dict(sorted(subtype_counts.items())),
        "parsed": parsed,
        "partial": partial,
        "identifierChecked": identifier_checked,
        "identifierUnproven": identifier_unproven,
        "candidateChecked": candidate_checked,
        "awardChecked": award_checked,
        "attachmentStatuses": dict(sorted(attachment_statuses.items())),
        "errors": len(errors),
        "warnings": len(warnings),
    }
    return summary, errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计润世和爬取输出")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    args = parser.parse_args(argv)
    summary, errors, warnings = audit(args.output_root.expanduser().resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for message in errors:
        print(f"[错误] {message}")
    for message in warnings:
        print(f"[警告] {message}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

