"""按当前规则修复已保存 SXZWFW JSON/CSV 中的编号和金额字段。

脚本只重算编号、组合编号、缺失字段和对应抽取版本元数据；公告正文、原始
HTML、响应元数据、快照路径、快照哈希及内容指纹保持不变。JSON 采用流式
读写，避免加载数 GB 文件；替换使用同目录临时文件和原子 rename。
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO

from crawler_scrapy.sites.sxzwfw.parser import (
    PROJECT_IDENTIFIER_LABELS,
    TENDER_IDENTIFIER_LABELS,
    SxzwfwParser,
    _identifier_values,
    _labelled_identifiers,
    _monetary_label_paragraph,
    _project_investment,
    _project_numbers,
)
from crawler_scrapy.schemas.notice_fields import coerce_decimal_amount


COMBINED_FIELDS = ("项目编号/招标编号", "招标编号/项目编号")
VERSION = SxzwfwParser.parser_version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fill(source: TextIO, buffer: str, position: int) -> tuple[str, int, bool]:
    if position:
        buffer = buffer[position:]
        position = 0
    chunk = source.read(1024 * 1024)
    return buffer + chunk, position, not chunk


def _json_array(path: Path) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as source:
        buffer = ""
        position = 0
        eof = False
        started = False
        expect_value = True
        while True:
            while True:
                while position < len(buffer) and buffer[position].isspace():
                    position += 1
                if position < len(buffer) or eof:
                    break
                buffer, position, eof = _fill(source, buffer, position)

            if not started:
                if eof and position >= len(buffer):
                    raise ValueError(f"空 JSON 文件：{path}")
                if buffer[position] != "[":
                    raise ValueError(f"公告 JSON 不是数组：{path}")
                started = True
                position += 1
                continue

            while position >= len(buffer) and not eof:
                buffer, position, eof = _fill(source, buffer, position)
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position >= len(buffer):
                if eof:
                    raise ValueError(f"公告 JSON 数组未闭合：{path}")
                continue

            if not expect_value:
                character = buffer[position]
                if character == "]":
                    return
                if character != ",":
                    raise ValueError(f"公告 JSON 数组分隔符无效：{path}")
                position += 1
                expect_value = True
                continue

            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer) and buffer[position] == "]":
                return
            try:
                value, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                if eof:
                    raise
                buffer, position, eof = _fill(source, buffer, position)
                continue
            if not isinstance(value, dict):
                raise ValueError(f"公告记录不是对象：{path}")
            position = end
            expect_value = False
            yield value


def _raw_text(row: Mapping[str, Any]) -> str:
    value = row.get("公告正文")
    if value:
        return str(value)
    trace = row.get("_trace")
    if isinstance(trace, Mapping) and trace.get("rawText"):
        return str(trace["rawText"])
    return ""


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else value


def _update_row(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    text = _raw_text(row)
    project_values = _identifier_values(text, PROJECT_IDENTIFIER_LABELS)
    tender_values = _identifier_values(text, TENDER_IDENTIFIER_LABELS)
    project = project_values[0] if project_values else ""
    tender = tender_values[0] if tender_values else ""
    combined = _project_numbers(text)

    old_project = str(row.get("项目编号") or "")
    old_tender = str(row.get("招标编号") or "")
    old_combined = {
        field: str(row.get(field) or "") for field in COMBINED_FIELDS if field in row
    }
    row["项目编号"] = project
    row["招标编号"] = tender
    for field in old_combined:
        row[field] = combined

    amount_changes: dict[str, tuple[Any, Any]] = {}
    if "项目总投资/估算金额" in row:
        extracted = _project_investment(text)
        normalized = coerce_decimal_amount(extracted)
        if normalized is not None:
            old_value = row.get("项目总投资/估算金额")
            new_value = float(normalized)
            row["项目总投资/估算金额"] = new_value
            amount_changes["项目总投资/估算金额"] = (old_value, new_value)
    if "招标金额" in row:
        extracted = _monetary_label_paragraph(
            text,
            (
                "最高投标限价总价", "招标控制总价", "财政审定金额",
                "本次招标金额", "招标金额", "最高投标限价", "最高限价",
                "招标控制价",
            ),
        )
        normalized = coerce_decimal_amount(extracted)
        if normalized is not None:
            old_value = row.get("招标金额")
            new_value = float(normalized)
            row["招标金额"] = new_value
            amount_changes["招标金额"] = (old_value, new_value)
    row["抽取版本"] = VERSION

    filled = {
        field for field, value in (
            ("项目编号", project),
            ("招标编号", tender),
            *((field, combined) for field in old_combined),
        ) if value
    }
    filled.update(
        field for field, (old, new) in amount_changes.items()
        if old in (None, "") and new not in (None, "")
    )
    missing = row.get("缺失字段")
    if isinstance(missing, list):
        row["缺失字段"] = [field for field in missing if field not in filled]

    trace = row.get("_trace")
    if isinstance(trace, dict):
        trace["crawlerVersion"] = VERSION
        trace["extractionVersion"] = VERSION
        payload = trace.get("payload")
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict):
                detail["parserVersion"] = VERSION
        field_meta = trace.setdefault("fieldMeta", {})
        if isinstance(field_meta, dict):
            field_meta["site_parser"] = VERSION
            field_meta["identifierExtraction"] = {
                "version": "sxzwfw-identifiers-v2",
                "projectSource": "raw_text_label" if project else "missing",
                "tenderSource": "raw_text_label" if tender else "missing",
                "projectCandidates": project_values,
                "tenderCandidates": tender_values,
                "combinedIdentifiers": combined.split("；") if combined else [],
            }
        export = trace.get("exportMetadata")
        if isinstance(export, dict):
            export["missingFields"] = list(row.get("缺失字段") or [])

    changed = (
        old_project != project
        or old_tender != tender
        or any(old_combined[field] != combined for field in old_combined)
        or any(old != new for old, new in amount_changes.values())
    )
    details = {
        "noticeId": str(row.get("公告ID") or ""),
        "oldProject": old_project,
        "project": project,
        "oldTender": old_tender,
        "tender": tender,
        "combined": combined,
        "amountChanges": {
            field: {"old": old, "new": new}
            for field, (old, new) in amount_changes.items() if old != new
        },
    }
    return changed, details


def _rewrite(path: Path, dry_run: bool) -> dict[str, Any]:
    csv_path = path.parents[1] / "csv" / f"{path.stem}.csv"
    fieldnames: list[str] = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
            fieldnames = list(csv.reader(source).__next__())

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
        "projectPollutionRemoved": 0,
        "tenderPollutionRemoved": 0,
        "amountChanged": 0,
        "examples": [],
    }
    first = True
    try:
        for row in _json_array(path):
            changed, detail = _update_row(row)
            report["records"] += 1
            report["projectFilled"] += bool(detail["project"])
            report["tenderFilled"] += bool(detail["tender"])
            report["projectPollutionRemoved"] += bool(
                detail["oldProject"]
                and detail["oldProject"] != detail["project"]
                and ("|" in detail["oldProject"] or len(detail["oldProject"]) > 128)
            )
            report["tenderPollutionRemoved"] += bool(
                detail["oldTender"]
                and detail["oldTender"] != detail["tender"]
                and ("|" in detail["oldTender"] or len(detail["oldTender"]) > 128)
            )
            report["amountChanged"] += bool(detail["amountChanges"])
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
                csv_writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})
            if report["records"] % 500 == 0:
                print(
                    f"[SXZWFW 编号修复] {path.name}: {report['records']} 条",
                    flush=True,
                )
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


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


def run(output_root: Path, dry_run: bool) -> int:
    site_dir = output_root / "sxzwfw"
    paths = sorted((site_dir / "json").glob("*.json"))
    if not paths:
        print(f"没有找到 SXZWFW JSON：{site_dir / 'json'}", file=sys.stderr)
        return 2
    manifest: dict[str, Any] = {
        "site": "sxzwfw",
        "version": VERSION,
        "dryRun": dry_run,
        "files": [],
    }
    for path in paths:
        report = _rewrite(path, dry_run)
        manifest["files"].append(report)
        print(
            f"[SXZWFW 编号修复完成] {path.name}: 总数={report['records']} "
            f"修改={report['changedRecords']} 项目编号={report['projectFilled']} "
            f"招标编号={report['tenderFilled']}",
            flush=True,
        )
    state_dir = site_dir / "state" / "backfill_fields_v7"
    state_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(
        state_dir / ("dry_run_manifest.json" if dry_run else "manifest.json"),
        manifest,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修复 SXZWFW 既有公告编号")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    lock_path = output_root / "sxzwfw" / "state" / "resumable.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("SXZWFW 正在运行，拒绝修改现有 JSON/CSV", file=sys.stderr)
            return 5
        return run(output_root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
