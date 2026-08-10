"""修复华新/玖邦既有 JSON、CSV 中的项目编号与招标编号。

两个站点使用相同的 TWS 公告模板和解析器。本脚本只改结构化编号、组合编号、
抽取版本与对应溯源元数据；公告正文、原始 HTML、原始响应和内容指纹保持不变。
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Mapping

from crawler_scrapy.sites.huaxin.parser import (
    HuaxinParser,
    _clean_identifier_value,
    _labelled_identifier,
)
from crawler_scrapy.sites.jiubang.parser import JiubangParser


PROJECT_LABELS = ("招标项目编号", "项目编号", "投资项目统一代码", "项目代码")
TENDER_LABELS = ("招标编号", "采购编号", "代理编号")
VERSIONS = {
    "huaxin": HuaxinParser.parser_version,
    "jiubang": JiubangParser.parser_version,
}
COMBINED_FIELDS = ("项目编号/招标编号", "招标编号/项目编号")
PROSE_MARKERS = (
    "资金来源", "项目资金来源", "招标人", "采购人", "已由", "本项目",
    "经评标委员会", "经评审", "现将", "现对",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_object:
        while chunk := file_object.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as file_object:
        json.dump(value, file_object, ensure_ascii=False, indent=2)
        file_object.write("\n")
        file_object.flush()
        os.fsync(file_object.fileno())
    temporary.replace(path)


def _trace(row: Mapping[str, Any]) -> Mapping[str, Any]:
    trace = row.get("_trace")
    return trace if isinstance(trace, Mapping) else {}


def _detail(row: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = _trace(row).get("payload")
    detail = payload.get("detail") if isinstance(payload, Mapping) else None
    return detail if isinstance(detail, Mapping) else {}


def _raw_text(row: Mapping[str, Any]) -> str:
    return str(row.get("公告正文") or _trace(row).get("rawText") or "")


def _direct_identifiers(row: Mapping[str, Any]) -> tuple[str, str]:
    text = _raw_text(row)
    detail = _detail(row)
    project = _labelled_identifier(text, PROJECT_LABELS) or _clean_identifier_value(
        detail.get("diyProjectNo"), PROJECT_LABELS
    )
    tender = _labelled_identifier(text, TENDER_LABELS) or _clean_identifier_value(
        detail.get("purDiyCode"), TENDER_LABELS
    )
    return project, tender


def _valid_project_identifier(value: str) -> bool:
    if not value or len(value) > 128 or any(marker in value for marker in PROSE_MARKERS):
        return False
    # 全国公共资源交易平台 E/M 类代码固定为一个字母加 19 位数字。
    if re.fullmatch(r"[EM]\d+", value, flags=re.IGNORECASE):
        return len(value) == 20
    return bool(re.search(r"[A-Za-z0-9]", value))


def _combined(project: str, tender: str) -> str:
    values: list[str] = []
    for value in (project, tender):
        if value and value not in values:
            values.append(value)
    return "；".join(values)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"公告 JSON 必须是对象数组：{path}")
    return rows


def _update_csv(path: Path, changes: Mapping[str, Mapping[str, Any]]) -> None:
    if not path.exists():
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    with temporary.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            change = changes.get(str(row.get("公告ID") or ""))
            if change:
                for field in ("项目编号", "招标编号", *COMBINED_FIELDS, "抽取版本"):
                    if field in row and field in change:
                        row[field] = change[field]
                if "缺失字段" in row and change.get("filledFields"):
                    try:
                        missing = json.loads(row["缺失字段"] or "[]")
                    except json.JSONDecodeError:
                        missing = []
                    row["缺失字段"] = json.dumps(
                        [field for field in missing if field not in change["filledFields"]],
                        ensure_ascii=False,
                    )
            writer.writerow(row)
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


def _scan_site(site_dir: Path) -> tuple[list[Path], dict[tuple[Path, str], tuple[str, str]], dict[str, set[str]]]:
    paths = sorted((site_dir / "json").glob("*.json"))
    direct: dict[tuple[Path, str], tuple[str, str]] = {}
    projects_by_tender: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        for row in _load_rows(path):
            notice_id = str(row.get("公告ID") or "")
            project, tender = _direct_identifiers(row)
            direct[(path, notice_id)] = (project, tender)
            if tender and _valid_project_identifier(project):
                projects_by_tender[tender].add(project)
    return paths, direct, projects_by_tender


def _rewrite_site(output_root: Path, site: str) -> dict[str, Any]:
    site_dir = output_root / site
    paths, direct, projects_by_tender = _scan_site(site_dir)
    if not paths:
        raise FileNotFoundError(f"没有找到 {site} JSON：{site_dir / 'json'}")

    version = VERSIONS[site]
    report: dict[str, Any] = {
        "site": site,
        "version": version,
        "records": 0,
        "changedRecords": 0,
        "projectChanged": 0,
        "tenderChanged": 0,
        "inferredFromTender": 0,
        "files": [],
        "changes": [],
    }
    for path in paths:
        before = _sha256(path)
        rows = _load_rows(path)
        csv_changes: dict[str, dict[str, Any]] = {}
        file_changed = 0
        for row in rows:
            notice_id = str(row.get("公告ID") or "")
            project, tender = direct[(path, notice_id)]
            source = "raw_text_or_detail"
            if project and not _valid_project_identifier(project):
                candidates = projects_by_tender.get(tender, set())
                if tender and len(candidates) == 1:
                    project = next(iter(candidates))
                    source = "same_tender_unique_project"
                    report["inferredFromTender"] += 1
                else:
                    project = ""
                    source = "invalid_source_value_removed"

            old_project = str(row.get("项目编号") or "")
            old_tender = str(row.get("招标编号") or "")
            old_version = str(row.get("抽取版本") or "")
            values_changed = old_project != project or old_tender != tender
            filled_fields = [
                field for field, old, new in (
                    ("项目编号", old_project, project),
                    ("招标编号", old_tender, tender),
                ) if not old and new
            ]

            row["项目编号"] = project
            row["招标编号"] = tender
            combined = _combined(project, tender)
            for field in COMBINED_FIELDS:
                if field in row:
                    row[field] = combined
            row["抽取版本"] = version
            missing = row.get("缺失字段")
            if isinstance(missing, list) and filled_fields:
                row["缺失字段"] = [field for field in missing if field not in filled_fields]
            trace = row.get("_trace")
            if isinstance(trace, dict):
                trace["extractionVersion"] = version
                field_meta = trace.setdefault("fieldMeta", {})
                if isinstance(field_meta, dict):
                    field_meta["identifierNormalization"] = {
                        "version": "tws-identifiers-v2",
                        "projectSource": source,
                        "tenderSource": "raw_text_or_detail" if tender else "missing",
                    }

            csv_change = {
                "项目编号": project,
                "招标编号": tender,
                "抽取版本": version,
                "filledFields": filled_fields,
            }
            for field in COMBINED_FIELDS:
                if field in row:
                    csv_change[field] = combined
            csv_changes[notice_id] = csv_change

            report["records"] += 1
            if values_changed:
                file_changed += 1
                report["changedRecords"] += 1
                report["projectChanged"] += old_project != project
                report["tenderChanged"] += old_tender != tender
                report["changes"].append({
                    "file": path.name,
                    "noticeId": notice_id,
                    "title": row.get("公告标题", ""),
                    "oldProject": old_project,
                    "newProject": project,
                    "oldTender": old_tender,
                    "newTender": tender,
                    "projectSource": source,
                    "oldVersion": old_version,
                    "newVersion": version,
                })

        _atomic_json(path, rows)
        _update_csv(site_dir / "csv" / f"{path.stem}.csv", csv_changes)
        report["files"].append({
            "file": path.name,
            "records": len(rows),
            "changedRecords": file_changed,
            "beforeSha256": before,
            "afterSha256": _sha256(path),
        })
        print(
            f"[编号修复] {site}/{path.name}: 总数={len(rows)} 修改={file_changed}",
            flush=True,
        )
    return report


def run(output_root: Path, sites: tuple[str, ...]) -> int:
    reports = [_rewrite_site(output_root, site) for site in sites]
    state_dir = output_root / "state" / "tws_identifier_backfill_v2"
    state_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(state_dir / "manifest.json", reports)
    for report in reports:
        print(
            f"[编号修复完成] {report['site']}: 总数={report['records']} "
            f"修改={report['changedRecords']} 项目编号修改={report['projectChanged']} "
            f"招标编号修改={report['tenderChanged']} "
            f"同招标编号纠错={report['inferredFromTender']}",
            flush=True,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修复华新/玖邦既有 JSON、CSV 编号")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    parser.add_argument("--sites", default="huaxin,jiubang")
    args = parser.parse_args(argv)
    sites = tuple(site.strip() for site in args.sites.split(",") if site.strip())
    unsupported = set(sites) - set(VERSIONS)
    if unsupported:
        parser.error(f"不支持的网站：{','.join(sorted(unsupported))}")

    output_root = args.output_root.expanduser().resolve()
    with ExitStack() as stack:
        for site in sorted(sites):
            lock_path = output_root / site / "state" / "resumable.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = stack.enter_context(lock_path.open("a+"))
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"{site} 正在运行，拒绝修改现有 JSON/CSV", file=sys.stderr)
                return 5
        return run(output_root, sites)


if __name__ == "__main__":
    raise SystemExit(main())
