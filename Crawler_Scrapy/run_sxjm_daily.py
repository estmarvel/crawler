"""山西焦煤每日增量采集与累计公告统计。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def collect_notice_ids(output_root: Path) -> set[tuple[str, str]]:
    """按“频道/项目性质 + 公告ID”统计已经保存的唯一公告。"""

    identities: set[tuple[str, str]] = set()
    json_dirs = {output_root / "sxjm" / "json"}
    json_dirs.update(PROJECT_ROOT.glob("output_sxjm*/sxjm/json"))
    paths = (path for directory in json_dirs for path in directory.glob("*.json"))
    for path in paths:
        if ".before_fix" in path.name:
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            notice_id = str(row.get("公告ID") or "").strip()
            if not notice_id:
                continue
            channel = str(row.get("项目性质") or path.stem.split("_", 1)[0]).strip()
            identities.add((channel, notice_id))
    return identities


def run_once(args: argparse.Namespace) -> dict[str, object]:
    output_root = Path(args.output_root).resolve()
    before = collect_notice_ids(output_root)
    started_at = datetime.now()
    target_date = (started_at - timedelta(days=1)).date()
    start_date = datetime.combine(target_date, datetime_time.min)
    end_date = datetime.combine(target_date, datetime_time.max)
    command = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        "sxjm",
        "-a",
        f"channels={args.channels}",
        "-a",
        f"start_date={start_date.strftime('%Y-%m-%d %H:%M:%S')}",
        "-a",
        f"end_date={end_date.strftime('%Y-%m-%d %H:%M:%S')}",
        "-a",
        f"max_records={args.max_records}",
        "-a",
        f"max_pages={args.max_pages}",
        "-a",
        f"page_size={args.page_size}",
        "-s",
        f"NOTICE_OUTPUT_ROOT={output_root}",
        "-s",
        "NOTICE_DEDUP_ENABLED=True",
        "-s",
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED=False",
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    after = collect_notice_ids(output_root)
    finished_at = datetime.now()
    report = {
        "started_at": started_at.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": finished_at.strftime("%Y-%m-%d %H:%M:%S"),
        "target_date": target_date.isoformat(),
        "success": completed.returncode == 0,
        "new_notices": len(after - before),
        "total_notices": len(after),
        "return_code": completed.returncode,
    }
    state_dir = output_root / "sxjm" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "daily_statistics.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(report, ensure_ascii=False) + "\n")

    status = "成功" if report["success"] else "失败"
    print("\n========== 山西焦煤每日爬取统计 ==========")
    print(f"运行状态：{status}")
    print(f"采集日期：{report['target_date']} 00:00:00 至 23:59:59")
    print(f"运行时间：{report['started_at']} 至 {report['finished_at']}")
    print(f"本次爬取公告数：{report['new_notices']} 条")
    print(f"截止目前累计公告数：{report['total_notices']} 条")
    print(f"统计记录：{state_dir / 'daily_statistics.jsonl'}")
    print("==========================================")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="山西焦煤每日增量爬取")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "output"))
    parser.add_argument("--channels", default="yfxm,zbxm,fzxm,jycg")
    parser.add_argument("--max-records", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--loop", action="store_true", help="持续运行，每天到点执行")
    parser.add_argument("--run-hour", type=int, default=1, choices=range(24))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.loop:
        return 0 if run_once(args)["success"] else 1
    while True:
        now = datetime.now()
        next_run = datetime.combine(now.date(), datetime_time(args.run_hour))
        if next_run <= now:
            next_run += timedelta(days=1)
        print(f"下一次运行时间：{next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep((next_run - now).total_seconds())
        run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
