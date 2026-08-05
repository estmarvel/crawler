from __future__ import annotations

import unittest
import hashlib
import json

from scrapy import Request
from scrapy.http import HtmlResponse

from crawler_scrapy.spiders.base_notice import BaseNoticeSpider
from crawler_scrapy.schemas.notice_fields import (
    ANNOUNCEMENT_SCHEMAS,
    PARSER_DIAGNOSTIC_FIELDS,
    SYSTEM_FIELDS,
)


class ExampleNoticeSpider(BaseNoticeSpider):
    name = "example_notice"
    platform_name = "示例平台"
    platform_code = "example"


class NoticeItemDatabaseFieldsTest(unittest.TestCase):
    def test_excel_required_field_contract_cannot_be_silently_deleted(self):
        # 项目爬取关键字段20260622.xlsx 的有序字段摘要。资格预审表中重复的
        # “招标代理机构”按 JSON 唯一键只计一次；扩展字段不参与摘要。
        expected = {
            "招标计划": (13, "ed308aea6a66010d166379df54b25a296c421ed2fe23b719fc68b2147f5b7a17"),
            "资格预审公告": (32, "16784aac3c5dcd615cda9a3c67e242f7c5c2491c6b7b24bc27a7d20dd1b0e944"),
            "招标公告": (35, "f0003ee377051211c2e0ccee4ed8ab20f5ab79574b2a5819f32bb24372c63b31"),
            "中标候选人公示": (19, "a485f04289e658e7d4f1ef202bf064f79eafd2215346919d8b9e68b2fcb44d8d"),
            "定标候选人公示": (27, "f640a0f5f83548d04ef99d91b8ebe6fca62010df481c3428829babf2cbe3d58b"),
            "中标结果公示": (24, "1524aa2db9e5d884a601b4b7abdd603bc60239253d9bb20a3f53384794b51479"),
            "更正结果公示": (21, "6fb09874be00b98855a62fd45e88b5fe5fff40a7079ba6b8756b3c7ca12f60b3"),
            "合同与履约": (11, "14a3e5074a09c94c74c9599f1ae572fc02e9cfae6a4cbdeb4bc2de77ddc5a569"),
        }
        extensions = {
            notice_type: {"项目编号", "招标编号"}
            for notice_type in ANNOUNCEMENT_SCHEMAS
        }
        # “合同与履约”的项目编号本来就在 Excel 必需字段中，本次只新增招标编号。
        extensions["合同与履约"].remove("项目编号")
        extensions["资格预审公告"].add("源站公告性质")
        extensions["招标公告"].add("源站公告性质")
        extensions["中标候选人公示"].update(
            {"源站公告性质", "中标候选人明细"}
        )
        extensions["中标结果公示"].update(
            {"源站公告性质", "中标结果明细"}
        )
        for notice_type, fields in ANNOUNCEMENT_SCHEMAS.items():
            self.assertIn("项目编号", fields)
            self.assertIn("招标编号", fields)
            excel_fields = [
                field for field in fields
                if field not in extensions.get(notice_type, set())
            ]
            digest = hashlib.sha256(
                json.dumps(
                    excel_fields,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual((len(excel_fields), digest), expected[notice_type])
            self.assertTrue(set(fields).isdisjoint(SYSTEM_FIELDS))
            self.assertTrue(
                set(fields).isdisjoint(
                    PARSER_DIAGNOSTIC_FIELDS.get(notice_type, ())
                )
            )

    def test_database_metadata_is_top_level_not_duplicated_in_business_data(self):
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
        self.assertNotIn("公告正文", data)
        self.assertNotIn("解析状态", data)
        self.assertNotIn("内容指纹", data)
        self.assertNotIn("抽取方式", data)
        self.assertNotIn("抽取版本", data)
        self.assertNotIn("是否已核验", data)

        self.assertEqual(item["raw_text"], "测试正文")
        self.assertEqual(item["parse_status"], "PARSED")
        self.assertEqual(len(item["fingerprint"]), 64)
        self.assertEqual(item["extraction_model"], "rule-parser")
        self.assertEqual(item["extraction_version"], "rule-v1")
        self.assertIs(item["is_verified"], False)
        self.assertEqual(item["notice_type"], "TENDER")
        self.assertNotIn("schema_version", item)

    def test_response_trace_metadata_excludes_credentials(self):
        spider = ExampleNoticeSpider()
        request = Request(
            "https://user:password@example.invalid/api/notices/1?access_token=secret&section=zbgg",
            headers={"Authorization": "Bearer secret", "Cookie": "token=secret"},
            meta={"download_latency": 0.25, "retry_times": 1},
        )
        response = HtmlResponse(
            url=request.url,
            request=request,
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8", "ETag": "v1"},
            body=b"<p>source</p>",
            encoding="utf-8",
        )

        metadata = spider.build_response_metadata(
            response,
            request_kind="detail_page",
            context={"section": "zbgg"},
        )

        self.assertEqual(
            metadata["request"]["url"],
            "https://example.invalid/api/notices/1?access_token=%5BREDACTED%5D&section=zbgg",
        )
        self.assertEqual(metadata["response"]["status"], 200)
        self.assertEqual(metadata["download"]["retryTimes"], 1)
        self.assertEqual(metadata["context"]["section"], "zbgg")
        serialized = str(metadata).lower()
        self.assertNotIn("authorization", serialized)
        self.assertNotIn("cookie", serialized)
        self.assertNotIn("secret", serialized)

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
