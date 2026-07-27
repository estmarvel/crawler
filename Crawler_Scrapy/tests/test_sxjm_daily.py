from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "run_sxjm_daily.py"
SPEC = importlib.util.spec_from_file_location("run_sxjm_daily", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_collect_notice_ids_counts_unique_channel_and_id(tmp_path):
    MODULE.PROJECT_ROOT = tmp_path
    json_dir = tmp_path / "sxjm" / "json"
    json_dir.mkdir(parents=True)
    rows = [
        {"项目性质": "依法项目", "公告ID": "1"},
        {"项目性质": "依法项目", "公告ID": "1"},
        {"项目性质": "非招项目", "公告ID": "1"},
        {"项目性质": "非招项目", "公告ID": "2"},
    ]
    (json_dir / "公告.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    assert MODULE.collect_notice_ids(tmp_path) == {
        ("依法项目", "1"),
        ("非招项目", "1"),
        ("非招项目", "2"),
    }


def test_previous_calendar_day_is_used(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, "PROJECT_ROOT", tmp_path)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    args = Namespace(
        output_root=str(tmp_path), channels="yfxm,zbxm,fzxm,jycg",
        max_records=1000, max_pages=100, page_size=50,
    )
    report = MODULE.run_once(args)
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    assert report["target_date"] == yesterday
    joined = " ".join(captured["command"])
    assert f"start_date={yesterday} 00:00:00" in joined
    assert f"end_date={yesterday} 23:59:59" in joined
