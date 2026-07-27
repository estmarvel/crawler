from __future__ import annotations

import base64
import json

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from crawler_scrapy.sites.sxjm import config
from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline
from crawler_scrapy.sites.sxjm.parser import SxjmParser, decrypt_envelope
from crawler_scrapy.spiders.sxjm import SxjmSpider


def _encrypt(value):
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    encrypted = AES.new(config.AES_KEY, AES.MODE_CBC, config.AES_IV).encrypt(
        pad(raw, AES.block_size)
    )
    return base64.b64encode(encrypted).decode("ascii")


def _detail(announcement_type=1):
    return {
        "id": 44849,
        "announcement_type": announcement_type,
        "title": "华晋焦煤有限责任公司设备采购招标公告",
        "project_name": "华晋焦煤有限责任公司设备采购",
        "content": "<p>项目编号：SJZBHJ06000026H057V18</p><p>招标人：华晋焦煤有限责任公司</p>",
        "publish_time_format": "2026-07-21 18:00:00",
        "created_at_format": "2026-07-21 17:00:00",
        "bid_opening_date_format": "2026-08-12 09:00:00",
        "tender_number": "SJZBHJ06000026H057V18",
        "code": "SJZBHJ06000026H057V18/001",
        "project_type": 10,
        "industry_category": "采矿业",
        "region": "山西省吕梁市",
        "tendering_agency": "山西焦煤集团招标有限公司",
        "document": [
            {
                "id": 53634,
                "original_name": "招标公告.pdf",
                "mime_type": "application/pdf",
                "path": "zcpt/2026-07-21/example.pdf",
            }
        ],
    }


def test_decrypt_envelope_round_trip():
    expected = {"data": [{"id": 44849}], "total": 1}
    assert decrypt_envelope({"errcode": 0, "result": _encrypt(expected)}) == expected


def test_list_params_match_tender_project_frontend():
    assert config.list_params("zbxm", "4", 2, 50) == {
        "page": 2,
        "per_page": 50,
        "announcement_type": "4",
        "project_type": "",
        "category": 3,
    }


def test_parser_maps_tender_notice_and_attachment():
    subtype, notice_type, data, attachments = SxjmParser.parse("zbxm", "zbgg", _detail())
    assert subtype == "zbgg"
    assert notice_type == "招标公告"
    assert data["项目性质"] == "招标项目"
    assert data["源站公告性质"] == "招标（预审）公告"
    assert data["项目名称"]
    assert data["发布网站"] == config.PLATFORM_NAME
    assert attachments[0]["file_name"] == "招标公告.pdf"
    assert attachments[0]["file_url"].endswith("/zcpt/2026-07-21/example.pdf")


def test_parser_preserves_termination_nature():
    subtype, notice_type, data, _ = SxjmParser.parse("zbxm", "zzgg", _detail(4))
    assert subtype == "zbgg"
    assert notice_type == "招标公告"
    assert data["源站公告性质"] == "终止公告"


def test_spider_defaults_and_section_validation():
    spider = SxjmSpider()
    assert spider.channels == ("yfxm", "zbxm", "fzxm", "jycg")
    assert len(spider.feeds) == 17
    request = spider._list_request("zbxm", "zbgg", "1", 1)
    assert "announcement_type=1" in request.url
    assert "category=3" in request.url

    non_tender = SxjmSpider(channels="fzxm")
    assert {feed[2] for feed in non_tender.feeds} == {"4", "5", "6", "7"}


def test_export_basenames_cover_all_homepage_channels():
    routes = SxjmMultiFormatPipeline.ROUTES
    assert len(routes) == 16
    assert routes["zbxm.zbgg"][0] == "招标项目_招标（预审）公告"
    assert routes["yfxm.zbjh"][0] == "依法项目_招标计划"
    assert routes["fzxm.cjhxr"][0] == "非招项目_成交候选人公示"
    assert routes["jycg.cjgg"][0] == "简易采购限额以下_成交公告"


def test_parser_marks_other_channel_nature():
    _, notice_type, data, _ = SxjmParser.parse("fzxm", "cggg", _detail(5))
    assert notice_type == "招标公告"
    assert data["项目性质"] == "非招项目"
    assert data["源站公告性质"] == "采购（预审）公告"
