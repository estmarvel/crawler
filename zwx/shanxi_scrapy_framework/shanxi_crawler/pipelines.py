import csv
import json
from pathlib import Path

from shanxi_crawler.ai_service import write_ai_report
from shanxi_crawler.columns import COLUMNS, DEFAULT_VALUE, SITE_NAME
from shanxi_crawler.text_utils import is_empty_value, append_unique, clean_value, normalize_publish_cell


class ShanxiCsvUpsertPipeline:
    def __init__(self, settings=None):
        self.settings = settings

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def open_spider(self):
        output_dir = Path(self.settings.get("CRAWLER_OUTPUT_DIR", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = output_dir / "山西.csv"
        self.jsonl_path = output_dir / "山西.jsonl"
        self.rows = []
        self.index = {}
        if self.csv_path.exists() and self.csv_path.stat().st_size > 0:
            with self.csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for raw in reader:
                    row = self._normalize_row(raw)
                    self._add_or_replace(row)
        self.jsonl_file = self.jsonl_path.open("a", encoding="utf-8")

    def close_spider(self):
        tmp = self.csv_path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            for row in self.rows:
                writer.writerow(self._normalize_row(row))
        tmp.replace(self.csv_path)
        self.jsonl_file.close()
        write_ai_report(self.settings.get("LOG_DIR", "logs"))

    def process_item(self, item):
        row = self._normalize_row(dict(item))
        self._upsert(row)
        self.jsonl_file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.jsonl_file.flush()
        return item

    def _normalize_row(self, row: dict) -> dict:
        out = {col: clean_value(row.get(col, DEFAULT_VALUE)) for col in COLUMNS}
        out["发布网站"] = SITE_NAME
        out["依据文件"] = out.get("依据文件") or "无"
        out["依据文号"] = out.get("依据文号") or "无"
        out["发布日期"] = normalize_publish_cell(out.get("发布日期", DEFAULT_VALUE))
        return out

    def _add_or_replace(self, row: dict):
        name = row.get("项目名称", DEFAULT_VALUE)
        if name in self.index:
            self.rows[self.index[name]] = row
        else:
            self.index[name] = len(self.rows)
            self.rows.append(row)

    def _upsert(self, new_row: dict):
        name = new_row.get("项目名称", DEFAULT_VALUE)
        if is_empty_value(name):
            name = new_row.get("公告历史", DEFAULT_VALUE)
        if name not in self.index:
            self.index[name] = len(self.rows)
            self.rows.append(new_row)
            return
        old = self.rows[self.index[name]]
        for col in COLUMNS:
            new_val = new_row.get(col, DEFAULT_VALUE)
            old_val = old.get(col, DEFAULT_VALUE)
            if col in ["公告类型", "发布日期", "公告历史"]:
                old[col] = append_unique(old_val, new_val)
                if col == "发布日期":
                    old[col] = normalize_publish_cell(old[col])
                continue
            if col in ["公告内容", "开标时间", "标书发售时间"]:
                if is_empty_value(old_val) and not is_empty_value(new_val):
                    old[col] = new_val
                continue
            if not is_empty_value(new_val):
                old[col] = new_val
        old["发布网站"] = SITE_NAME
        self.rows[self.index[name]] = self._normalize_row(old)
