from __future__ import annotations

import unittest

from crawler_scrapy.schemas.notice_fields import DATABASE_CRAWLER_FIELDS
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class ExampleNoticeSpider(BaseNoticeSpider):
    name = "example_notice"
    platform_name = "示例平台"
    platform_code = "example"


class NoticeItemDatabaseFieldsTest(unittest.TestCase):
    def test_all_database_crawler_fields_are_populated_in_notice_data(self):
        spider = ExampleNoticeSpider()
        item = spider.build_notice_item(
            notice_type="招标公告",
            notice_id="notice-1",
            title="测试公告",
            publish_time="2026-07-15 10:20:30",
            detail_url="https://example.invalid/notice-1",
            data={"项目名称": "测试项目"},
            raw_html="<p>测试正文</p>",
            raw_text="测试正文",
            parse_status="PARSED",
            extraction_model="rule-parser",
            extraction_version="rule-v1",
        )

        data = item["data"]
        self.assertTrue(set(DATABASE_CRAWLER_FIELDS).issubset(data))
        self.assertEqual(data["公告正文"], "测试正文")
        self.assertEqual(data["解析状态"], "PARSED")
        self.assertEqual(len(data["内容指纹"]), 64)
        self.assertEqual(data["抽取方式"], "rule-parser")
        self.assertEqual(data["抽取版本"], "rule-v1")
        self.assertIs(data["是否已核验"], False)

        self.assertEqual(item["raw_text"], "测试正文")
        self.assertEqual(item["fingerprint"], data["内容指纹"])
        self.assertEqual(item["notice_type"], "TENDER")
        self.assertNotIn("schema_version", item)

    def test_structured_only_notices_still_have_distinct_fingerprints(self):
        spider = ExampleNoticeSpider()
        first = spider.build_notice_item(
            notice_type="招标计划",
            notice_id="plan-1",
            data={"项目名称": "计划一", "项目总投资": "100万元"},
        )
        second = spider.build_notice_item(
            notice_type="招标计划",
            notice_id="plan-2",
            data={"项目名称": "计划二", "项目总投资": "200万元"},
        )
        self.assertEqual(len(first["fingerprint"]), 64)
        self.assertEqual(len(second["fingerprint"]), 64)
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])


if __name__ == "__main__":
    unittest.main()
