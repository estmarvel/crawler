from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from scrapy.exceptions import DropItem

from crawler_scrapy.ai.validation_quota import AiValidationQuotaPipeline
from crawler_scrapy.spiders.qianji import QianjiSpider


class _Stats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key):
        self.values[key] = self.values.get(key, 0) + 1

    def set_value(self, key, value):
        self.values[key] = value


def test_validation_quota_is_noop_when_disabled():
    pipeline = AiValidationQuotaPipeline(
        crawler=SimpleNamespace(stats=_Stats(), spider=SimpleNamespace()), limit=0
    )
    item = {"notice_type": "招标公告", "notice_subtype": "tender.engineering"}
    assert pipeline.process_item(item) is item


def test_qianji_validation_quota_groups_project_types_by_source_category():
    spider = QianjiSpider(categories="tender", project_types="engineering")
    crawler = SimpleNamespace(stats=_Stats(), spider=spider)
    pipeline = AiValidationQuotaPipeline(crawler=crawler, limit=2)
    pipeline.spider_opened(spider)

    first = {"notice_type": "招标公告", "notice_subtype": "tender.engineering"}
    second = {"notice_type": "招标公告", "notice_subtype": "tender.goods"}
    third = {"notice_type": "招标公告", "notice_subtype": "tender.service"}
    assert pipeline.process_item(first) is first
    assert pipeline.process_item(second) is second
    with pytest.raises(DropItem):
        pipeline.process_item(third)
    assert crawler.stats.values["validation_quota/accepted/tender"] == 2
    assert crawler.stats.values["validation_quota/dropped/tender"] == 1


def test_qianji_validation_quota_restores_existing_json_after_restart(tmp_path):
    json_dir = tmp_path / "qianji" / "json"
    json_dir.mkdir(parents=True)
    (json_dir / "千极链_招标公告.json").write_text(
        json.dumps(
            [
                {
                    "平台代码": "qianji",
                    "公告ID": "one",
                    "公告类型": "TENDER",
                    "公告子类型": "tender.engineering",
                },
                {
                    "平台代码": "qianji",
                    "公告ID": "two",
                    "公告类型": "TENDER",
                    "公告子类型": "tender.goods",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    spider = QianjiSpider(categories="tender", project_types="engineering")
    crawler = SimpleNamespace(stats=_Stats(), spider=spider)
    pipeline = AiValidationQuotaPipeline(
        crawler=crawler, limit=2, output_root=tmp_path
    )

    pipeline.spider_opened(spider)

    assert pipeline.counts["tender"] == 2
    assert crawler.stats.values["validation_quota/resumed/tender"] == 2
    with pytest.raises(DropItem):
        pipeline.process_item(
            {
                "notice_type": "招标公告",
                "notice_subtype": "tender.service",
            }
        )


def test_validation_quota_restore_deduplicates_repeated_notice_versions(tmp_path):
    json_dir = tmp_path / "demo" / "json"
    json_dir.mkdir(parents=True)
    repeated = {
        "平台代码": "demo",
        "公告ID": "same-id",
        "公告类型": "TENDER",
    }
    (json_dir / "03_招标公告.json").write_text(
        json.dumps([repeated, repeated], ensure_ascii=False), encoding="utf-8"
    )
    spider = SimpleNamespace(
        platform_code="demo",
        name="demo",
        ai_extract_fields={"招标公告": ()},
        logger=SimpleNamespace(info=lambda *args: None, warning=lambda *args: None),
    )
    crawler = SimpleNamespace(stats=_Stats(), spider=spider)
    pipeline = AiValidationQuotaPipeline(
        crawler=crawler, limit=2, output_root=tmp_path
    )

    pipeline.spider_opened(spider)

    assert pipeline.counts["招标公告"] == 1
    assert crawler.stats.values["validation_quota/resumed/招标公告"] == 1
