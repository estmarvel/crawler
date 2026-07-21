#!/usr/bin/env python3
"""Export selected fields from a Huaxin award-results JSON file to CSV."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


FIELDS = ["项目名称", "所属行业", "招标人/采购人", "招标代理机构", "详情页链接"]
DEFAULT_INPUT = Path("output/huaxin/json/06_中标结果公示.json")
DEFAULT_OUTPUT = Path("output/huaxin/csv/06_中标结果公示.csv")


def export_csv(input_path: Path, output_path: Path) -> int:
    with input_path.open("r", encoding="utf-8") as input_file:
        records: Any = json.load(input_file)

    if not isinstance(records, list):
        raise ValueError("JSON 顶层必须是公告记录数组。")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("JSON 数组中的每条公告记录必须是对象。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: record.get(field, "") for field in FIELDS} for record in records)

    return len(records)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出华新中标结果公示的指定字段到 CSV。")
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT, help="输入 JSON 文件路径")
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT, help="输出 CSV 文件路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    exported_count = export_csv(args.input, args.output)
    print(f"已导出 {exported_count} 条记录到：{args.output}")


if __name__ == "__main__":
    main()
