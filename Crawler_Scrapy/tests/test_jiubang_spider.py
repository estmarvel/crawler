from __future__ import annotations

import json
import unittest

from scrapy import Request
from scrapy.http import TextResponse

from crawler_scrapy.items import NoticeItem
from crawler_scrapy.sites.jiubang import config
from crawler_scrapy.sites.jiubang.parser import JiubangParser
from crawler_scrapy.spiders.jiubang import JiubangSpider


class JiubangSpiderTest(unittest.TestCase):
    def test_site_identity_and_public_api_origin_are_isolated(self):
        spider = JiubangSpider(sections="zbgg_zys")
        self.assertEqual(spider.name, "jiubang")
        self.assertEqual(spider.platform_code, "jiubang")
        self.assertEqual(spider.allowed_domains, ["www.bjjbkj.cn"])
        self.assertEqual(config.API_ORIGIN, "https://www.bjjbkj.cn:9998")
        self.assertNotIn("authentication", spider.api_headers)
        self.assertEqual(spider.api_headers["Origin"], config.WEB_BASE_URL)

    def test_announcement_list_request_matches_jiubang_frontend(self):
        spider = JiubangSpider(sections="zbgg_zys", page_size=5)
        request = spider._list_request("zbgg_zys", 2)
        self.assertEqual(request.url, config.ANNOUNCEMENT_LIST_URL)
        self.assertEqual(
            json.loads(request.body),
            {
                "pageNum": 2,
                "pageSize": 5,
                "annClassifications": ["1"],
                "classifications": ["A", "B", "C"],
                "purDiyCode": "",
            },
        )

    def test_bid_plan_request_uses_dedicated_frontend_list(self):
        spider = JiubangSpider(sections="zbjh", page_size=5)
        request = spider._list_request("zbjh", 3)
        self.assertEqual(request.url, config.BID_PLAN_LIST_URL)
        self.assertEqual(
            json.loads(request.body),
            {"current": 3, "size": 5, "status": 6},
        )

    def test_normal_detail_and_backup_requests_use_jiubang_api(self):
        spider = JiubangSpider(sections="hxr")
        primary = spider._detail_request("hxr", "123", {})
        backup = spider._backup_detail_request("hxr", "123", {})
        self.assertEqual(
            primary.url,
            f"{config.ANNOUNCEMENT_DETAIL_URL}?annId=123",
        )
        self.assertEqual(
            backup.url,
            f"{config.INPUT_ANNOUNCEMENT_DETAIL_URL}?annId=123",
        )

    def test_shared_parser_keeps_jiubang_identity_and_nature(self):
        subtype, notice_type, data, _ = JiubangParser.parse(
            "hxr",
            {
                "annId": "123",
                "annTitle": "某项目中标候选人公示",
                "annClassification": 2,
                "annNature": 4,
                "annContent": "公示内容",
            },
        )
        self.assertEqual(subtype, "hxr")
        self.assertEqual(notice_type, "中标候选人公示")
        self.assertEqual(
            data["源站公告性质"],
            "更正中标候选人公示（annNature=4）",
        )
        self.assertEqual(data["发布网站"], config.PLATFORM_NAME)
        self.assertEqual(
            JiubangParser.detail_url(subtype, {"annId": "123"}),
            "https://www.bjjbkj.cn/#/biddingdetails?annId=123",
        )

    def test_attachment_metadata_uses_jiubang_file_service(self):
        spider = JiubangSpider(sections="gs")
        item = NoticeItem(attachments=[], data={"附件": []}, file_urls=[])
        attachments = [
            {
                "source_file_id": "file-1",
                "file_name": None,
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
                        "url": "//public.cdn.bjjbkj.cn/file-1.pdf",
                        "fileName": "附件.pdf",
                        "fileSize": "2048",
                    },
                }
            ).encode("utf-8"),
        )
        result = spider.parse_attachment_info(response, item, attachments, 0)
        self.assertEqual(
            result["attachments"][0]["file_url"],
            "https://public.cdn.bjjbkj.cn/file-1.pdf",
        )
        self.assertEqual(result["attachments"][0]["file_name"], "附件.pdf")
        self.assertEqual(result["attachments"][0]["file_size_bytes"], 2048)

    def test_built_item_is_written_to_jiubang_output_namespace(self):
        spider = JiubangSpider(sections="gs")
        item = spider._build_item(
            "gs",
            {
                "annId": "123",
                "annTitle": "某项目中标结果公示",
                "annClassification": 3,
                "annNature": 1,
                "annContent": "一、中标人信息\n中标人：某公司",
            },
            "primary",
        )
        self.assertEqual(item["platform_code"], "jiubang")
        self.assertEqual(item["extraction_model"], "jiubang-rule-parser")
        self.assertEqual(
            item["detail_url"],
            "https://www.bjjbkj.cn/#/biddingdetails?annId=123",
        )


if __name__ == "__main__":
    unittest.main()

