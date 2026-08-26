from __future__ import annotations

import json

from scrapy.http import HtmlResponse, Request, TextResponse

from crawler_scrapy.items import NoticeItem
from crawler_scrapy.sites.lfggzyjy import config
from crawler_scrapy.sites.lfggzyjy.parser import (
    LfggzyjyParser,
    award_name,
    award_price,
    candidate_names,
    candidate_prices,
    classify_notice,
    normalize_list_record,
    parse_list_response,
)
from crawler_scrapy.spiders.lfggzyjy import LfggzyjySpider


def test_list_json_and_detail_url_are_parsed():
    body = json.dumps(
        {
            "success": True,
            "attribute": "45201",
            "obj": [
                {
                    "ID": "aa6f97291cfa4ddb85f1213e8d6c1bda",
                    "TABLE_NAME": "gcjs_notice",
                    "PROJECT_CODE": "GC141000202600327",
                    "PROJECT_NAME": "汾西县文物保护修缮项目招标公告",
                    "REGION_CODE": "141034",
                    "FABUPX_TIME": 1786591506000,
                    "URL": "/gcjs/gcjsNotice/form?id=",
                }
            ],
        },
        ensure_ascii=False,
    )
    rows, total = parse_list_response(body)
    assert total == 45201
    record = normalize_list_record(rows[0])
    assert record.notice_id == "aa6f97291cfa4ddb85f1213e8d6c1bda"
    assert record.table_name == "gcjs_notice"
    assert "getNoticeDetail&url=" in record.detail_url
    assert "getNoticeDetail?url=" not in record.detail_url
    assert "aa6f97291cfa4ddb85f1213e8d6c1bda" in record.detail_url


def test_detail_parser_extracts_core_tender_fields():
    html = """<html><body>
    <div class="title">汾西县文物保护修缮项目招标公告</div>
    <div>发布日期：2026-08-13 浏览量：10</div>
    <div class="content">
    招标编号： GC141000202600327001
    招标项目所在地：山西省-临汾市-汾西县
    一、招标条件
    本招标项目资金来源为政府一般债券与地方财政资金。招标人为汾西县文化和旅游局。
    二、项目概况和招标范围
    2.1项目规模：修缮工程。
    2.4总投资：2255.46万元，本次招标金额为：255.850922万元。
    三、投标人资格要求
    须具备文物保护工程施工资质。
    四、招标文件的获取
    获取时间：2026年8月14日00时00分至2026年8月20日23时59分
    </div></body></html>"""
    record = {
        "ID": "aa6f97291cfa4ddb85f1213e8d6c1bda",
        "TABLE_NAME": "gcjs_notice",
        "PROJECT_CODE": "GC141000202600327",
        "PROJECT_NAME": "汾西县文物保护修缮项目招标公告",
        "FABUPX_TIME": 1786591506000,
        "URL": "/gcjs/gcjsNotice/form?id=",
    }
    parsed = LfggzyjyParser.parse(record, html)
    assert parsed.notice_type == "招标公告"
    assert parsed.notice_subtype == "engineering.gcjs_notice.zbgg"
    assert parsed.publish_time == "2026-08-13"
    assert parsed.data["发布日期"].date().isoformat() == "2026-08-13"
    assert parsed.data["发布网站"] == config.PLATFORM_NAME
    assert parsed.data["项目编号/招标编号"] == "GC141000202600327001"
    assert "汾西县" in parsed.data["项目地点"]
    assert str(parsed.data["项目总投资/估算金额"]) == "22554600.00"
    assert str(parsed.data["招标金额"]) == "2558509.22"
    assert "文物保护工程施工资质" in parsed.data["申请人资格要求/投标人资格要求"]


def test_classification_uses_title_for_change_candidate_and_award():
    assert classify_notice("gcjs_notice", "项目变更公告") == ("更正结果公示", "gzjg")
    assert classify_notice("gcjs_zbhxrgs", "项目中标候选人公示") == (
        "中标候选人公示",
        "hxr",
    )
    assert classify_notice("gcjs_result_notice", "项目中标结果公示") == (
        "中标结果公示",
        "zbjg",
    )


def test_spider_builds_notice_item_from_detail():
    spider = LfggzyjySpider(max_records=1)
    req = Request(
        config.detail_url("gcjs_notice", "abc", "/gcjs/gcjsNotice/form?id=")
    )
    response = HtmlResponse(
        req.url,
        request=req,
        body="""<html><body><div class="title">道路工程招标公告</div>
        <div>发布日期：2026-08-13</div>
        <div>招标编号：GC141000202600001001 项目规模：道路工程。</div>
        </body></html>""".encode(),
        encoding="utf-8",
    )
    items = list(
        spider.parse_detail(
            response,
            {
                "ID": "abc",
                "TABLE_NAME": "gcjs_notice",
                "PROJECT_CODE": "GC141000202600001",
                "PROJECT_NAME": "道路工程招标公告",
                "URL": "/gcjs/gcjsNotice/form?id=",
            },
            "list-sha256",
        )
    )
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, NoticeItem)
    assert item["platform_code"] == "lfggzyjy"
    assert item["notice_type"] == "TENDER"
    assert item["notice_subtype"] == "engineering.gcjs_notice.zbgg"
    assert item["field_meta"]["source_table"] == "gcjs_notice"
    assert item["field_meta"]["_dedup_list_fingerprint"] == "list-sha256"


def test_candidate_publicity_period_is_rule_extracted_from_split_lines():
    html = """<html><body><div class="body_main">
    测试工程中标候选人公示
    公示期：
    202
    6
    年
    8
    月
    22
    日至
    202
    6
    年
    8
    月
    24日
    排序
    中标候选人名称
    投标报价
    1
    山西测试建设有限公司
    100.00万元
    </div></body></html>"""
    parsed = LfggzyjyParser.parse(
        {
            "ID": "abc",
            "TABLE_NAME": "gcjs_zbhxrgs",
            "PROJECT_CODE": "GC141000202600001",
            "PROJECT_NAME": "测试工程中标候选人公示",
            "URL": "/gcjs/gcjsWinNotice/form?id=",
        },
        html,
    )
    assert parsed.data["公示时间"] == "公示期：2026年8月22日至2026年8月24日"


def test_multiline_candidate_table_fields_are_rule_extracted():
    text = """1、
中标候选人基本情况
排序
中标候选人名称
投标报价
（元）
质量要求
工期
1
陕西千载建设有限公司
15299223
.
23
达到合格标准
540日历天
2
（牵头人）江苏中路交通发展有限公司
（成员）山西诚玖路桥工程有限公司
15281300
.
71
达到合格标准
540日历天
3
山东鸿典建设有限公司
15321634
.
04
达到合格标准
540日历天
2、中标候选人按照招标文件要求承诺的项目经理情况
序号
中标候选人名称
项目经理
1
陕西千载建设有限公司
孙吉鹏
二级建造师注册证书
陕
261242407903
"""
    assert candidate_names(text)[:3] == [
        "陕西千载建设有限公司",
        "（牵头人）江苏中路交通发展有限公司；（成员）山西诚玖路桥工程有限公司",
        "山东鸿典建设有限公司",
    ]
    assert candidate_prices(text)[:3] == [
        "15299223.23元",
        "15281300.71元",
        "15321634.04元",
    ]


def test_multiline_award_name_and_price_are_rule_extracted():
    text = """公示期结束后招标人确定
山西五建集团有限公司（牵头人）、中誉恒信工程咨询有限公司（联合体成员）
为该项目
的
中标人，现予以公示。
投标报
价：
191.521702万元
质量标准：合格
"""
    assert award_name(text) == "山西五建集团有限公司（牵头人）、中誉恒信工程咨询有限公司（联合体成员）"
    assert award_price(text) == "191.521702万元"


def test_ai_is_exception_driven_and_keeps_source_trusted_fields():
    assert LfggzyjySpider.ai_extract_fields["招标公告"] == ()
    assert "申请人资格要求/投标人资格要求" in LfggzyjySpider.ai_candidate_fields["招标公告"]
    spider = LfggzyjySpider(max_records=1)
    record = normalize_list_record({
        "ID": "abc",
        "TABLE_NAME": "gcjs_notice",
        "PROJECT_CODE": "GC141000202600001",
        "PROJECT_NAME": "道路工程招标公告",
        "URL": "/gcjs/gcjsNotice/form?id=",
    })
    parsed = LfggzyjyParser.parse(record.raw, "<html><body><div class='body_main'>道路工程招标公告</div></body></html>")
    trusted = spider._trusted_fields(record, parsed)
    assert "项目名称" in trusted
    assert "项目编号" in trusted
    assert "发布日期" in trusted
