from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from scrapy import Request
from scrapy.http import TextResponse

from crawler_scrapy.items import NoticeItem
from crawler_scrapy.pipelines import NoticeFilesPipeline
from crawler_scrapy.sites.huaxin import config
from crawler_scrapy.spiders.huaxin import HuaxinSpider


class HuaxinSpiderRequestTest(unittest.TestCase):
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
        item["data"] = {"附件": []}
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
        self.assertEqual(result["data"]["附件"], result["attachments"])

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
            data={"附件": []},
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
        self.assertEqual(result["data"]["附件"], result["attachments"])


if __name__ == "__main__":
    unittest.main()
