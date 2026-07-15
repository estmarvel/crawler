from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from itemadapter import ItemAdapter

from crawler_scrapy.ai.html_extractor import (
    AiExtractionConfig,
    AiExtractionResult,
    AiHtmlExtractionService,
    html_to_text,
)
from crawler_scrapy.pipelines import AiHtmlExtractionPipeline
from crawler_scrapy.schemas.notice_fields import get_missing_fields
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider
from crawler_scrapy.spiders.huaxin import HuaxinSpider


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.content))
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
        )


class _FakeClient:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


class _Stats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key, count=1):
        self.values[key] = self.values.get(key, 0) + count


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Crawler:
    def __init__(self):
        self.stats = _Stats()
        self.spider = SimpleNamespace(logger=_Logger())


class _ExampleSpider(BaseNoticeSpider):
    name = "ai_example"
    platform_name = "测试平台"
    platform_code = "ai_example"


class _SelectedFieldsSpider(_ExampleSpider):
    ai_extract_fields = {
        "招标公告": ("招标金额", "招标人/采购人名称"),
    }


class AiHtmlExtractorTest(unittest.TestCase):
    def test_html_to_text_removes_invisible_content_and_keeps_lines(self):
        value = html_to_text(
            "<div>项目名称：测试项目</div>"
            "<script>ignore()</script><p>招标人：测试公司&nbsp;</p>"
        )
        self.assertEqual(value, "项目名称：测试项目\n招标人：测试公司")

    def test_service_extracts_only_requested_keys(self):
        client = _FakeClient(
            "```json\n"
            '{"招标人/采购人名称":"测试公司","开标时间":"2026-08-01 09:30",'
            '"未请求字段":"不能进入结果"}'
            "\n```"
        )
        config = AiExtractionConfig(
            enabled=True,
            api_key="fake",
            min_interval_seconds=0,
            retry_times=0,
        )
        service = AiHtmlExtractionService(config, client=client)

        result = service.extract(
            notice_type="招标公告",
            title="测试项目招标公告",
            fields=["招标人/采购人名称", "开标时间"],
            text="<p>招标人为测试公司，开标时间为2026年8月1日09:30。</p>",
        )

        self.assertTrue(result.success)
        self.assertEqual(
            set(result.values), {"招标人/采购人名称", "开标时间"}
        )
        self.assertEqual(result.total_tokens, 120)
        call = client.completions.calls[0]
        self.assertEqual(call["temperature"], 0)
        self.assertNotIn("response_format", call)
        self.assertIn("目标字段", call["messages"][1]["content"])

    def test_service_keeps_both_ends_of_long_notice(self):
        config = AiExtractionConfig(
            enabled=True,
            api_key="fake",
            max_input_chars=1000,
        )
        service = AiHtmlExtractionService(config, client=_FakeClient("{}"))
        source = "开头" + ("中" * 1400) + "结尾联系方式"
        shortened = service._truncate_text(source)
        self.assertLess(len(shortened), len(source))
        self.assertTrue(shortened.startswith("开头"))
        self.assertTrue(shortened.endswith("结尾联系方式"))

    def test_pipeline_fills_blanks_without_overwriting_rules(self):
        spider = _ExampleSpider()
        item = spider.build_notice_item(
            notice_type="招标公告",
            notice_id="notice-ai-1",
            title="测试项目招标公告",
            data={
                "项目名称": "规则项目名",
                "招标人/采购人名称": "",
                "开标时间": "",
                "发布网站": "测试平台",
            },
            raw_html="<p>测试正文</p>",
            raw_text="测试正文",
            extraction_model="rule-parser",
        )
        item["missing_fields"] = get_missing_fields(
            "招标公告", item["data"], include_optional=False
        )

        pipeline = AiHtmlExtractionPipeline(
            config=AiExtractionConfig(enabled=True, api_key="fake"),
            service=None,
        )
        pipeline.crawler = _Crawler()
        result = AiExtractionResult(
            values={
                "项目名称": "AI不应覆盖的项目名",
                "招标人/采购人名称": "AI补充公司",
                "开标时间": "2026-08-01 09:30",
            },
            requested_fields=["项目名称", "招标人/采购人名称", "开标时间"],
            success=True,
            input_chars=20,
            attempts=1,
        )

        returned = pipeline._apply_result(result, item)
        adapter = ItemAdapter(returned)
        data = adapter["data"]
        self.assertEqual(data["项目名称"], "规则项目名")
        self.assertEqual(data["招标人/采购人名称"], "AI补充公司")
        self.assertEqual(data["开标时间"], datetime(2026, 8, 1, 9, 30))
        self.assertEqual(
            adapter["field_meta"]["ai_extraction"]["filled_fields"],
            ["招标人/采购人名称", "开标时间"],
        )
        self.assertEqual(adapter["extraction_model"], "rule-parser+AI:glm-4.6-thinking")

    def test_pipeline_never_sends_framework_fields_to_ai(self):
        pipeline = AiHtmlExtractionPipeline(
            config=AiExtractionConfig(enabled=True, api_key="fake"),
            service=None,
        )
        spider = _ExampleSpider()
        item = spider.build_notice_item(
            notice_type="招标公告",
            data={"项目名称": ""},
            raw_text="正文",
        )
        item["missing_fields"] = [
            "项目名称",
            "发布日期",
            "发布网站",
            "详情页链接",
            "HTML快照路径",
        ]
        targets = pipeline._target_fields(
            "招标公告", item["data"], ItemAdapter(item)
        )
        self.assertEqual(targets, ["项目名称"])

    def test_site_can_select_fields_freely_for_each_notice_type(self):
        pipeline = AiHtmlExtractionPipeline(
            config=AiExtractionConfig(enabled=True, api_key="fake"),
            service=None,
        )
        crawler = _Crawler()
        crawler.spider = _SelectedFieldsSpider()
        pipeline.crawler = crawler
        item = crawler.spider.build_notice_item(
            notice_type="招标公告",
            data={},
            raw_text="正文",
        )
        item["missing_fields"] = [
            "项目名称",
            "招标金额",
            "招标人/采购人名称",
        ]
        targets = pipeline._target_fields(
            "招标公告", item["data"], ItemAdapter(item)
        )
        self.assertEqual(targets, ["招标金额", "招标人/采购人名称"])

    def test_huaxin_ai_only_receives_html_business_fields(self):
        pipeline = AiHtmlExtractionPipeline(
            config=AiExtractionConfig(
                enabled=True,
                api_key="fake",
                include_optional_fields=True,
            ),
            service=None,
        )
        crawler = _Crawler()
        crawler.spider = HuaxinSpider()
        pipeline.crawler = crawler
        item = crawler.spider.build_notice_item(
            notice_type="招标公告",
            data={"项目名称": "结构化项目名称"},
            raw_text="公告正文",
        )
        targets = pipeline._target_fields(
            "招标公告", item["data"], ItemAdapter(item)
        )
        self.assertIn("招标金额", targets)
        self.assertIn("招标代理机构联系方式", targets)
        self.assertNotIn("项目名称", targets)
        self.assertNotIn("发布日期", targets)

        plan_item = crawler.spider.build_notice_item(
            notice_type="招标计划",
            data={},
            raw_text="计划正文",
        )
        self.assertEqual(
            pipeline._target_fields(
                "招标计划", plan_item["data"], ItemAdapter(plan_item)
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
