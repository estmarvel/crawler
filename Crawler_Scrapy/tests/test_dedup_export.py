from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scrapy.exceptions import DropItem
from scrapy.settings import Settings

from crawler_scrapy.pipelines import (
    NoticeDedupPipeline,
    NoticeMultiFormatPipeline,
    NoticeSchemaPipeline,
)
from crawler_scrapy.storage.dedup import (
    JsonNoticeDedupStore,
    build_list_fingerprint,
    build_notice_identity,
)
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class _Stats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key, count=1):
        self.values[key] = self.values.get(key, 0) + count


class _Crawler:
    def __init__(self, spider, output_root: Path, dedup_root: Path | None = None):
        self.spider = spider
        self.settings = Settings(
            {
                "NOTICE_OUTPUT_ROOT": str(output_root),
                "NOTICE_DEDUP_ENABLED": True,
                "NOTICE_EXPORT_INCLUDE_META": True,
                "NOTICE_EXPORT_DIAGNOSTICS": True,
                "NOTICE_EXPORT_EMPTY_FILES": False,
            }
        )
        if dedup_root is not None:
            self.settings.set("NOTICE_DEDUP_ROOT", str(dedup_root))
        self.stats = _Stats()


class _ExportSpider(BaseNoticeSpider):
    name = "dedup_export"
    platform_name = "去重测试平台"
    platform_code = "dedup_export"


class DedupStoreTest(unittest.TestCase):
    def test_list_fingerprint_ignores_view_count_but_keeps_business_changes(self):
        original = {
            "annId": "1001",
            "annTitle": "公告A",
            "releaseTime": "2026-07-16 10:00:00",
            "clickTimes": 10,
        }
        viewed = {**original, "clickTimes": 999}
        changed = {**original, "annTitle": "公告A（变更）"}

        self.assertEqual(
            build_list_fingerprint(original),
            build_list_fingerprint(viewed),
        )
        self.assertNotEqual(
            build_list_fingerprint(original),
            build_list_fingerprint(changed),
        )

    def test_identity_and_content_versions_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notice_versions.json"
            store = JsonNoticeDedupStore(path)
            identity = build_notice_identity(
                platform_code="site-a", notice_id="1001"
            )
            list_v1 = build_list_fingerprint({"id": "1001", "title": "公告A"})
            list_v2 = build_list_fingerprint({"id": "1001", "title": "公告A变更"})

            self.assertTrue(store.reserve(identity, "content-v1"))
            store.commit(
                identity=identity,
                content_fingerprint="content-v1",
                platform_code="site-a",
                notice_id="1001",
                list_fingerprint=list_v1,
            )
            self.assertTrue(store.has_identity(identity))
            self.assertFalse(store.has_identity("site-a|id:missing"))
            self.assertFalse(store.reserve(identity, "content-v1"))
            self.assertFalse(store.should_fetch_detail(identity, list_v1))
            self.assertTrue(store.should_fetch_detail(identity, list_v2))

            self.assertTrue(store.reserve(identity, "content-v2"))
            store.commit(
                identity=identity,
                content_fingerprint="content-v2",
                list_fingerprint=list_v2,
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            fingerprints = saved["identities"][identity]["content_fingerprints"]
            self.assertEqual(fingerprints, ["content-v1", "content-v2"])


class AppendExportTest(unittest.TestCase):
    def _pipelines(self, output_root: Path):
        spider = _ExportSpider()
        crawler = _Crawler(spider, output_root)
        dedup = NoticeDedupPipeline.from_crawler(crawler)
        schema = NoticeSchemaPipeline.from_crawler(crawler)
        exporter = NoticeMultiFormatPipeline.from_crawler(crawler)
        dedup.open_spider()
        exporter.open_spider()
        return spider, crawler, dedup, schema, exporter

    @staticmethod
    def _item(spider, text: str):
        return spider.build_notice_item(
            notice_type="招标公告",
            notice_id="notice-1001",
            title="测试项目招标公告",
            detail_url="https://example.invalid/notices/1001",
            data={"项目名称": "测试项目", "发布网站": "去重测试平台"},
            raw_html=f"<p>{text}</p>",
            raw_text=text,
            extraction_model="rule-test",
        )

    @staticmethod
    def _process(dedup, schema, exporter, item):
        item = dedup.process_item(item)
        item = schema.process_item(item)
        return exporter.process_item(item)

    def test_cross_run_duplicate_is_skipped_and_changed_content_is_appended(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)

            spider, _, dedup, schema, exporter = self._pipelines(output_root)
            self._process(
                dedup,
                schema,
                exporter,
                self._item(spider, "正文版本一"),
            )
            exporter.close_spider()

            # 模拟下一次 Scrapy 进程：索引从磁盘重新加载。
            spider2, crawler2, dedup2, schema2, exporter2 = self._pipelines(
                output_root
            )
            with self.assertRaises(DropItem):
                dedup2.process_item(self._item(spider2, "正文版本一"))

            self._process(
                dedup2,
                schema2,
                exporter2,
                self._item(spider2, "正文版本二"),
            )
            exporter2.close_spider()

            site_dir = output_root / "dedup_export"
            json_path = site_dir / "json" / "03_招标公告.json"
            csv_path = site_dir / "csv" / "03_招标公告.csv"
            state_path = site_dir / "state" / "notice_versions.json"

            rows = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                [row["公告正文"] for row in rows],
                ["正文版本一", "正文版本二"],
            )
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            self.assertEqual(len(csv_rows), 2)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            identity = "dedup_export|id:notice-1001"
            self.assertEqual(
                len(state["identities"][identity]["content_fingerprints"]),
                2,
            )
            self.assertEqual(crawler2.stats.values["dedup/duplicate_versions"], 1)

    def test_disabled_snapshots_are_not_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            spider = _ExportSpider()
            crawler = _Crawler(spider, output_root)
            crawler.settings.set("NOTICE_SNAPSHOT_ENABLED", False)
            schema = NoticeSchemaPipeline.from_crawler(crawler)
            item = self._item(spider, "不保存快照的正文")

            result = schema.process_item(item)

            self.assertNotIn("HTML快照路径", result["missing_fields"])
            self.assertNotIn("HTML快照SHA256", result["missing_fields"])

    def test_dedup_state_can_be_shared_outside_task_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_output = root / "tasks" / "42"
            shared_state = root / "shared"
            spider = _ExportSpider()
            crawler = _Crawler(spider, task_output, shared_state)
            dedup = NoticeDedupPipeline.from_crawler(crawler)

            dedup.open_spider()

            self.assertEqual(
                dedup.store.path,
                shared_state / "dedup_export" / "state" / "notice_versions.json",
            )
            self.assertFalse((task_output / "dedup_export" / "state").exists())


if __name__ == "__main__":
    unittest.main()
