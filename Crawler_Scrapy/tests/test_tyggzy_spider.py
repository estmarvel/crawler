from crawler_scrapy.sites.tyggzy import config
from crawler_scrapy.sites.tyggzy.parser import TyggzyParser
from crawler_scrapy.spiders.tyggzy import TyggzySpider


def test_all_two_modules_and_eight_categories_are_enabled():
    assert len(config.DEFAULT_FEEDS) == 16
    assert "engineering.tender" in config.DEFAULT_FEEDS
    assert "comprehensive.manager_change" in config.DEFAULT_FEEDS


def test_frontend_signatures_match_known_request():
    values = config.list_values("engineering.tender", 1, 2)
    body, sign = config.signed_form(values, ("secondArea", "industriesTypeCode", "hangYe", "title", "projectCode"))
    assert b"typeOne=1" in body and b"typeTwo=1" in body
    assert b"hashSign=e2606debaf9c29a3a03601b3d06cdbe0" in body
    assert sign == "cf8584718643fe6d2c5d8f5fbe851254"


def test_spider_can_filter_module_and_category():
    spider = TyggzySpider(modules="engineering", categories="tender,candidate")
    assert spider.feeds == ("engineering.tender", "engineering.candidate")


def test_tender_detail_uses_source_html_and_extracts_fields():
    payload = {
        "title": "测试工程招标公告", "publicshTime": "2026-06-30",
        "content": """
        <p>（招标项目编号：E140100001）</p><p>招标项目所在地区：山西省太原市</p>
        <p>项目资金来源为财政资金，招标人为甲单位。</p><p>项目规模：建设道路1000米</p>
        <p>获取时间：2026年7月1日至2026年7月5日</p>
        <p>递交截止时间：2026年7月20日09时30分</p><p>开标时间：2026年7月20日09时30分</p>
        <p>招标人：甲单位</p><p>地址：甲方路1号</p><p>联系人：张三</p><p>电话：13000000000</p>
        <p>招标代理机构：乙代理</p><p>地址：乙方路2号</p><p>联系人：李四</p><p>电话：0351-1234567</p>
        """,
        "attachmentCodeList": [{"attachmentCode": "f1", "attachmentFileName": "公告.pdf", "url": "https://x/a.pdf"}],
    }
    parsed, html = TyggzyParser.parse("engineering.tender", payload, {"guid": "g1"})
    assert parsed.notice_type == "招标公告"
    assert parsed.data["项目编号/招标编号"]
    assert parsed.data["招标人地址"] == "甲方路1号"
    assert parsed.data["招标代理机构地址"] == "乙方路2号"
    assert parsed.attachments[0]["file_name"] == "公告.pdf"
    assert 'data-source="apiJyxxDetail"' in html


def test_special_categories_keep_distinct_schema_subtypes():
    base = {"title": "测试项目澄清公告", "publicshTime": "2026-06-30", "content": "<p>内容变更</p>"}
    parsed, _ = TyggzyParser.parse("comprehensive.clarification", base, {"guid": "g2"})
    assert parsed.notice_type == "更正结果公示" and parsed.subtype == "cqxg"
    parsed, _ = TyggzyParser.parse("engineering.control_price", {**base, "title": "测试控制价公示"}, {"guid": "g3"})
    assert parsed.notice_type == "更正结果公示" and parsed.subtype == "kzj"


def test_contract_uses_structured_frontend_detail_data():
    payload = {"data": {
        "projectName": "道路工程", "tenderProjectCode": "E001",
        "constructionUnitName": "甲单位", "biddingAgencyName": "乙代理",
        "winningBidderName": "丙公司", "bidSectionName": "道路工程施工合同",
        "submitTime": 1785542400000,
    }, "attachmentCodeList": []}
    parsed, html = TyggzyParser.parse("engineering.contract", payload, {"guid": "g4"})
    assert parsed.notice_type == "合同与履约"
    assert parsed.data["项目编号"] == "E001"
    assert parsed.data["招标人名称"] == "甲单位"
    assert parsed.data["中标人名称"] == "丙公司"
    assert parsed.data["发布日期"].startswith("2026-")
    assert "constructionUnitName" in html


def test_result_variants_and_control_price_html_attachment():
    award = {"title": "道路项目", "publicshTime": "2026-08-01",
             "content": "<p>中标单位：丙公司</p><p>中标金额：1407.7万元</p>"}
    parsed, _ = TyggzyParser.parse("engineering.award", award, {"guid": "g5"})
    assert parsed.data["中标人名称"] == ["丙公司"]
    assert parsed.data["中标价"] == ["1407.7万元"]
    control = {"title": "项目控制价", "publicshTime": "2026-08-01",
               "bulletincontent": '<p>项目编号：E002</p><p>招标控制价总价：100万元</p><a href="https://x/report.pdf">报告.pdf</a>'}
    parsed, _ = TyggzyParser.parse("comprehensive.control_price", control, {"guid": "g6"})
    assert parsed.data["公共类型"] == "控制价公示"
    assert parsed.data["依据文号"] == "E002"
    assert parsed.attachments[0]["file_name"] == "报告.pdf"


def test_site_specific_field_rules_cover_real_page_variants():
    clarification = {
        "title": "某项目澄清公告",
        "publicshTime": "20260715170029",
        "content": "<p>工程编码：E1401000198012345</p><p>原开标时间：2026年7月20日9:30，现统一变更为：2026年7月23日9:30</p>",
    }
    parsed, _ = TyggzyParser.parse("engineering.clarification", clarification, {"guid": "g7"})
    assert parsed.data["发布日期"] == "2026-07-15 17:00:29"
    assert parsed.data["项目编号"] == "E1401000198012345"
    assert parsed.data["开标时间"] == "2026年7月23日9:30"

    candidate = {
        "title": "某项目中标候选人公示",
        "publicshTime": "2026-08-01",
        "content": """
        <p>公示日期：2026年8月1日至2026年8月4日</p>
        <p>招 标 人：甲单位</p><p>地 址：甲路1号</p><p>联 系 人：张三 联系电话：13000000000</p>
        <p>招标代理机构：乙代理</p><p>地址：乙路2号</p><p>项目负责人：李四</p><p>联系电话：0351-1234567</p>
        """,
    }
    parsed, _ = TyggzyParser.parse("engineering.candidate", candidate, {"guid": "g8"})
    assert parsed.data["公示时间"] == "2026年8月1日至2026年8月4日"
    assert parsed.data["招标人联系人"] == "张三"
    assert parsed.data["招标人联系方式"] == "13000000000"
    assert parsed.data["招标代理机构联系人"] == "李四"


def test_manager_change_reads_nested_publish_time_and_engineering_code():
    payload = {"gcjsGongGao": {
        "title": "项目经理变更公告", "publishTime": 1785542400000,
        "content": "<p>工程编码：E1401000099001</p><p>变更后项目经理：王五</p>",
    }}
    parsed, _ = TyggzyParser.parse("engineering.manager_change", payload, {"guid": "g9"})
    assert parsed.data["发布日期"].startswith("2026-")
    assert parsed.data["项目编号"] == "E1401000099001"


def test_empty_html_anchor_is_not_exported_as_attachment():
    payload = {"title": "公告", "publicshTime": "2026-08-01", "content": '<a href="http://">返回</a>'}
    parsed, _ = TyggzyParser.parse("engineering.tender", payload, {"guid": "g10"})
    assert parsed.attachments == []


def test_hybrid_ai_uses_qwen_and_locks_frontend_api_fields():
    assert TyggzySpider.custom_settings["NOTICE_AI_MODEL"] == "Qwen/Qwen3-8B"
    assert TyggzySpider.ai_metadata_key == "tyggzyHybridAi"
    trusted = TyggzySpider._api_trusted_fields(
        "engineering.tender",
        {"title": "测试项目招标公告"},
        {"title": "测试项目招标公告", "tenderProjectCode": "E001"},
        {"发布网站": "太原公共资源"},
        "2026-08-20",
    )
    assert "项目名称" in trusted
    assert "项目编号" in trusted
    assert "发布日期" in trusted
    assert "招标人地址" not in trusted


def test_contract_structured_api_fields_are_all_protected_from_ai():
    trusted = TyggzySpider._api_trusted_fields(
        "engineering.contract", {"data": {"projectName": "道路工程"}}, {},
        {"项目名称": "道路工程", "合同金额": "100万元", "发布网站": "太原公共资源"},
        "2026-08-20",
    )
    assert {"项目名称", "合同金额", "发布网站"} <= set(trusted)
    assert TyggzySpider.ai_extract_fields["合同与履约"] == ()
