from decimal import Decimal

from crawler_scrapy.schemas.notice_fields import canonicalize_notice_data
from crawler_scrapy.sites.trade365 import config
from crawler_scrapy.sites.trade365.exporter import Trade365MultiFormatPipeline
from crawler_scrapy.sites.trade365.parser import (
    Trade365Parser,
    classify_category,
    parse_list_records,
    parse_page_info,
)
from crawler_scrapy.spiders.trade365 import Trade365Spider


def _detail(title: str, body: str) -> str:
    return f"""<html><body><div class='app'><h2>{title}</h2>
    <div><span>发布时间：2026-08-06 12:30:01</span></div>
    <div class='content' id='content'>{body}</div></div></body></html>"""


def test_list_records_and_page_info_are_parsed():
    html = """<ul class='searchList'><li><a href='/zgczb/24502.jhtml'>
    <p><em>工程</em><span class='span_hover' title='道路施工招标公告'>道路...</span></p>
    <i class='release_date'>发布日期：2026-06-05</i></a></li></ul>
    <div class='pagination_div'>共2143条记录 1/195页</div>"""
    record = parse_list_records(html)[0]
    assert record.notice_id == "24502"
    assert record.title == "道路施工招标公告"
    assert record.publish_time == "2026-06-05"
    assert record.project_type == "工程"
    assert record.detail_url == "http://shanxi.365trade.com.cn/zgczb/24502.jhtml"
    assert parse_page_info(html) == (2143, 1, 195)


def test_page_urls_keep_project_type_filter():
    assert config.list_url("tender.engineering", 1).endswith(
        "/zbgg/index.jhtml?typeId=101"
    )
    assert config.list_url("candidate.service", 2).endswith(
        "/jggs/index_2.jhtml?typeId=103"
    )


def test_mixed_columns_are_classified_by_title():
    assert classify_category("change", "某项目招标终止公告")[0] == "termination"
    assert classify_category("change", "某项目二次招标公告")[0] == "tender"
    assert classify_category("candidate", "某项目中标候选人公示")[0] == "candidate"
    assert classify_category("award", "某项目中标结果公示")[0] == "award"


def test_tender_fields_contacts_and_identifiers_are_extracted():
    parsed = Trade365Parser.parse(
        "tender.service",
        _detail(
            "宣传合作项目（不分标段）招标公告",
            """<p>招标项目编号：I1401000001</p><p>招标编号：ZCFD-2026-01</p>
            <p>1.项目名称：宣传合作项目</p><p>3.服务期：合同签订后一年。</p>
            <p>5.质量标准：满足招标人需求。</p>
            <p>1.递交截止时间：2026年8月31日9时30分</p>
            <p>1.开标时间：2026年8月31日9时30分</p>
            <p>十、联系方式</p><p>招标人：甲公司</p><p>地址：太原市</p>
            <p>联系人：张三</p><p>电话：0351-1234567</p>
            <p>招标代理机构：乙公司</p><p>联系人：李四</p><p>电话：13800000000</p>""",
        ),
    )
    assert parsed.data["项目名称"] == "宣传合作项目"
    assert parsed.data["项目编号"] == "I1401000001"
    assert parsed.data["招标编号"] == "ZCFD-2026-01"
    assert parsed.data["工期/服务期/供货日期"] == "合同签订后一年。"
    assert parsed.data["质量要求"] == "满足招标人需求。"
    assert parsed.data["递交截止时间"] == "2026-08-31 09:30:00"
    assert parsed.data["招标人/采购人名称"] == "甲公司"
    assert parsed.data["招标代理机构"] == "乙公司"


def test_candidate_table_only_uses_price_table():
    parsed = Trade365Parser.parse(
        "candidate.service",
        _detail(
            "宣传项目中标候选人公示",
            """<p>（招标编号：ZCFD-01）</p><p>（招标项目编号：I1401）</p>
            <table><tr><td>排序</td><td>中标候选人名称</td><td>投标总价含税(元)</td></tr>
            <tr><td>1</td><td>甲传媒有限公司</td><td>2820000.00</td></tr>
            <tr><td>2</td><td>乙科技有限公司</td><td>2730000.00</td></tr></table>
            <table><tr><td>序号</td><td>中标候选人名称</td><td>项目负责人姓名</td></tr>
            <tr><td>1</td><td>甲传媒有限公司</td><td>张三</td></tr></table>""",
        ),
    )
    assert parsed.data["中标候选人名称"] == ["甲传媒有限公司", "乙科技有限公司"]
    assert parsed.data["中标候选人报价"] == ["2820000.00元", "2730000.00元"]


def test_candidate_table_keeps_header_unit_and_unit_rate_precision():
    parsed = Trade365Parser.parse(
        "candidate.goods",
        _detail(
            "设备采购中标候选人公示",
            """<table><tr><td>排序</td><td>中标候选人名称</td><td>投标报价（万元）</td></tr>
            <tr><td>1</td><td>甲公司</td><td>341.1527</td></tr></table>""",
        ),
    )
    normalized = canonicalize_notice_data("中标候选人公示", parsed.data)
    assert parsed.data["中标候选人报价"] == ["341.1527万元"]
    assert normalized["中标候选人报价"] == [Decimal("3411527.00")]

    rate = canonicalize_notice_data(
        "中标候选人公示",
        {"中标候选人报价": ["0.536元/个"]},
    )
    assert rate["中标候选人报价"] == ["0.536元/个"]


def test_plain_candidate_template_and_missing_price_table_are_supported():
    plain = Trade365Parser.parse(
        "candidate.service",
        _detail(
            "环卫作业项目（不分标段）中标候选人公示",
            """<p>第一中标候选人：甲物业有限公司</p><p>投标报价：2759328.08元/年</p>
            <p>第二中标候选人：乙物业有限公司</p><p>投标报价：2760000元/年</p>""",
        ),
    )
    assert plain.data["中标候选人名称"] == ["甲物业有限公司", "乙物业有限公司"]
    assert plain.data["中标候选人报价"] == ["2759328.08元/年", "2760000元/年"]

    table = Trade365Parser.parse(
        "candidate.service",
        _detail(
            "运营维护（不分标段）中标候选人公示",
            """<table><tr><td>排序</td><td>中标候选人名称</td><td>服务周期</td></tr>
            <tr><td>1</td><td>甲公司</td><td>三年</td></tr>
            <tr><td>2</td><td>乙公司</td><td>三年</td></tr></table>""",
        ),
    )
    assert table.data["中标候选人名称"] == ["甲公司", "乙公司"]
    assert table.data["中标候选人报价"] == ["", ""]


def test_title_project_name_identifier_fallback_and_control_price():
    parsed = Trade365Parser.parse(
        "tender.goods",
        _detail(
            "汾酒（45度 汇通天下1）泡沫底座（不分标段）招标公告",
            """<p>（项目编号：Q9A-001）</p><p>（招标项目编号：I1401）</p>
            <p>1．项目名称：汾酒（45度<br>汇通天下1）泡沫底座</p>""",
        ),
    )
    assert parsed.data["项目名称"] == "汾酒（45度 汇通天下1）泡沫底座"
    assert parsed.data["项目编号"] == "I1401"
    assert parsed.data["招标编号"] == "Q9A-001"

    control = Trade365Parser.parse(
        "change.engineering",
        _detail(
            "某工程（不分标段）招标控制价",
            "<p>某工程招标控制总价为9738170.12元，措施项目价为10元。</p>",
        ),
    )
    assert control.data["项目名称"] == "某工程"
    assert control.data["招标金额"] == "9738170.12元"


def test_second_tender_name_joint_deadline_funding_and_investment_are_cleaned():
    parsed = Trade365Parser.parse(
        "tender.engineering",
        _detail(
            "某工程（不分标段）二次招标公告",
            """<p>项目资金来源：自筹，招标人为甲公司</p>
            <p>项目规模：建设厂房，工程投资额约1000万元。</p>
            <p>投标文件递交截止时间：2026年02月11日09点30分</p>
            <p>开标时间：2026年02月11日09点30分</p>""",
        ),
    )
    assert parsed.data["项目名称"] == "某工程"
    assert parsed.data["资金来源"] == "自筹"
    assert parsed.data["项目总投资/估算金额"] == "1000万元"
    assert parsed.data["递交截止时间"] == "2026-02-11 09:30:00"
    assert parsed.data["开启时间"] == "2026-02-11 09:30:00"


def test_reversed_source_date_range_is_flagged_without_fabricating_a_correction():
    parsed = Trade365Parser.parse(
        "tender.goods",
        _detail(
            "设备采购招标公告",
            "<p>获取时间：2026年01月20日至2025年01月27日</p>",
        ),
    )
    assert parsed.data["预审文件获取时间"] == "2026年01月20日至2025年01月27日"
    assert parsed.validation_warnings == [
        "SOURCE_DATE_RANGE_REVERSED:预审文件获取时间结束日期早于开始日期"
    ]


def test_trade365_contact_variants_are_mapped_to_correct_party_fields():
    parsed = Trade365Parser.parse(
        "change.goods",
        _detail(
            "设备采购招标变更公告",
            """<p>招标人：甲公司</p><p>电话：吕金鑫</p><p>联系电话：15234403112</p>
            <p>代理机构：乙公司</p><p>项目负责人：张雨</p><p>电话：0351-8787037</p>""",
        ),
    )
    assert parsed.data["招标人联系人"] == "吕金鑫"
    assert parsed.data["招标人联系方式"] == "15234403112"
    assert parsed.data["招标代理机构联系人"] == "张雨"

    government = Trade365Parser.parse(
        "tender.goods",
        _detail(
            "医疗设备招标公告",
            """<p>1. 采购人信息</p><p>名 称：甲医院</p><p>地址：甲地址</p>
            <p>2. 采购代理机构信息</p><p>名 称：乙公司</p><p>地址：乙地址</p>
            <p>3. 项目联系方式</p><p>项目联系人：朱嘉宁 兰亚珍</p>
            <p>联系电话：0351-7770785<br>13753158431</p>""",
        ),
    )
    assert government.data["招标代理机构联系人"] == "朱嘉宁 兰亚珍"
    assert government.data["招标代理机构联系方式"] == "0351-7770785 13753158431"


def test_award_footer_is_not_project_manager_and_consortium_is_split():
    parsed = Trade365Parser.parse(
        "award.engineering",
        _detail(
            "工程总承包中标结果公示",
            """<p>中标人：牵头人：甲建设有限公司、联合体：乙设计有限公司、丙实业有限公司</p>
            <p>中标价格：工程费：96.60% 设计费：49.58%</p>
            <p>招标人或其招标代理机构主要负责人（项目负责人）：（签名）</p>""",
        ),
    )
    assert parsed.data["中标人名称"] == ["甲建设有限公司"]
    assert parsed.data["联合体成员"] == ["乙设计有限公司", "丙实业有限公司"]
    assert parsed.data["项目经理"] == ""


def test_unit_price_is_preserved_in_award_schema():
    normalized = canonicalize_notice_data(
        "中标结果公示",
        {"中标价": ["0.0191元/千瓦时"]},
    )
    assert normalized["中标价"] == ["0.0191元/千瓦时"]


def test_web_page_link_is_not_misclassified_as_attachment():
    parsed = Trade365Parser.parse(
        "tender.service",
        _detail(
            "服务项目招标公告",
            """<a href='http://jyzt.sxzwfw.gov.cn/ztxxzc/index.jhtml%EF%BC%89%E3%80%82'>
            http://jyzt.sxzwfw.gov.cn/ztxxzc/index.jhtml）。</a>""",
        ),
    )
    assert parsed.attachments == []


def test_award_and_public_attachment_are_extracted():
    parsed = Trade365Parser.parse(
        "award.goods",
        _detail(
            "设备采购中标结果公示",
            """<p>设备采购（招标项目编号：I1402）的中标人如下：</p>
            <p>中标人：甲设备有限公司</p><p>中标价格：1200000.00元</p>
            <p><a href='/u/cms/files/result.pdf'>结果附件.pdf</a></p>""",
        ),
    )
    assert parsed.data["中标人名称"] == ["甲设备有限公司"]
    assert parsed.data["中标价"] == ["1200000.00元"]
    assert parsed.attachments[0]["file_name"] == "结果附件.pdf"
    assert parsed.attachments[0]["file_url"].endswith("/u/cms/files/result.pdf")


def test_spider_selects_expected_category_and_project_type_feeds():
    spider = Trade365Spider(
        categories="tender,candidate", project_types="engineering,service"
    )
    assert spider.feeds == (
        "tender.engineering", "tender.service",
        "candidate.engineering", "candidate.service",
    )


def test_exporter_has_every_actual_category_and_project_type_route():
    assert len(Trade365MultiFormatPipeline.ROUTES) == 15
    assert Trade365MultiFormatPipeline.ROUTES["termination.engineering"] == (
        "中招联合山西_终止公告_工程", "招标公告"
    )
