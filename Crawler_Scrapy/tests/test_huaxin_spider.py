from __future__ import annotations

import json
from datetime import datetime, time
from types import SimpleNamespace
import unittest

from scrapy import Request
from scrapy.http import TextResponse

from crawler_scrapy.items import NoticeItem
from crawler_scrapy.pipelines import NoticeFilesPipeline
from crawler_scrapy.sites.huaxin import config
from crawler_scrapy.spiders.huaxin import HuaxinSpider


class HuaxinSpiderRequestTest(unittest.TestCase):
    def test_missing_time_window_enables_full_history_pagination(self):
        spider = HuaxinSpider(
            sections="hxr",
            page_size=2,
            max_records=20,
            max_pages=20,
        )
        self.assertIsNone(spider.window_start)
        self.assertIsNone(spider.window_end)

        request = spider._list_request("hxr", 1)
        response = TextResponse(
            url=request.url,
            request=request,
            encoding="utf-8",
            body=json.dumps(
                {
                    "code": 200,
                    "data": {
                        "records": [
                            {"annId": "old-1", "releaseTime": "2020-01-02"},
                            {"annId": "old-2", "releaseTime": "2020-01-01"},
                        ],
                        "total": 4,
                        "pages": 2,
                    },
                }
            ).encode("utf-8"),
        )

        requests = list(spider.parse_list(response, "hxr", 1))

        self.assertEqual(
            [value.cb_kwargs.get("notice_id") for value in requests[:2]],
            ["old-1", "old-2"],
        )
        list_trace = requests[0].cb_kwargs["list_record"]["_crawler_list_trace"]
        self.assertEqual(list_trace["responseMetadata"]["requestKind"], "list_api")
        self.assertEqual(list_trace["responseMetadata"]["response"]["status"], 200)
        self.assertEqual(list_trace["businessEnvelope"]["total"], 4)
        self.assertEqual(list_trace["requestPayload"]["pageNum"], 1)
        self.assertEqual(requests[-1].cb_kwargs.get("page"), 2)

    def test_explicit_history_window_is_parsed_and_validated(self):
        spider = HuaxinSpider(
            sections="hxr",
            start_date="2026-01-01",
            end_date="2026-06-30",
        )
        self.assertEqual(spider.window_start, datetime(2026, 1, 1))
        self.assertEqual(
            spider.window_end,
            datetime.combine(datetime(2026, 6, 30).date(), time.max),
        )
        with self.assertRaises(ValueError):
            HuaxinSpider(sections="hxr", days="0")
        with self.assertRaises(ValueError):
            HuaxinSpider(
                sections="hxr",
                start_date="2026-07-01",
                end_date="2026-06-30",
            )

    def test_history_page_filters_records_and_stops_at_time_boundary(self):
        spider = HuaxinSpider(
            sections="hxr",
            page_size=3,
            max_records=20,
            max_pages=20,
            start_date="2026-01-01",
            end_date="2026-06-30",
        )
        request = spider._list_request("hxr", 1)
        response = TextResponse(
            url=request.url,
            request=request,
            encoding="utf-8",
            body=json.dumps(
                {
                    "code": 200,
                    "data": {
                        "records": [
                            {
                                "annId": "future",
                                "releaseTime": "2026-07-01 00:00:00",
                            },
                            {
                                "annId": "inside",
                                "releaseTime": "2026-06-15 12:00:00",
                            },
                            {
                                "annId": "old",
                                "releaseTime": "2025-12-31 23:59:59",
                            },
                        ],
                        "total": 100,
                        "pages": 34,
                    },
                }
            ).encode("utf-8"),
        )

        requests = list(spider.parse_list(response, "hxr", 1))

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].cb_kwargs["notice_id"], "inside")
        self.assertEqual(spider._scanned_counts["hxr"], 3)

    def test_unsorted_old_record_does_not_stop_history_pagination(self):
        spider = HuaxinSpider(
            sections="hxr",
            page_size=3,
            max_records=20,
            max_pages=20,
            start_date="2026-01-01",
        )
        request = spider._list_request("hxr", 1)
        response = TextResponse(
            url=request.url,
            request=request,
            encoding="utf-8",
            body=json.dumps(
                {
                    "code": 200,
                    "data": {
                        "records": [
                            {
                                "annId": "pinned-old",
                                "releaseTime": "2025-12-01 00:00:00",
                            },
                            {
                                "annId": "new-1",
                                "releaseTime": "2026-06-15 00:00:00",
                            },
                            {
                                "annId": "new-2",
                                "releaseTime": "2026-05-15 00:00:00",
                            },
                        ],
                        "total": 6,
                        "pages": 2,
                    },
                }
            ).encode("utf-8"),
        )

        requests = list(spider.parse_list(response, "hxr", 1))

        detail_ids = [
            value.cb_kwargs["notice_id"]
            for value in requests
            if "notice_id" in value.cb_kwargs
        ]
        next_pages = [
            value.cb_kwargs["page"]
            for value in requests
            if "page" in value.cb_kwargs
        ]
        self.assertEqual(detail_ids, ["new-1", "new-2"])
        self.assertEqual(next_pages, [2])

    def test_short_category_stops_after_source_is_exhausted(self):
        spider = HuaxinSpider(
            sections="gs",
            page_size=5,
            max_records=20,
            max_pages=20,
            start_date="2025-01-01",
        )
        request = spider._list_request("gs", 1)
        response = TextResponse(
            url=request.url,
            request=request,
            encoding="utf-8",
            body=json.dumps(
                {
                    "code": 200,
                    "data": {
                        "records": [
                            {"annId": "result-1", "releaseTime": "2026-06-01"},
                            {"annId": "result-2", "releaseTime": "2026-05-01"},
                        ],
                        "total": 2,
                        "pages": 1,
                    },
                }
            ).encode("utf-8"),
        )

        requests = list(spider.parse_list(response, "gs", 1))

        self.assertEqual(
            [value.cb_kwargs.get("notice_id") for value in requests],
            ["result-1", "result-2"],
        )
        self.assertFalse(any("page" in value.cb_kwargs for value in requests))

    def test_bid_plan_history_uses_frontend_release_time(self):
        published = HuaxinSpider._record_publish_time(
            "zbjh",
            {
                "createTime": "2025-01-01 00:00:00",
                "releaseTime": "2026-06-01 12:00:00",
            },
        )
        self.assertEqual(published, datetime(2026, 6, 1, 12, 0, 0))

    def test_huaxin_disables_snapshots_but_keeps_attachment_downloads(self):
        self.assertIs(HuaxinSpider.custom_settings["NOTICE_SNAPSHOT_ENABLED"], False)
        self.assertIs(
            HuaxinSpider.custom_settings["NOTICE_ATTACHMENT_DOWNLOAD_ENABLED"],
            True,
        )

    def test_bid_plan_list_uses_frontend_endpoint_and_payload(self):
        spider = HuaxinSpider(sections="zbjh", page_size=20)
        request = spider._list_request("zbjh", 3)
        self.assertEqual(request.url, config.BID_PLAN_LIST_URL)
        self.assertEqual(
            json.loads(request.body),
            {"current": 3, "size": 20, "status": 6},
        )
        self.assertEqual(
            spider._list_record_id(
                "zbjh",
                {"id": 14, "planId": "p2047138042393747456"},
            ),
            "14",
        )

    def test_attachment_metadata_url_is_written_back_to_item(self):
        spider = HuaxinSpider(sections="zbjh")
        item = NoticeItem()
        item["attachments"] = []
        item["data"] = {}
        item["file_urls"] = []
        attachments = [
            {
                "source_file_id": "file-1",
                "file_name": "核准.pdf",
                "file_url": None,
                "file_size_bytes": None,
                "parse_status": "PENDING",
            }
        ]
        request = Request(f"{config.BIDDING_FILE_QUERY_URL}/file-1")
        response = TextResponse(
            url=request.url,
            request=request,
            encoding="utf-8",
            body=json.dumps(
                {
                    "code": 200,
                    "data": {
                        "url": "//files.example.test/preview/file-1",
                        "downloadUrl": "//files.example.test/download/file-1",
                        "fileSize": "1024",
                    },
                }
            ).encode("utf-8"),
        )
        result = spider.parse_attachment_info(response, item, attachments, 0)
        self.assertEqual(
            result["attachments"][0]["file_url"],
            "https://files.example.test/preview/file-1",
        )
        self.assertEqual(result["attachments"][0]["file_size_bytes"], 1024)
        self.assertEqual(result["attachments"][0]["parse_status"], "URL_RESOLVED")
        self.assertEqual(
            result["file_urls"],
            ["https://files.example.test/preview/file-1"],
        )
        self.assertIsNone(result["attachments"][0].get("file_hash"))
        self.assertNotIn("附件", result["data"])

    def test_bid_plan_uses_plan_title_as_notice_title(self):
        spider = HuaxinSpider(sections="zbjh")
        item = spider._build_item(
            "zbjh",
            {
                "id": 14,
                "_route_planid": "14",
                "projectName": "项目名称",
                "planTitle": "项目名称招标计划",
            },
            "primary",
        )
        self.assertEqual(item["title"], "项目名称招标计划")

    def test_attachment_download_keeps_schema_fields_without_ocr(self):
        class Stats:
            def __init__(self):
                self.values = {}

            def inc_value(self, key):
                self.values[key] = self.values.get(key, 0) + 1

        pipeline = NoticeFilesPipeline.__new__(NoticeFilesPipeline)
        pipeline.enabled = True
        pipeline.files_urls_field = "file_urls"
        pipeline.files_result_field = "files"
        pipeline.crawler = SimpleNamespace(stats=Stats())

        item = NoticeItem(
            platform_code="huaxin",
            notice_type="招标计划",
            notice_id="14",
            detail_url="https://www.ygcgpt.com/#/biddingplan?planid=14",
            data={},
            attachments=[
                {
                    "source_file_id": "file-1",
                    "file_name": "核准.pdf",
                    "file_url": "https://files.example.test/file-1",
                    "storage_path": None,
                    "file_hash": None,
                    "file_size_bytes": None,
                    "file_type": "application/pdf",
                    "parse_status": "URL_RESOLVED",
                }
            ],
            file_urls=["https://files.example.test/file-1"],
        )
        request = Request(
            "https://files.example.test/file-1",
            meta={"_notice_attachment_index": 0},
        )
        self.assertEqual(
            pipeline.file_path(request, item=item),
            "huaxin/attachments/招标计划/14/file-1_核准.pdf",
        )

        media_request = pipeline.get_media_requests(item, None)[0]
        self.assertTrue(media_request.meta["allow_offsite"])
        self.assertEqual(media_request.meta["_notice_attachment_index"], 0)

        result = pipeline.item_completed(
            [
                (
                    True,
                    {
                        "url": request.url,
                        "path": "huaxin/attachments/招标计划/14/file-1_核准.pdf",
                        "checksum": "a" * 32,
                        "status": "downloaded",
                        "file_size_bytes": 1024,
                        "file_type": "application/pdf",
                    },
                )
            ],
            item,
            None,
        )
        attachment = result["attachments"][0]
        self.assertEqual(attachment["storage_path"], result["files"][0]["path"])
        self.assertEqual(attachment["file_hash"], "a" * 32)
        self.assertEqual(attachment["file_size_bytes"], 1024)
        self.assertEqual(attachment["parse_status"], "DOWNLOADED_NO_OCR")
        self.assertNotIn("附件", result["data"])


if __name__ == "__main__":
    unittest.main()
