from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import parse_qs
import unittest

from scrapy import Request
from scrapy.http import TextResponse

from crawler_scrapy.items import NoticeItem
from crawler_scrapy.sites.sxzwfw import config
from crawler_scrapy.spiders.sxzwfw import SxzwfwSpider


DOCS = Path(__file__).parents[1] / "crawler_scrapy" / "docs" / "sxzwfw"


class SxzwfwSpiderTest(unittest.TestCase):
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
                },
            )
        )
        self.assertEqual(len(values), 1)
        item = values[0]
        self.assertIsInstance(item, NoticeItem)
        self.assertEqual(item["notice_type"], "TENDER")
        self.assertEqual(item["notice_subtype"], "zbgg")
        self.assertEqual(item["raw_html"], page)
        self.assertEqual(item["data"]["详情页链接"], request.url)
        self.assertEqual(item["attachments"], [])

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
            data={"附件": []},
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
        self.assertEqual(result["data"]["附件"], result["attachments"])


if __name__ == "__main__":
    unittest.main()
