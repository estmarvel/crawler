"""使用已保存的 HTML 溯源包原地重算山西交控测试 JSON/CSV 字段。"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Any

from crawler_scrapy.pipelines import _to_json_compatible
from crawler_scrapy.schemas.notice_fields import (
    canonicalize_notice_data,
    get_missing_fields,
    get_notice_fields,
)
from crawler_scrapy.sites.sxjkzcpt.parser import SxjkzcptParser


def _atomic_json(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as file_object:
        json.dump(_to_json_compatible(rows), file_object, ensure_ascii=False, indent=2)
        file_object.write("\n")
        file_object.flush()
        os.fsync(file_object.fileno())
    temporary.replace(path)


def _serialize(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(_to_json_compatible(value), ensure_ascii=False)
    return value if value is not None else ""


def _sync_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        fieldnames = list(csv.DictReader(source).fieldnames or [])
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _serialize(row.get(name, "")) for name in fieldnames})
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


def reparse(output_root: Path) -> int:
    site_dir = output_root / "sxjkzcpt"
    paths = sorted((site_dir / "json").glob("*.json"))
    if not paths:
        print(f"没有找到测试JSON：{site_dir / 'json'}", file=sys.stderr)
        return 2
    total = changed = 0
    for path in paths:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"JSON不是数组：{path}")
        for row in rows:
            trace = row.get("_trace") if isinstance(row, dict) else None
            payload = trace.get("payload") if isinstance(trace, dict) else None
            raw_html = trace.get("rawHtml") if isinstance(trace, dict) else None
            feed = payload.get("sourceFeed") if isinstance(payload, dict) else None
            list_record = payload.get("list") if isinstance(payload, dict) else None
            if not raw_html or not feed:
                raise ValueError(f"缺少rawHtml/sourceFeed，无法安全重算：{path}")
            parsed = SxjkzcptParser.parse(
                str(feed), raw_html, list_record=list_record if isinstance(list_record, dict) else {}
            )
            data = canonicalize_notice_data(parsed.notice_type, parsed.data)
            comparable_data = _to_json_compatible(data)
            before = {field: row.get(field) for field in get_notice_fields(parsed.notice_type)}
            for field, value in data.items():
                row[field] = value
            row["缺失字段"] = get_missing_fields(parsed.notice_type, data)
            row["抽取版本"] = SxjkzcptParser.parser_version
            trace["crawlerVersion"] = SxjkzcptParser.parser_version
            trace["extractionVersion"] = SxjkzcptParser.parser_version
            export = trace.get("exportMetadata")
            if isinstance(export, dict):
                export["missingFields"] = list(row["缺失字段"])
            field_meta = trace.get("fieldMeta")
            if isinstance(field_meta, dict):
                field_meta["snapshotReparsedWith"] = SxjkzcptParser.parser_version
            total += 1
            changed += before != {
                field: comparable_data.get(field) for field in before
            }
        _atomic_json(path, rows)
        _sync_csv(site_dir / "csv" / f"{path.stem}.csv", rows)
        print(f"[快照重算] {path.name}: {len(rows)}条", flush=True)
    print(f"[快照重算完成] 总数={total} 字段有变化={changed}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从HTML快照重算山西交控测试结果")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    args = parser.parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    lock_path = output_root / "sxjkzcpt" / "state" / "resumable.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("sxjkzcpt 正在运行，拒绝修改JSON/CSV", file=sys.stderr)
            return 5
        return reparse(output_root)


if __name__ == "__main__":
    raise SystemExit(main())
