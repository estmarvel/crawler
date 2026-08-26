from __future__ import annotations

import json
from types import SimpleNamespace

from itemadapter import ItemAdapter
from scrapy.http import HtmlResponse, Request, TextResponse
from scrapy.settings import Settings

from crawler_scrapy.sites.sxxindian import config
from crawler_scrapy.sites.sxxindian.parser import SxxindianParser
from crawler_scrapy.sites.sxxindian.exporter import SxxindianMultiFormatPipeline
from crawler_scrapy.spiders.sxxindian import SxxindianSpider


def test_all_columns_and_types_are_configured():
    assert len(config.BIDDING_FEEDS) == 19
    assert len(config.PURCHASE_FEEDS) == 6
    assert len(config.DEFAULT_FEEDS) == 25
    assert "bidding.tender.engineering" in config.DEFAULT_FEEDS
    assert "bidding.award.service" in config.DEFAULT_FEEDS
    assert "purchase.contract.all" in config.DEFAULT_FEEDS


def test_feed_filtering():
    spider = SxxindianSpider(
        modules="bidding",
        bidding_categories="change,award",
        project_types="goods,service",
    )
    assert spider.feeds == (
        "bidding.change.goods",
        "bidding.change.service",
        "bidding.award.goods",
        "bidding.award.service",
    )


def test_global_notice_type_quota_aggregates_multiple_feeds():
    spider = SxxindianSpider(max_records=1000, max_records_per_notice_type=200)
    spider._scheduled_type_counts["招标公告"] = 200
    assert spider._type_quota_reached("招标公告") is True
    assert spider._feed_quota_reached("bidding.tender.engineering") is True
    assert spider._feed_quota_reached("purchase.notice.all") is True
    assert spider._feed_quota_reached("bidding.other.engineering") is False


def test_global_notice_type_quota_is_seeded_from_json_after_restart(tmp_path):
    json_dir = tmp_path / "sxxindian" / "json"
    json_dir.mkdir(parents=True)
    rows = [{"公告类型": "TENDER"} for _ in range(2)]
    (json_dir / "tender.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    spider = SxxindianSpider(max_records_per_notice_type=2)
    spider.crawler = SimpleNamespace(
        settings=Settings({"NOTICE_OUTPUT_ROOT": str(tmp_path)})
    )
    spider._load_existing_type_counts()
    assert spider._scheduled_type_counts["招标公告"] == 2
    assert spider._emitted_type_counts["招标公告"] == 2
    assert spider._feed_quota_reached("bidding.tender.engineering") is True


def test_double_encoded_list_payload():
    inner = {
        "RowCount": 1,
        "Table": [{"title": "测试公告", "date": "2026-08-10", "href": "/info/a.html"}],
    }
    body = json.dumps({"custom": json.dumps(inner, ensure_ascii=False)}, ensure_ascii=False).encode()
    response = TextResponse("http://www.sxxindian.com/api", body=body, encoding="utf-8")
    records, total = SxxindianSpider._decode_list(response)
    assert total == 1
    assert records[0]["title"] == "测试公告"


def test_purchase_notice_fields_and_method_are_site_parsed():
    html = """
    <html><body>
      <h1 class="ewb-info-tt">[询比采购]测试设备采购公告</h1>
      <div class="ewb-article">
        <p>采购编号：CG-2026-001</p>
        <p>采购人：甲单位</p><p>采购代理机构：乙公司</p>
        <p>采购范围：设备供货、安装和调试。</p>
        <p>最高限价：100万元</p><p>交货期：30日历天</p>
        <p>采购文件获取时间：2026年8月10日09时至2026年8月15日17时</p>
        <p>响应文件递交截止时间：2026年8月20日09时30分</p>
        <p>联系人：张三</p><p>联系电话：13800000000</p>
      </div>
    </body></html>
    """
    typ, method, data, _, _, text = SxxindianParser.parse(
        "purchase.notice.all",
        html,
        {"title": "测试设备采购公告", "date": "2026-08-10"},
    )
    assert typ == "招标公告"
    assert method == "询比采购"
    assert data["招标方式"] == "询比采购"
    assert data["项目编号/招标编号"] == "CG-2026-001"
    assert data["招标人/采购人名称"] == "甲单位"
    assert data["招标代理机构"] == "乙公司"
    assert "设备供货" in data["招标内容与范围"]
    assert "最高限价" in text


def test_site_number_and_sentence_deadline_are_cleaned():
    text = """（采购编号：MCBX-2026-004-2）
响应文件递交的截止时间为2026年08月17日09时00分；
开启时间：同递交截止时间；"""
    assert SxxindianParser._number(text) == "MCBX-2026-004-2"
    assert SxxindianParser._submission_deadline(text) == "2026-08-17 09:00:00"
    assert SxxindianParser._opening_time(text) == "2026-08-17 09:00:00"


def test_other_column_uses_detail_title_to_choose_schema():
    assert SxxindianParser._notice_type("bidding", "other", "设备采购询比采购公告") == "招标公告"
    assert SxxindianParser._notice_type("bidding", "other", "设备采购成交结果公示") == "中标结果公示"
    assert SxxindianParser._notice_type("bidding", "other", "设备采购流标公告") == "更正结果公示"
    assert SxxindianParser._notice_type(
        "bidding", "other", "变电站综合监控平台", "本项目现发布招标公告，欢迎投标人参加。"
    ) == "招标公告"
    filename, schema = SxxindianMultiFormatPipeline._route_config(
        "__sxxindian__招标信息|其他公告|工程|招标公告"
    )
    assert filename == "山西新点_招标信息_其他公告_工程_招标公告"
    assert schema == "招标公告"


def test_prequalification_uses_its_own_scope_field():
    html = """
    <h1 class="ewb-info-tt">测试项目资格预审公告</h1>
    <div class="ewb-article">项目概况与招标范围：道路施工。\n申请人资格要求：具备施工资质。\n资格预审文件的获取：2026年8月1日至2026年8月5日</div>
    """
    typ, _, data, _, _, _ = SxxindianParser.parse(
        "bidding.prequalification.engineering", html, {"title": "测试项目资格预审公告", "date": "2026-08-01"}
    )
    assert typ == "资格预审公告"
    assert "道路施工" in data["项目概况与招标范围"]


def test_detail_builds_framework_item():
    spider = SxxindianSpider(feeds="bidding.plan.all", start_date="2026-01-01")
    spider._set_crawler(type("Crawler", (), {"settings": {}, "stats": None})()) if False else None
    request = Request("http://www.sxxindian.com/info/x.html")
    response = HtmlResponse(
        request.url,
        request=request,
        body=(
            '<h1 class="ewb-info-tt">测试项目招标计划</h1>'
            '<div class="ewb-article">项目名称：测试项目<br>招标方式：公开招标<br>'
            '项目总投资：500万元<br>建设地点：太原市</div>'
        ).encode("utf-8"),
        encoding="utf-8",
    )
    items = list(
        spider.parse_detail(
            response,
            "bidding.plan.all",
            {"title": "测试项目招标计划", "date": "2026-08-10", "href": "/info/x.html"},
            "fingerprint",
            "招标信息",
            "招标计划",
            "全部",
        )
    )
    assert len(items) == 1
    assert items[0]["platform_code"] == "sxxindian"
    assert items[0]["notice_subtype"] == "sxxindian|招标信息|招标计划|全部"
    assert items[0]["data"]["项目名称"] == "测试项目"


def test_project_nature_and_industry_do_not_use_navigation_labels():
    html = """
    <h1 class="ewb-info-tt">设备采购招标公告</h1>
    <div class="ewb-article">招标项目编号：P-1\n招标内容与范围：采购设备。</div>
    """
    _, _, data, _, _, _ = SxxindianParser.parse(
        "bidding.tender.goods", html,
        {"title": "设备采购招标公告", "date": "2026-08-01"},
    )
    assert data["项目性质"] == ""
    assert data["所属行业"] == ""
    assert data["项目类型/行业分类"] == "货物"


def test_result_horizontal_key_value_table_is_parsed():
    html = """
    <h1 class="ewb-info-tt">学校维修成交结果公示</h1>
    <div class="ewb-article">
      <p>采购编号：MC-2026-01</p>
      <table><tr><td>成交供应商</td><td>山西坚铭建设<br>工程有限公司</td>
      <td>报价(元)</td><td>418323.42</td></tr></table>
    </div>
    """
    typ, _, data, _, _, _ = SxxindianParser.parse(
        "purchase.award.all", html,
        {"title": "学校维修成交结果公示", "date": "2026-08-01"},
    )
    assert typ == "中标结果公示"
    assert data["中标人名称"] == ["山西坚铭建设工程有限公司"]
    assert data["中标价"] == ["418323.42"]
    assert data["项目编号"] == ""
    assert data["招标编号"] == "MC-2026-01"


def test_qualification_stops_before_procurement_file_section():
    html = """
    <h1 class="ewb-info-tt">吊装服务询比采购公告</h1>
    <div class="ewb-article">三、供应商资格要求\n具备独立法人资格。\n
    四、询比采购文件的获取\n获取方法：登录平台下载。\n
    五、响应文件的递交\n递交方式：线上上传。</div>
    """
    _, _, data, _, _, _ = SxxindianParser.parse(
        "purchase.notice.all", html,
        {"title": "吊装服务询比采购公告", "date": "2026-08-01"},
    )
    qualification = data["申请人资格要求/投标人资格要求"]
    assert "独立法人资格" in qualification
    assert "询比采购文件的获取" not in qualification
    assert "登录平台下载" not in qualification


def test_signature_placeholder_is_not_an_agency_name():
    text = """联系方式
    采购人：甲学校
    联系人：张老师
    采购代理机构：________________（盖章）"""
    contacts = SxxindianParser._contacts_xindian(text)
    assert contacts["owner"]["name"] == "甲学校"
    assert contacts["agency"]["name"] == ""


def test_submission_method_drops_attendance_hint():
    html = """
    <h1 class="ewb-info-tt">测试项目招标公告</h1>
    <div class="ewb-article">递交方式：递交截止时间前，在网站（http://www.sxxindian.com）上传电子版投标文件（加密）届时。</div>
    """
    _, _, data, _, _, _ = SxxindianParser.parse(
        "bidding.tender.engineering", html,
        {"title": "测试项目招标公告", "date": "2026-08-01"},
    )
    assert data["递交方法"] == (
        "递交截止时间前，在网站（http://www.sxxindian.com）上传电子版投标文件（加密）"
    )


def test_sxxindian_json_export_uses_compact_trace():
    pipeline = SxxindianMultiFormatPipeline.__new__(SxxindianMultiFormatPipeline)
    pipeline.include_meta = True
    pipeline.include_diagnostics = True
    pipeline.include_trace = True
    adapter = ItemAdapter({
        "notice_type": "招标公告",
        "title": "测试项目招标公告",
        "data": {"项目名称": "测试项目"},
        "raw_text": "项目名称：测试项目",
        "field_meta": {"source": "detail_html"},
        "response_metadata": {"requestKind": "detail_html"},
        "extraction_version": "sxxindian-v3",
        "payload_snapshot_path": "payloads/03_招标公告/id.json",
        "payload_snapshot_sha256": "a" * 64,
    })
    record = pipeline._build_record(adapter, "招标公告")
    exported = pipeline._build_json_record(adapter, "招标公告", record)
    assert exported["项目名称"] == "测试项目"
    assert exported["_trace"]["crawlerVersion"] == "sxxindian-v3"
    assert exported["_trace"]["fieldMeta"]["source"] == "detail_html"
    assert "rawText" not in exported["_trace"]


def test_sxxindian_candidate_announcement_range_is_publicity_time():
    text = """中标候选人公示
公告开始时间:2025年11月07日
公告结束时间:2025年11月10日"""
    assert SxxindianParser._publicity_time(text) == (
        "2025-11-07 至 2025-11-10"
    )


def test_sxxindian_two_column_contacts_do_not_cross_roles():
    text = """十、联系方式
招标人：陵川县居民委员会    招标代理机构：山西根基项目管理有限公司
地址：陵川县崇文镇          地址：晋城市西环路办公楼三楼
联系人：赵先生              联系人：常女士
电话：18635656359            电话：15364663338
招标人或其招标代理机构主要负责人：________________（签名）
招标人或其招标代理机构：________________（盖章）"""
    contacts = SxxindianParser._contacts_xindian(text)
    assert contacts["owner"] == {
        "name": "陵川县居民委员会",
        "address": "陵川县崇文镇",
        "contact": "赵先生",
        "phone": "18635656359",
    }
    assert contacts["agency"] == {
        "name": "山西根基项目管理有限公司",
        "address": "晋城市西环路办公楼三楼",
        "contact": "常女士",
        "phone": "15364663338",
    }
