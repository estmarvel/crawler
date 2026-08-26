import json

from scrapy.http import TextResponse

from crawler_scrapy.sites.eqbidding import config
from crawler_scrapy.sites.eqbidding.exporter import EqbiddingMultiFormatPipeline
from crawler_scrapy.sites.eqbidding.parser import EqbiddingParser
from crawler_scrapy.spiders.eqbidding import EqbiddingSpider


def test_three_frontend_categories_are_enabled():
    assert config.DEFAULT_FEEDS == ("tender", "candidate", "award")
    assert EqbiddingSpider().feeds == config.DEFAULT_FEEDS


def test_urls_and_utf8_form_body_match_frontend():
    spider = EqbiddingSpider(start_date="2026-01-01", end_date="2026-12-31")
    request = spider._list_request("candidate", 1)
    assert request.url.endswith("/web-back/nx/n/list/notice")
    assert b"notice_type=%E5%80%99%E9%80%89%E4%BA%BA%E5%85%AC%E7%A4%BA" in request.body
    assert "type=2" in config.detail_page_url("123", "candidate")


def test_nested_note_and_tender_fields_override_html_gaps():
    note = {"tender": json.dumps({"apply_date_begin": 1784887200000, "apply_date_end": 1785924000000,
            "bid_deadline": 1787018400000, "bid_open_date": 1787018400000,
            "bid_file_obtain_way": "平台获取", "bid_open_place": "电子开标室", "bid_send_form": "在线递交"})}
    payload = {"kid": "1", "notice_title": "测试服务项目招标公告", "project_name": "测试服务项目",
               "notice_release_time": "2026-07-24", "project_item_code": "EQ-001", "region_name": "太原市",
               "org_name": "甲公司", "notice_type": "招标公告", "notice_nature": "正常公告",
               "notice_content": "<p>资金来源：自筹资金</p>", "note": json.dumps(note)}
    typ, data, *_ = EqbiddingParser.parse("tender", payload, {})
    assert typ == "招标公告" and data["项目编号/招标编号"] == "EQ-001"
    assert data["项目地点"] == "太原市" and data["招标人/采购人名称"] == "甲公司"
    assert data["获取方式"] == "平台获取" and data["递交方法"] == "在线递交"
    assert data["开标时间"] == "2026-08-18 10:00:00"


def test_candidate_names_prices_contacts_and_period():
    payload = {"kid": "2", "notice_title": "测试项目成交候选服务商公示", "project_name": "测试项目",
      "notice_release_time": "2026-04-21", "notice_end_time": 1777011120000,
      "project_item_code": "EQ-002", "org_name": "采购人甲", "notice_type": "候选人公示",
      "notice_content": """<p>1 成交候选服务商：甲服务公司 投标报价（万元）：144.9</p>
      <p>2 成交候选服务商：乙服务公司 投标报价（万元）：147</p>
      <p>采购人：采购人甲</p><p>地址：甲路1号</p><p>联系人：张三</p><p>联系电话：13000000000</p>
      <p>采购代理机构：乙代理</p><p>地址：乙路2号</p><p>联系人：李四</p><p>联系电话：0351-1234567</p>"""}
    typ, data, *_ = EqbiddingParser.parse("candidate", payload, {})
    assert typ == "中标候选人公示" and data["招标编号/项目编号"] == "EQ-002"
    assert data["招标人地址"] == "甲路1号" and data["招标代理机构地址"] == "乙路2号"
    assert "2026-04-21" in data["公示时间"]


def test_award_and_correction_schema_selection():
    award = {"notice_title": "设备采购中标公示", "project_name": "设备采购", "notice_type": "中标公示",
             "notice_release_time": "2026-01-02", "notice_content": "<p>中标人：丙公司</p><p>中标价：99万元</p>"}
    typ, data, *_ = EqbiddingParser.parse("award", award, {})
    assert typ == "中标结果公示" and data["中标人名称"]
    change = {"notice_title": "设备采购延期公告", "notice_nature": "延期公告", "notice_type": "招标公告",
              "notice_release_time": "2026-01-02", "notice_content": "<p>开标时间：2026-02-01 09:00</p>"}
    typ, data, *_ = EqbiddingParser.parse("tender", change, {})
    assert typ == "更正结果公示" and data["公共类型"] == "延期公告"


def test_other_content_duplicate_does_not_pollute_party_blocks():
    html = "<p>采购人：甲单位</p><p>地址：甲路</p><p>联系人：张三</p><p>联系电话：13000000000</p><p>采购代理机构：乙代理</p><p>地址：乙路</p><p>联系人：李四</p><p>联系电话：0351-1234567</p>"
    payload = {"notice_title": "测试候选人公示", "notice_type": "候选人公示",
               "notice_content": html, "other_content": "采购人：甲单位地址：甲路联系人：张三采购代理机构：乙代理"}
    _, data, *_ = EqbiddingParser.parse("candidate", payload, {})
    assert data["招标人地址"] == "甲路"
    assert data["招标代理机构"] == "乙代理"
    assert data["招标代理机构地址"] == "乙路"


def test_api_result_and_export_route():
    response = TextResponse("https://www.eqbidding.com/api", body=json.dumps({"code": 200, "result": {"x": 1}}).encode(), encoding="utf8")
    assert EqbiddingSpider._result(response)["x"] == 1
    assert EqbiddingMultiFormatPipeline._route_config("__eqbidding__候选人公示|中标候选人公示") == ("云买卖_候选人公示", "中标候选人公示")
