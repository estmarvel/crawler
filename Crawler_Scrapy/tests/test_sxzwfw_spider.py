from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
from urllib.parse import parse_qs
import unittest
from unittest.mock import patch

from scrapy import Request
from scrapy.http import TextResponse
from scrapy.settings import Settings

from crawler_scrapy.items import NoticeItem
from crawler_scrapy.sites.sxzwfw import config
from crawler_scrapy.sites.sxzwfw.parser import SxzwfwParser
from crawler_scrapy.spiders.sxzwfw import SxzwfwSpider


DOCS = Path(__file__).parents[1] / "crawler_scrapy" / "docs" / "sxzwfw"


class SxzwfwSpiderTest(unittest.TestCase):
    def test_direct_mode_uses_local_ip_with_guard_and_no_system_proxy(self):
        settings = Settings(
            {
                "CRAWLER_OUTBOUND_MODE": "direct",
                "DIRECT_CONCURRENT_REQUESTS": 1,
                "DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN": 1,
                "DIRECT_DOWNLOAD_DELAY": 6.0,
                "DIRECT_RETRY_TIMES": 2,
                "DIRECT_DOWNLOAD_TIMEOUT": 90,
            }
        )
        SxzwfwSpider.update_settings(settings)

        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        self.assertEqual(
            middlewares[
                "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
            ],
            650,
        )
        self.assertIsNone(
            middlewares[
                "crawler_scrapy.transport.proxy_middleware.StaticProxyMiddleware"
            ]
        )
        self.assertFalse(settings.getbool("HTTPPROXY_ENABLED"))
        self.assertEqual(settings.getint("CONCURRENT_REQUESTS"), 1)
        self.assertEqual(settings.getfloat("DOWNLOAD_DELAY"), 6.0)
        self.assertEqual(settings.getint("RETRY_TIMES"), 2)
        self.assertEqual(settings.getint("DOWNLOAD_TIMEOUT"), 90)

    def test_exact_date_form_and_frontend_paging_url(self):
        spider = SxzwfwSpider(
            sections="zbgg_zys",
            start_date="2026-01-16",
            end_date="2026-07-16",
        )
        request = spider._list_request(
            "zbgg_zys", 2, date(2026, 7, 1), date(2026, 7, 16)
        )
        form = parse_qs(request.body.decode("utf-8"), keep_blank_values=True)

        self.assertEqual(request.url, config.build_list_url(2))
        self.assertEqual(form["channelId"], ["12"])
        self.assertEqual(form["beginTime"], ["2026-07-01"])
        self.assertEqual(form["endTime"], ["2026-07-16"])
        self.assertEqual(form["inDates"], [""])
        self.assertEqual(len(spider.query_windows), 7)
        self.assertEqual(spider.query_windows[0], (date(2026, 7, 1), date(2026, 7, 16)))

    def test_unknown_section_and_invalid_window_are_rejected(self):
        with self.assertRaises(ValueError):
            SxzwfwSpider(sections="unknown")
        with self.assertRaises(ValueError):
            SxzwfwSpider(start_date="2026-07-17", end_date="2026-07-16")
        with self.assertRaises(ValueError):
            SxzwfwSpider(days="0")

    def test_saved_list_schedules_details_and_next_page(self):
        spider = SxzwfwSpider(
            sections="zbgg_zys",
            start_date="2026-07-16",
            end_date="2026-07-16",
            max_records=20,
            max_pages=2,
        )
        request = spider._list_request(
            "zbgg_zys", 1, date(2026, 7, 16), date(2026, 7, 16)
        )
        response = TextResponse(
            url=request.url,
            request=request,
            encoding="utf-8",
            body=(DOCS / "山西省公共资源交易平台列表页.html").read_bytes(),
        )

        values = list(
            spider.parse_list(
                response, "zbgg_zys", 1, date(2026, 7, 16), date(2026, 7, 16)
            )
        )
        detail_requests = [value for value in values if "notice_id" in value.cb_kwargs]
        page_requests = [value for value in values if "page" in value.cb_kwargs]
        self.assertEqual(len(detail_requests), 10)
        self.assertEqual(detail_requests[0].cb_kwargs["notice_id"], "1074678")
        list_trace = detail_requests[0].cb_kwargs["list_record"]["_crawler_list_trace"]
        self.assertEqual(list_trace["responseMetadata"]["requestKind"], "list_page")
        self.assertEqual(list_trace["requestForm"]["channelId"], "12")
        self.assertEqual(list_trace["requestForm"]["beginTime"], "2026-07-16")
        self.assertEqual(list_trace["pagination"]["total"], 413981)
        self.assertEqual(list_trace["pagination"]["recordCount"], 10)
        self.assertEqual(
            list_trace["content"]["bodySha256"],
            hashlib.sha256(response.body).hexdigest(),
        )
        self.assertEqual([value.cb_kwargs["page"] for value in page_requests], [2])

    def test_detail_builds_framework_item_with_raw_html_snapshot(self):
        spider = SxzwfwSpider(sections="zbgg_zys", days=1)
        page = (DOCS / "山西省公共资源交易平台.html").read_bytes()
        request = Request("https://prec.sxzwfw.gov.cn/jyxxgczb/1074678.jhtml")
        response = TextResponse(
            url=request.url, request=request, encoding="utf-8", body=page
        )

        values = list(
            spider.parse_detail(
                response,
                "zbgg_zys",
                "1074678",
                {
                    "notice_id": "1074678",
                    "title": "泽州县南村镇农村人居环境整治项目初步设计招标公告",
                    "publish_time": "2026/07/16",
                    "location": "晋城市",
                    "_crawler_list_fingerprint": "list-sha256",
                    "_crawler_list_trace": {
                        "responseMetadata": {"requestKind": "list_page"},
                        "requestForm": {"channelId": "12"},
                    },
                },
            )
        )
        self.assertEqual(len(values), 1)
        item = values[0]
        self.assertIsInstance(item, NoticeItem)
        self.assertEqual(item["notice_type"], "TENDER")
        self.assertEqual(
            item["notice_subtype"], "engineering.zbgg_zys.zbgg"
        )
        self.assertEqual(item["raw_html"], page)
        self.assertEqual(item["detail_url"], request.url)
        self.assertNotIn("详情页链接", item["data"])
        self.assertEqual(item["attachments"], [])
        self.assertEqual(item["raw_data"]["list"]["notice_id"], "1074678")
        self.assertNotIn("_crawler_list_trace", item["raw_data"]["list"])
        self.assertNotIn("_crawler_list_fingerprint", item["raw_data"]["list"])
        self.assertEqual(
            item["raw_data"]["transport"]["list"]["requestForm"]["channelId"],
            "12",
        )
        self.assertEqual(
            item["response_metadata"]["relatedRequests"]["list"]["requestKind"],
            "list_page",
        )
        self.assertEqual(
            item["field_meta"]["_dedup_list_fingerprint"], "list-sha256"
        )
        self.assertEqual(item["field_meta"]["site_parser"], SxzwfwParser.parser_version)
        self.assertEqual(item["field_meta"]["source_section"], "zbgg_zys")
        self.assertEqual(item["field_meta"]["source_channel_id"], "12")
        self.assertEqual(item["field_meta"]["source_notice_type"], "招标/资审公告")
        self.assertEqual(item["field_meta"]["schema_notice_subtype"], "zbgg")

    def test_cms_metadata_url_is_written_back_without_losing_item(self):
        spider = SxzwfwSpider(sections="zbgg_zys")
        item = NoticeItem(
            attachments=[
                {
                    "source_file_id": "123_0",
                    "file_name": "清单.xlsx",
                    "file_url": None,
                    "parse_status": "PENDING",
                }
            ],
            file_urls=[],
            data={},
        )
        request = Request("https://prec.sxzwfw.gov.cn/attachment_url.jspx?cid=123&n=1")
        response = TextResponse(
            url=request.url,
            request=request,
            encoding="utf-8",
            body=b'["&d=abc.xlsx"]',
        )
        result = spider.parse_attachment_metadata(
            response,
            item,
            {"base": "https://prec.sxzwfw.gov.cn", "content_id": "123", "count": 1},
        )
        self.assertEqual(
            result["attachments"][0]["file_url"],
            "https://prec.sxzwfw.gov.cn/attachment.jspx?cid=123&i=0&d=abc.xlsx",
        )
        self.assertEqual(result["attachments"][0]["parse_status"], "URL_RESOLVED")
        self.assertNotIn("附件", result["data"])
        attachment_trace = result["response_metadata"]["relatedRequests"][
            "attachmentMetadata"
        ]
        self.assertEqual(attachment_trace["requestKind"], "attachment_metadata")
        self.assertEqual(attachment_trace["context"]["expectedCount"], 1)
        self.assertEqual(attachment_trace["context"]["resolvedCount"], 1)
        self.assertEqual(
            result["raw_data"]["transport"]["attachmentMetadata"],
            attachment_trace,
        )

    def test_detail_parse_failure_keeps_raw_page_for_diagnosis(self):
        spider = SxzwfwSpider(sections="zbgg_zys", days=1)
        page = (DOCS / "山西省公共资源交易平台.html").read_bytes()
        request = Request("https://prec.sxzwfw.gov.cn/jyxxgczb/1074678.jhtml")
        response = TextResponse(
            url=request.url, request=request, encoding="utf-8", body=page
        )

        with patch(
            "crawler_scrapy.spiders.sxzwfw.SxzwfwParser.parse",
            side_effect=ValueError("unexpected template"),
        ):
            values = list(
                spider.parse_detail(
                    response,
                    "zbgg_zys",
                    "1074678",
                    {
                        "notice_id": "1074678",
                        "title": "解析异常样本招标公告",
                        "publish_time": "2026/07/16",
                    },
                )
            )

        self.assertEqual(len(values), 1)
        item = values[0]
        self.assertEqual(item["parse_status"], "FAILED")
        self.assertEqual(item["raw_html"], page)
        self.assertTrue(item["raw_text"])
        self.assertEqual(
            item["raw_data"]["detail"]["parseError"]["type"], "ValueError"
        )
        self.assertEqual(item["field_meta"]["site_parser"], SxzwfwParser.parser_version)

    def test_cms_metadata_failure_is_recorded_in_trace(self):
        spider = SxzwfwSpider(sections="zbgg_zys")
        item = NoticeItem(
            attachments=[
                {
                    "source_file_id": "123_0",
                    "file_name": "清单.xlsx",
                    "file_url": None,
                    "parse_status": "PENDING",
                }
            ],
            data={"附件": []},
            response_metadata={},
            raw_data={"transport": {}},
        )
        request = Request(
            "https://prec.sxzwfw.gov.cn/attachment_url.jspx?cid=123&n=1",
            callback=spider.parse_attachment_metadata,
            cb_kwargs={
                "item": item,
                "cms": {"content_id": "123", "count": 1},
            },
        )

        class Failure:
            value = TimeoutError("metadata timeout")

            def __init__(self, failed_request):
                self.request = failed_request

            @staticmethod
            def getErrorMessage():
                return "metadata timeout"

        result = spider.on_attachment_metadata_error(Failure(request))
        trace = result["response_metadata"]["relatedRequests"][
            "attachmentMetadata"
        ]
        self.assertEqual(result["attachments"][0]["parse_status"], "METADATA_FAILED")
        self.assertEqual(trace["requestKind"], "attachment_metadata")
        self.assertEqual(trace["error"]["type"], "TimeoutError")
        self.assertEqual(
            result["raw_data"]["transport"]["attachmentMetadata"], trace
        )


if __name__ == "__main__":
    unittest.main()
