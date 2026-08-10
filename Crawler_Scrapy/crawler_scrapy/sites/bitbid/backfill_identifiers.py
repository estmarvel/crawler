"""按 v2 规则流式修复既有 Bitbid JSON/CSV 编号字段。"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from crawler_scrapy.sites.bitbid import config
from crawler_scrapy.sites.bitbid.parser import BitbidParser, valid_identifier
from crawler_scrapy.sites.sxzwfw.backfill_identifiers import (
    _atomic_json,
    _csv_value,
    _json_array,
    _sha256,
)


VERSION = BitbidParser.parser_version
PROJECT_LABELS = (
    "招标项目编号", "投资项目统一代码", "项目代码", "采购项目编号", "项目编号",
)
TENDER_LABELS = ("招标编号", "采购编号", "代理编号")
COMBINED_FIELDS = ("项目编号/招标编号", "招标编号/项目编号")


def _raw_text(row: Mapping[str, Any]) -> str:
    if row.get("公告正文"):
        return str(row["公告正文"])
    trace = row.get("_trace")
    return str(trace.get("rawText") or "") if isinstance(trace, Mapping) else ""


def _combined(project: str, tender: str) -> str:
    return "；".join(dict.fromkeys(value for value in (project, tender) if value))


def _valid_identifier(value: str) -> bool:
    return valid_identifier(value)


def _update(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    text = _raw_text(row)
    old_project = str(row.get("项目编号") or "")
    old_tender = str(row.get("招标编号") or "")
    old_notice_id = str(row.get("公告ID") or "")
    category = str(row.get("公告子类型") or "").strip().lower()
    raw_notice_id = old_notice_id.split(":", 1)[-1]
    new_notice_id = config.source_notice_id(category, raw_notice_id)
    project_from_text = BitbidParser._identifier_label(text, *PROJECT_LABELS)
    tender_from_text = BitbidParser._identifier_label(text, *TENDER_LABELS)
    project = (
        project_from_text if _valid_identifier(project_from_text) else
        old_project if _valid_identifier(old_project) else ""
    )
    tender = (
        tender_from_text if _valid_identifier(tender_from_text) else
        old_tender if _valid_identifier(old_tender) else ""
    )
    combined = _combined(project, tender)
    old_combined = {
        field: str(row.get(field) or "") for field in COMBINED_FIELDS if field in row
    }
    row["项目编号"] = project
    row["招标编号"] = tender
    row["公告ID"] = new_notice_id
    for field in old_combined:
        row[field] = combined
    row["抽取版本"] = VERSION

    filled = {
        field for field, value in (
            ("项目编号", project), ("招标编号", tender),
            *((field, combined) for field in old_combined),
        ) if value
    }
    missing = row.get("缺失字段")
    if isinstance(missing, list):
        row["缺失字段"] = [field for field in missing if field not in filled]

    trace = row.get("_trace")
    if isinstance(trace, dict):
        trace["crawlerVersion"] = VERSION
        trace["extractionVersion"] = VERSION
        field_meta = trace.setdefault("fieldMeta", {})
        if isinstance(field_meta, dict):
            field_meta["site_parser"] = VERSION
            field_meta["identifierExtraction"] = {
                "version": "bitbid-identifiers-v2",
                "projectSource": (
                    "raw_text_label" if project_from_text else
                    "api_or_previous_valid_value" if project else "missing"
                ),
                "tenderSource": (
                    "raw_text_label" if tender_from_text else
                    "api_or_previous_valid_value" if tender else "missing"
                ),
            }
            field_meta["sourceIdentity"] = {
                "version": "bitbid-category-id-v1",
                "category": category,
                "rawNoticeId": raw_notice_id,
                "sourceNoticeId": new_notice_id,
            }
        export = trace.get("exportMetadata")
        if isinstance(export, dict):
            export["missingFields"] = list(row.get("缺失字段") or [])
            export["sourceNoticeId"] = new_notice_id

    changed = (
        old_notice_id != new_notice_id
        or old_project != project
        or old_tender != tender
        or any(old_combined[field] != combined for field in old_combined)
    )
    return changed, {
        "oldNoticeId": old_notice_id,
        "noticeId": new_notice_id,
        "oldProject": old_project,
        "project": project,
        "oldTender": old_tender,
        "tender": tender,
        "combined": combined,
    }


def _rebuild_notice_versions(paths: list[Path], target: Path) -> int:
    identities: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in _json_array(path):
            notice_id = str(row.get("公告ID") or "")
            identity = f"bitbid|id:{notice_id}"
            if identity in identities:
                raise ValueError(f"Bitbid 新公告身份仍重复：{notice_id}")
            trace = row.get("_trace") if isinstance(row.get("_trace"), Mapping) else {}
            field_meta = trace.get("fieldMeta") if isinstance(trace.get("fieldMeta"), Mapping) else {}
            seen_at = str(
                row.get("爬虫时间")
                or datetime.now().isoformat(sep=" ", timespec="seconds")
            )
            identities[identity] = {
                "platform_code": "bitbid",
                "notice_id": notice_id,
                "detail_url": str(row.get("详情页链接") or ""),
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
                "list_fingerprint": str(field_meta.get("_dedup_list_fingerprint") or ""),
                "content_fingerprints": [str(row.get("内容指纹") or "")],
            }
    _atomic_json(
        target,
        {
            "format_version": 1,
            "updated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "identities": identities,
        },
    )
    return len(identities)


def _rewrite(path: Path, dry_run: bool) -> dict[str, Any]:
    csv_path = path.parents[1] / "csv" / f"{path.stem}.csv"
    fieldnames: list[str] = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
            fieldnames = next(csv.reader(source), [])
    json_temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    csv_temp = csv_path.with_name(f".{csv_path.name}.{os.getpid()}.tmp")
    json_target = None
    csv_target = None
    csv_writer = None
    if not dry_run:
        json_target = json_temp.open("w", encoding="utf-8")
        json_target.write("[\n")
        if fieldnames:
            csv_target = csv_temp.open("w", encoding="utf-8-sig", newline="")
            csv_writer = csv.DictWriter(csv_target, fieldnames=fieldnames, extrasaction="ignore")
            csv_writer.writeheader()
    report: dict[str, Any] = {
        "file": path.name,
        "records": 0,
        "changedRecords": 0,
        "projectFilled": 0,
        "tenderFilled": 0,
        "examples": [],
    }
    first = True
    try:
        for row in _json_array(path):
            changed, detail = _update(row)
            report["records"] += 1
            report["projectFilled"] += bool(detail["project"])
            report["tenderFilled"] += bool(detail["tender"])
            if changed:
                report["changedRecords"] += 1
                if len(report["examples"]) < 20:
                    report["examples"].append(detail)
            if json_target is not None:
                if not first:
                    json_target.write(",\n")
                json.dump(row, json_target, ensure_ascii=False, indent=2)
                first = False
            if csv_writer is not None:
                csv_writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})
            if report["records"] % 1000 == 0:
                print(f"[Bitbid 编号修复] {path.name}: {report['records']} 条", flush=True)
        if json_target is not None:
            json_target.write("\n]\n")
            json_target.flush()
            os.fsync(json_target.fileno())
        if csv_target is not None:
            csv_target.flush()
            os.fsync(csv_target.fileno())
    finally:
        if json_target is not None:
            json_target.close()
        if csv_target is not None:
            csv_target.close()
    if not dry_run:
        report["beforeSha256"] = _sha256(path)
        json_temp.replace(path)
        report["afterSha256"] = _sha256(path)
        if fieldnames:
            csv_temp.replace(csv_path)
    return report


def run(output_root: Path, dry_run: bool) -> int:
    site_dir = output_root / "bitbid"
    paths = sorted((site_dir / "json").glob("*.json"))
    if not paths:
        print(f"没有找到 Bitbid JSON：{site_dir / 'json'}", file=sys.stderr)
        return 2
    manifest = {"site": "bitbid", "version": VERSION, "dryRun": dry_run, "files": []}
    for path in paths:
        report = _rewrite(path, dry_run)
        manifest["files"].append(report)
        print(
            f"[Bitbid 编号修复完成] {path.name}: 总数={report['records']} "
            f"修改={report['changedRecords']} 项目编号={report['projectFilled']} "
            f"招标编号={report['tenderFilled']}",
            flush=True,
        )
    state_dir = site_dir / "state" / "backfill_identifiers_v3"
    state_dir.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        manifest["dedupIdentities"] = _rebuild_notice_versions(
            paths, site_dir / "state" / "notice_versions.json"
        )
    _atomic_json(state_dir / ("dry_run_manifest.json" if dry_run else "manifest.json"), manifest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修复 Bitbid 既有公告编号和栏目化源站身份")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    lock_path = output_root / "bitbid" / "state" / "resumable.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Bitbid 正在运行，拒绝修改现有 JSON/CSV", file=sys.stderr)
            return 5
        return run(output_root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
