from __future__ import annotations

from crawler_scrapy.sites.sxzfcg import config
from crawler_scrapy.sites.sxzfcg.parser import SxzfcgParser, clean_html, parse_list_records
from crawler_scrapy.spiders.sxzfcg import SxzfcgSpider


LIST_JSON = {
    "success": True,
    "result": {
        "data": {
            "total": 0,
            "data": [
                {
                    "title": "测试设备采购项目的公开招标公告",
                    "articleId": "abc==",
                    "pubDate": 1786532559000,
                    "publishDateString": "2026-08-12",
                    "districtName": "山西省本级",
                    "purchaseMethod": "公开招标",
                    "purchaseName": "山西省教育厅",
                    "projectCode": "1499002026AGK02278",
                    "projectName": "测试设备采购项目",
                }
            ],
        }
    },
}


DETAIL_JSON = {
    "success": True,
    "result": {
        "data": {
            "title": "测试设备采购项目的公开招标公告",
            "articleId": "abc==",
            "projectCode": "1499002026AGK02278",
            "projectName": "测试设备采购项目",
            "purchaseName": "山西省教育厅",
            "purchaseMethod": "公开招标",
            "publishDate": 1786532559000,
            "categoryNames": ["采购公告", "公开招标公告"],
            "content": (
                "<div><p>项目概况</p><p>预算金额：100万元</p>"
                "<p>项目编号：1499002026AGK02278</p>"
                "<p>提交投标文件截止时间：2026年09月01日 09:00</p>"
                "<p>地点：山西省太原市</p></div>"
            ),
        }
    },
}


def test_list_records_ignore_broken_total_and_keep_article_id():
    records = parse_list_records(LIST_JSON, "tender")
    assert len(records) == 1
    assert records[0].article_id == "abc=="
    assert records[0].title == "测试设备采购项目的公开招标公告"
    assert records[0].publish_time == "2026-08-12"
    assert config.detail_page_url("abc==").endswith("articleId=abc%3D%3D")


def test_detail_json_maps_to_tender_schema_fields():
    parsed = SxzfcgParser.parse(
        "tender",
        DETAIL_JSON,
        list_record={
            "title": "测试设备采购项目的公开招标公告",
            "publish_time": "2026-08-12",
            "purchaser": "山西省教育厅",
        },
    )
    assert parsed.notice_type == "招标公告"
    assert parsed.title == "测试设备采购项目的公开招标公告"
    assert parsed.data["项目名称"] == "测试设备采购项目"
    assert parsed.data["项目编号"] == "1499002026AGK02278"
    assert parsed.data["招标人/采购人名称"] == "山西省教育厅"
    assert parsed.data["招标金额"] == "100万元"
    assert "项目概况" in parsed.raw_text


def test_clean_html_strips_template_tags():
    assert clean_html("<style>.a{}</style><div>甲&nbsp;<b>乙</b></div>").endswith("甲 乙")


def test_only_requested_categories_are_selected():
    spider = SxzfcgSpider(categories="tender,award", max_records=5)
    assert spider.categories == ("tender", "award")
    assert spider.max_records == 5


def test_list_payload_matches_second_level_category_api():
    payload = config.list_payload("tender", 2, 15)
    assert config.LIST_URL.endswith("/portal/category")
    assert payload["pageNo"] == 2
    assert payload["pageSize"] == 15
    assert payload["categoryCode"] == "ZcyAnnouncement1"
    assert payload["districtCode"] == ["149900"]
    assert payload["isProvince"] is True
