import json

from scrapy.http import TextResponse

from crawler_scrapy.sites.wtjypt import config
from crawler_scrapy.sites.wtjypt.exporter import WtjyptMultiFormatPipeline
from crawler_scrapy.sites.wtjypt.parser import WtjyptParser
from crawler_scrapy.spiders.wtjypt import WtjyptSpider


def test_all_four_bidding_categories_use_the_frontend_all_filter():
    assert config.DEFAULT_FEEDS == (
        "bidding.plan.all",
        "bidding.tender.all",
        "bidding.candidate.all",
        "bidding.award.all",
    )


def test_public_endpoints_payloads_and_detail_urls_match_frontend():
    payload = config.list_payload("bidding.candidate.goods")
    assert payload["tenderNature"] == "1" and payload["infoType"] == "1"
    assert config.list_endpoint("bidding", "candidate").endswith("/findZBInfo")
    assert config.list_endpoint("purchase", "notice").endswith("/findCGInfo")
    assert config.list_endpoint("bidding", "plan").endswith("/findZBPlanInfo")
    assert "ggarticle.html?notid=abc&type=2" in config.detail_page_url("bidding", "award", "abc", "2")
    assert config.PROJECT_TYPES["engineering"][2] == "A"
    assert config.PROJECT_TYPES["goods"][2] == "B"
    assert config.PROJECT_TYPES["service"][2] == "C"


def test_spider_defaults_to_bidding_categories_only():
    assert WtjyptSpider().feeds == config.DEFAULT_FEEDS
    assert WtjyptSpider(modules="bidding").feeds == config.DEFAULT_FEEDS


def test_list_payload_decoding_and_timestamp():
    body = json.dumps({"zbInfo": [{"noticeId": "x", "noticeName": "测试", "publishTime": 1786527414000}]})
    response = TextResponse("http://www.wtjypt.com/list", body=body.encode(), encoding="utf-8")
    rows = WtjyptSpider._records("bidding.tender.engineering", json.loads(response.text))
    assert rows[0]["noticeId"] == "x"
    assert WtjyptSpider._publish_time(rows[0]).year == 2026


def test_tender_api_fields_override_body_and_contacts_are_kept_separate():
    payload = {
        "noticeId": "x", "noticeName": "测试服务项目招标公告", "publishTime": 1786527414000,
        "tenderProjectCode": "WT-001", "bidOpenTime": "2026年08月21日 09:30",
        "tendereeName": "甲单位", "tenderAgencyName": "乙代理", "tenderModeName": "公开后审",
        "dicIndustriesType": "商务服务业", "classificationName": "服务类",
        "noticeContent": """
          <p>项目地点：山西省太原市</p><p>招标人：甲单位</p><p>地址：甲单位路1号</p>
          <p>联系人：张三</p><p>电话：13000000000</p><p>招标代理机构：乙代理</p>
          <p>地址：乙代理路2号</p><p>联系人：李四</p><p>电话：0351-1234567</p>
        """, "enclosureList": [],
    }
    typ, _, data, _, _, _, _, _ = WtjyptParser.parse("bidding.tender.service", payload, {})
    assert typ == "招标公告"
    assert data["项目编号/招标编号"] == "WT-001"
    assert data["开标时间"] == "2026年08月21日 09:30"
    assert data["项目地点"] == "山西省太原市"
    assert data["招标人地址"] == "甲单位路1号"
    assert data["招标代理机构地址"] == "乙代理路2号"


def test_candidate_text_extracts_names_prices_and_public_api_metadata():
    payload = {
        "noticeName": "测试项目中标候选人公示", "publishTime": 1786527414000,
        "tenderProjectCode": "WT-002", "bidOpenTime": "2026年08月12日",
        "tendereeName": "招标人", "tenderAgencyName": "代理", "tenderModeName": "公开后审",
        "dicIndustriesType": "服务业", "classificationName": "服务类",
        "noticeContent": """
          <p>公示开始时间：2026-08-12 18:00 公示结束时间：2026-08-15 18:00</p>
          <p>第1名：甲公司，投标报价（元）：7800259.1；</p>
          <p>第2名：乙公司，投标报价（元）：7892220.97；</p>
        """, "enclosureList": [],
    }
    typ, _, data, *_ = WtjyptParser.parse("bidding.candidate.service", payload, {})
    assert typ == "中标候选人公示"
    assert data["招标编号/项目编号"] == "WT-002"
    assert data["中标候选人名称"][:2] == ["甲公司", "乙公司"]
    assert data["中标候选人报价"][:2] == ["7800259.1", "7892220.97"]


def test_change_delay_and_termination_inside_notice_feed_use_correction_schema():
    for title, expected in (
        ("测试项目招标延期公告", "延期公告"),
        ("测试项目采购终止公告", "终止公告"),
    ):
        payload = {
            "noticeName": title, "publishTime": 1786527414000,
            "tenderProjectCode": "WT-C01", "bidOpenTime": "2026年08月20日 09:30",
            "tendereeName": "甲单位", "tenderAgencyName": "乙代理",
            "dicIndustriesType": "建筑业", "noticeContent": "<p>联系方式</p>", "enclosureList": [],
        }
        module = "purchase" if "采购" in title else "bidding"
        category = "notice" if module == "purchase" else "tender"
        typ, _, data, *_ = WtjyptParser.parse(f"{module}.{category}.engineering", payload, {})
        assert typ == "更正结果公示"
        assert data["公共类型"] == expected
        assert data["依据文号"] == "WT-C01"


def test_plan_and_attachment_mapping():
    plan = {"planName": "测试项目招标计划", "projectName": "测试项目", "publishDate": 1783675905000,
            "legalPerson": "甲单位", "region": "山西省", "noticeTime": "2026-08",
            "contributionScale": "28000万元", "projectOverview": "建设锅炉", "fjEnclosure": []}
    typ, _, data, attachments, raw_html, raw_text, *_ = WtjyptParser.parse("bidding.plan.all", plan, {})
    assert typ == "招标计划" and data["项目总投资"] == "28000万元"
    assert data["招标人名称"] == "甲单位" and not attachments
    assert 'data-source="findPlanDetail"' in raw_html
    assert "测试项目" in raw_html and "28000万元" in raw_text
    mapped = WtjyptParser._attachments({"enclosureList": [{"enclosureName": "公告.pdf", "enclosurePath": "/files/a.pdf"}]})
    assert mapped[0]["file_url"] == "http://www.wtjypt.com/files/a.pdf"


def test_export_routes_stay_separate_by_module_category_and_project_type():
    name, schema = WtjyptMultiFormatPipeline._route_config(
        "__wtjypt__采购项目|成交公示|服务|中标候选人公示"
    )
    assert name == "伟拓_采购项目_成交公示_服务"
    assert schema == "中标候选人公示"
    changed, schema = WtjyptMultiFormatPipeline._route_config(
        "__wtjypt__招标项目|招标公告|工程|更正结果公示"
    )
    assert changed == "伟拓_招标项目_招标公告_工程_更正结果公示"


def test_wtjypt_specific_deadline_precise_opening_and_party_variants():
    payload = {
        "noticeName": "测试项目招标公告", "publishTime": 1786527414000,
        "tenderProjectCode": "WT-003", "bidOpenTime": "2026-09-01 00:00:00",
        "tendereeName": "甲单位", "tenderAgencyName": "乙代理",
        "noticeContent": """
        <p>4.1 投标文件递交的截止时间：2026年9月1日上午9:30（北京时间）；</p>
        <p>5.1 开标时间：2026年9月1日上午9:30（北京时间）；</p>
        <p>6、投标保证金的递交 本项目不收取投标保证金。</p>
        <p>招标单位：甲单位</p><p>地址：甲方路1号</p><p>联系人：张三</p><p>联系方式：13000000000</p>
        <p>招标代理：乙代理</p><p>地址：乙方路2号</p><p>联系人：李四</p><p>电话：0351-1234567</p>
        """, "enclosureList": [],
    }
    _, _, data, *_ = WtjyptParser.parse("bidding.tender.all", payload, {})
    assert "9:30" in data["递交截止时间"]
    assert "9:30" in data["开标时间"]
    assert data["招标人地址"] == "甲方路1号"
    assert data["招标代理机构地址"] == "乙方路2号"
    assert "不收取投标保证金" in data["投标保证金方式"]


def test_wtjypt_award_accepts_winner_name_variants():
    payload = {
        "noticeName": "设备维修中标结果公示", "publishTime": 1786527414000,
        "tenderProjectCode": "WT-004", "tendereeName": "甲单位", "tenderAgencyName": "乙代理",
        "noticeContent": "<p>中标单位名称：丙公司</p><p>中标价格：3937288.00元</p>",
        "enclosureList": [],
    }
    _, _, data, *_ = WtjyptParser.parse("bidding.award.all", payload, {})
    assert data["中标人名称"] == ["丙公司"]
    assert data["中标价"] == ["3937288.00元"]


def test_plan_combines_overview_and_scale_without_losing_either():
    plan = {
        "planName": "测试招标计划", "projectName": "测试项目",
        "projectOverview": "建设储能站", "projectScale": "规模为200MW",
    }
    _, _, data, *_ = WtjyptParser.parse("bidding.plan.all", plan, {})
    assert data["建设内容及规模"] == "建设储能站\n规模为200MW"


def test_pdf_text_is_merged_for_rules_and_unified_ai_pipeline():
    payload = {
        "noticeName": "测试项目招标公告", "noticeContent": "<p>项目名称：测试项目</p>",
        "enclosureList": [],
    }
    _, _, data, _, raw_html, raw_text, *_ = WtjyptParser.parse(
        "bidding.tender.all", payload, {},
        pdf_text="投标人资格要求：具备建筑工程施工总承包一级资质",
    )
    assert "附件PDF正文" in raw_text
    assert "建筑工程施工总承包一级资质" in raw_text
    assert "附件PDF正文" not in raw_html
    # 混合 AI 不再通过旧 BaseNoticeSpider 补空接口选择字段；金额和时间只在
    # 正文有明确标签且规则缺失/异常时由 Hybrid Pipeline 动态升级。
    spider = WtjyptSpider()
    missing = ["招标金额", "开标时间"]
    assert spider.select_ai_extract_fields("招标公告", missing, data) == []


def test_pdf_parsing_can_be_disabled_like_qianji():
    assert WtjyptSpider(parse_pdf=True).parse_pdf is True
    assert WtjyptSpider(parse_pdf="false").parse_pdf is False


def test_hybrid_ai_uses_qwen_and_locks_detail_api_fields():
    assert WtjyptSpider.custom_settings["NOTICE_AI_MODEL"] == "Qwen/Qwen3-8B"
    assert WtjyptSpider.ai_metadata_key == "wtjyptHybridAi"
    trusted = WtjyptSpider._api_trusted_fields(
        "bidding.tender.all",
        {
            "tenderProjectCode": "E001", "dicIndustriesType": "建筑业",
            "bidOpenTime": "2026-08-30 09:30", "tendereeName": "甲单位",
        },
        {"发布网站": "伟拓"},
        "2026-08-20",
    )
    assert "项目编号/招标编号" in trusted
    assert "开标时间" in trusted
    assert "招标人/采购人名称" in trusted
    assert "项目规模" not in trusted


def test_wtjypt_ai_only_regularly_reviews_unstable_boundaries():
    assert "项目规模" in WtjyptSpider.ai_extract_fields["招标公告"]
    assert "项目名称" not in WtjyptSpider.ai_extract_fields["招标公告"]
    assert WtjyptSpider.ai_extract_fields["中标结果公示"] == ()
