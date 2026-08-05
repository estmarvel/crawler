"""从已保存的 SXJM 正文/原始详情回填 v8 项目编号与招标编号。"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from crawler_scrapy.schemas.notice_fields import (
    ANNOUNCEMENT_TYPES,
    NOTICE_SCHEMA_VERSION,
    SYSTEM_FIELDS,
    get_notice_fields,
    normalize_notice_type,
)


META_COLUMNS = (
    "平台名称", "平台代码", "公告ID", "公告类型", "公告子类型", "公告标题", "发布时间"
)


def _label(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*[：:]\s*([A-Za-z0-9][A-Za-z0-9._/-]{{2,190}})"
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip("：:()（）")
            if value:
                return value
    return ""


def _code(value: Any) -> str:
    match = re.search(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{2,190}",
        str(value or "").strip(),
    )
    return match.group(0) if match else ""


def _detail(row: Mapping[str, Any]) -> Mapping[str, Any]:
    trace = row.get("_trace")
    payload = trace.get("payload") if isinstance(trace, Mapping) else None
    detail = payload.get("detail") if isinstance(payload, Mapping) else None
    return detail if isinstance(detail, Mapping) else {}


def _identifiers(row: Mapping[str, Any]) -> tuple[str, str, dict[str, str]]:
    text = str(row.get("公告正文") or "")
    detail = _detail(row)
    project_from_text = _label(
        text,
        ("招标项目编号", "投资项目统一代码", "项目代码", "项目编号"),
    )
    tender_from_text = _label(text, ("招标编号", "采购编号"))
    project = project_from_text or _code(detail.get("invest_project_code"))
    tender = tender_from_text or _code(detail.get("tender_number"))
    sources = {
        "项目编号": (
            "raw_text_label" if project_from_text else
            "detail.invest_project_code" if detail.get("invest_project_code") else "missing"
        ),
        "招标编号": (
            "raw_text_label" if tender_from_text else
            "detail.tender_number" if detail.get("tender_number") else "missing"
        ),
    }
    return project, tender, sources


def _schema_type(row: Mapping[str, Any]) -> str:
    normalized = normalize_notice_type(row.get("公告类型"))
    if normalized in ANNOUNCEMENT_TYPES:
        return normalized
    subtype = str(row.get("公告子类型") or "")
    if subtype.endswith(".zbjh"):
        return "招标计划"
    if subtype.endswith((".hxr", ".cjhxr")):
        return "中标候选人公示"
    if subtype.endswith((".zbjg", ".cjgg")):
        return "中标结果公示"
    return "招标公告"


def _ordered_row(row: dict[str, Any], schema_type: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in META_COLUMNS:
        result[field] = row.get(field, "")
    for field in get_notice_fields(schema_type):
        result[field] = row.get(field, "")
    result["缺失字段"] = [
        field for field in row.get("缺失字段") or []
        if field not in {"项目编号", "招标编号"}
    ]
    for field in SYSTEM_FIELDS:
        result[field] = row.get(field, [] if field == "附件" else "")
    if "_trace" in row:
        result["_trace"] = row["_trace"]
    return result


def _write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as file_object:
        json.dump(rows, file_object, ensure_ascii=False, indent=2)
        file_object.write("\n")
        file_object.flush()
        os.fsync(file_object.fileno())
    temporary.replace(path)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]], schema_type: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [*META_COLUMNS, *get_notice_fields(schema_type), "缺失字段", *SYSTEM_FIELDS]
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as file_object:
        writer = csv.DictWriter(file_object, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})
        file_object.flush()
        os.fsync(file_object.fileno())
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_object:
        while chunk := file_object.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run(output_root: Path) -> int:
    site_dir = output_root / "sxjm"
    json_paths = sorted((site_dir / "json").glob("*.json"))
    if not json_paths:
        print(f"没有找到 SXJM JSON：{site_dir / 'json'}", file=sys.stderr)
        return 2
    manifest: dict[str, Any] = {
        "schemaVersion": NOTICE_SCHEMA_VERSION,
        "files": [],
        "total": 0,
        "projectNumber": 0,
        "tenderNumber": 0,
    }
    for path in json_paths:
        before = _sha256(path)
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"JSON 不是数组：{path}")
        schema_type = _schema_type(rows[0]) if rows else "招标公告"
        normalized: list[dict[str, Any]] = []
        project_count = tender_count = 0
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError(f"公告记录不是对象：{path}")
            project, tender, sources = _identifiers(raw)
            raw["项目编号"] = project
            raw["招标编号"] = tender
            project_count += bool(project)
            tender_count += bool(tender)
            trace = raw.get("_trace")
            if isinstance(trace, dict):
                trace["noticeSchemaVersion"] = NOTICE_SCHEMA_VERSION
                field_meta = trace.setdefault("fieldMeta", {})
                if isinstance(field_meta, dict):
                    field_meta["identifierExtraction"] = {
                        "version": "sxjm-identifiers-v1",
                        "sources": sources,
                    }
            normalized.append(_ordered_row(raw, schema_type))
        _write_json(path, normalized)
        _write_csv(site_dir / "csv" / f"{path.stem}.csv", normalized, schema_type)
        after = _sha256(path)
        manifest["files"].append(
            {
                "file": path.name,
                "records": len(normalized),
                "projectNumber": project_count,
                "tenderNumber": tender_count,
                "beforeSha256": before,
                "afterSha256": after,
            }
        )
        manifest["total"] += len(normalized)
        manifest["projectNumber"] += project_count
        manifest["tenderNumber"] += tender_count
        print(
            f"[编号回填] {path.name}: 总数={len(normalized)} "
            f"项目编号={project_count} 招标编号={tender_count}",
            flush=True,
        )
    state_dir = site_dir / "state" / "backfill_identifiers_v8"
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(state_dir / "manifest.json", [manifest])
    print(
        f"[编号回填完成] 总数={manifest['total']} 项目编号={manifest['projectNumber']} "
        f"招标编号={manifest['tenderNumber']}",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线回填既有 SXJM JSON/CSV 的两个编号字段")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    args = parser.parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    lock_path = output_root / "sxjm" / "state" / "resumable.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("SXJM 正在运行，拒绝修改现有 JSON/CSV", file=sys.stderr)
            return 5
        return run(output_root)


if __name__ == "__main__":
    raise SystemExit(main())
