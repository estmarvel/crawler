from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

from scrapy.settings import Settings

from crawler_scrapy.pipelines import HtmlSnapshotPipeline, NoticeSchemaPipeline
from crawler_scrapy.sites.bitbid import config
from crawler_scrapy.sites.bitbid.exporter import BitbidMultiFormatPipeline
from crawler_scrapy.sites.bitbid.parser import BitbidParser
from crawler_scrapy.schemas.notice_fields import (
    ANNOUNCEMENT_SCHEMAS,
    PARSER_DIAGNOSTIC_FIELDS,
    canonicalize_notice_data,
)
from crawler_scrapy.spiders.bitbid import BitbidSpider


def test_category_and_url_mapping():
    assert config.CATEGORIES["plan"]["gg_type"] == 4
    assert "ggType=1" in config.list_url("tender", 1, 10)
    assert "timeType=1" in config.list_url("tender", 1, 10)
    assert config.detail_api_url("candidate", 73971).endswith("/hxrInfo/73971")
    assert "dbZhongBiaoJieGuoGongGao.id=222457" in config.pdf_url("award", 222457)
    assert config.source_notice_id("tender", 1915) == "tender:1915"
    assert config.source_notice_id("candidate", 1915) == "candidate:1915"


def test_plan_uses_bitbid_display_mapping():
    payload = {"zbjhInfo": {
        "id": 308, "name": "岚县晋通风力发电项目", "capital": "公开招标",
        "planProjectOverview": "能源", "investEstimation": "53123.26(万元)",
        "planProjectBidSco": "施工;设计;监理", "legalPerson": "恒能新能源公司",
        "approvalNumber": "岚县能源局", "provinceName": "吕梁市岚县",
        "scale": "建设100MW风电场", "remark": "2026年08月",
        "fabuTime": "2026-07-07 16:48:13",
    }}
    notice_type, data, _, _, _ = BitbidParser.parse("plan", payload)
    assert notice_type == "招标计划"
    assert data["招标方式"] == "公开招标"
    assert data["行政监督部门"] == "岚县能源局"
    assert data["项目总投资"] == "53123.26(万元)"
    assert data["项目性质"] == ""


def test_plan_rejects_reused_api_keys_with_wrong_semantics():
    payload = {"zbjhInfo": {
        "id": 31,
        "name": "老版本计划项目",
        "nameApproval": "关于老版本计划项目可行性研究报告的批复",
        "capital": "区财政筹措",
        "planProjectOverview": "<p>主要建设教学楼和运动场。</p>",
        "approvalNumber": "文发改审批发〔2020〕18号",
        "provinceName": "山西省",
        "cityName": "太原市",
        "planProjectBidSco": "<p>施工及监理。</p>",
        "scale": "<p>总建筑面积10000平方米。</p>",
        "planFabuTime": "2020-07-01 10:00:00",
        "codeByAuth": "2020-140100-47-01-000001",
    }}
    notice_type, data, _, _, _ = BitbidParser.parse("plan", payload)
    assert notice_type == "招标计划"
    assert data["项目性质"] == ""
    assert data["招标方式"] == ""
    assert data["项目类型"] == ""
    assert data["行政监督部门"] == ""
    assert data["建设地点"] == "山西省太原市"
    assert data["招标内容"] == "施工及监理。"
    assert data["建设内容及规模"] == "总建筑面积10000平方米。"
    assert data["招标公告（资格预审公告）预计发布时间"] == ""
    assert data["发布日期"] == ""
    assert data["项目编号"] == "2020-140100-47-01-000001"


def test_tender_parses_html_and_direct_api_fields():
    payload = {
        "xmInfo": {"faBaoMingCheng": "开标测试校验值", "shengName": "山西省", "shiName": "晋中市"},
        "ggInfo": {
            "id": 10689965, "gongGaoBianHao": "SXHRZB-2026060",
            "gongGaoMingCheng": "场馆运营服务项目招标公告",
            "gongGaoFaBuTime": "2026-07-30 11:58:52",
            "zhaoBiaoRenName": "左权文旅公司", "submitDocEndTime": "2026-08-20 09:00:00",
            "gongGaoNeiRong": "<p>项目规模：场馆运营。</p><p>服务期限：10年</p><p>服务地点：晋中市左权县</p><p>招标内容与范围：运营服务。</p><p>投标人资格要求：具有营业执照。</p>",
        },
    }
    notice_type, data, attachments, _, text = BitbidParser.parse("tender", payload)
    assert notice_type == "招标公告"
    assert data["项目名称"] == "场馆运营服务项目"
    assert data["项目规模"] == "场馆运营。"
    assert data["工期/服务期/供货日期"] == "10年"
    assert data["项目编号/招标编号"] == "SXHRZB-2026060"
    assert data["项目编号"] == ""
    assert data["招标编号"] == "SXHRZB-2026060"
    assert attachments[0]["file_url"].startswith("http://www.bitbid.cn/auth/")
    assert attachments[0]["file_url"].endswith("zbGongGao.id=10689965")
    assert "场馆运营" in text


def test_tender_rejects_numeric_region_codes_and_stops_scope_at_peer_fields():
    payload = {
        "xmInfo": {},
        "ggInfo": {
            "id": 10690100,
            "gongGaoMingCheng": "广告投放项目招标公告",
            "xiangmushudiSheng": "",
            "xiangmushudiShi": 141100,
            "gongGaoNeiRong": """
            <p>二、项目概况和招标范围</p>
            <p>2.1招标内容与范围：本项目划分为1个标段：</p>
            <p>001不分标段，投放公交车体及站台广告。</p>
            <p>2.2投放周期：3个月。</p>
            <p>2.3服务标准：满足招标人要求。</p>
            <p>2.4资格审查方式：资格后审。</p>
            """,
        },
    }

    _, data, _, _, _ = BitbidParser.parse("tender", payload)

    assert data["项目地点"] == ""
    assert "投放公交车体及站台广告" in data["招标内容与范围"]
    assert "投放周期" not in data["招标内容与范围"]
    assert "服务标准" not in data["招标内容与范围"]
    assert "资格审查方式" not in data["招标内容与范围"]


def test_tender_scope_does_not_treat_three_digit_lot_as_chapter_heading():
    notice_type, data, *_ = BitbidParser.parse(
        "tender",
        {
            "ggInfo": {
                "gongGaoMingCheng": "设备采购招标公告",
                "gongGaoNeiRong": (
                    "<p>2.1招标范围：本项目划分为四个标段：</p>"
                    "<p>004 第四标段：采购测量装置30台。</p>"
                    "<p>2.2交货期：合同签订后60天。</p>"
                ),
            }
        },
    )

    assert notice_type == "招标公告"
    assert "004 第四标段" in data["招标内容与范围"]
    assert "交货期" not in data["招标内容与范围"]


def test_candidate_and_award_parse_pdf_text_fallback():
    candidate_text = """候选项目中标候选人公示
（招标编号：ZYYH-20260604）
公示开始时间：2026年7月30日12时30分 公示结束时间：2026年8月2日12时30分
一、中标候选人基本情况
排序 中标候选人名称 其他报价
1 辽宁福泰石油机械制造有限公司 投标预估总价(含税):1843506元
二、中标候选人按照招标文件要求承诺的项目负责人情况
"""
    _, candidate, _, _, _ = BitbidParser.parse("candidate", {"hxrInfo": {
        "id": 73971, "gongGaoMingCheng": "候选项目中标候选人公示",
        "faBuTime": "2026-07-30 12:14:27",
    }}, pdf_text=candidate_text)
    assert candidate["中标候选人名称"] == ["辽宁福泰石油机械制造有限公司"]
    assert candidate["中标候选人报价"] == ["1843506元"]
    assert candidate["项目编号"] == ""
    assert candidate["招标编号"] == "ZYYH-20260604"
    assert "2026年7月30日" in candidate["公示时间"]

    award_text = """结果项目中标结果公示
（招标编号：SXZX-20260627-013）
001不分标段：
中标人：华夏智联电子实业有限公司 中标价格：1275.122000万元
"""
    _, award, _, _, _ = BitbidParser.parse("award", {"zbjgInfo": {
        "id": 222457, "gongGaoMingCheng": "结果项目中标结果公示",
        "faBuTime": "2026-07-30 11:44:05",
    }}, pdf_text=award_text)
    assert award["中标人名称"] == ["华夏智联电子实业有限公司"]
    assert award["中标价"] == ["1275.122000万元"]
    assert award["项目编号"] == ""
    assert award["招标编号"] == "SXZX-20260627-013"

    spaced_label_text = """某工程中标结果公示
（招标编号：M1100000048005586001）
一、中标人信息
中 标 人:国源鑫建设有限公司
中标价格:4478378.58元
"""
    _, spaced_award, _, _, _ = BitbidParser.parse(
        "award",
        {"zbjgInfo": {
            "id": 222593,
            "gongGaoMingCheng": "某工程中标结果公示",
        }},
        pdf_text=spaced_label_text,
    )
    assert spaced_award["中标人名称"] == ["国源鑫建设有限公司"]
    assert spaced_award["中标价"] == ["4478378.58元"]


def test_award_table_keeps_all_shortlisted_supplier_discount_pairs():
    html = """
    <table>
      <tr><th>入围供应商</th><th>下浮率</th></tr>
      <tr><td>山西福源医药有限公司</td><td>下浮率10%</td></tr>
      <tr><td>国药控股山西有限公司</td><td>下浮率7%</td></tr>
      <tr><td>河北荷花池药业有限公司</td><td>下浮率10%</td></tr>
    </table>
    """
    _, award, _, _, _ = BitbidParser.parse(
        "award",
        {"zbjgInfo": {
            "id": 222219,
            "gongGaoMingCheng": "中药饮片供应商入围项目中标结果公示",
            "neiRong": html,
        }},
    )

    assert award["中标人名称"] == [
        "山西福源医药有限公司",
        "国药控股山西有限公司",
        "河北荷花池药业有限公司",
    ]
    assert award["中标价"] == ["下浮率10%", "下浮率7%", "下浮率10%"]
    assert len(award["中标结果明细"]) == 3

    narrative = """
    一、中标人信息：
    001第一标段：
    入围供应商：山西福源医药有限公司 其他类型中标价：下浮率10%
    入围供应商：国药控股山西有限公司 其他类型中标价：下浮率7%
    """
    _, narrative_award, _, _, _ = BitbidParser.parse(
        "award",
        {"zbjgInfo": {
            "id": 222220,
            "gongGaoMingCheng": "供应商入围项目中标结果公示",
            "neiRong": narrative,
        }},
    )
    assert narrative_award["中标人名称"] == [
        "山西福源医药有限公司", "国药控股山西有限公司"
    ]
    assert narrative_award["中标价"] == ["下浮率10%", "下浮率7%"]


def test_live_html_identifiers_candidate_prices_and_purchase_contacts():
    candidate_html = """
    <p>（招标编号：SXHS-2026-027）</p>
    <p>本项目（招标项目编号：M1100000048005562001），现公示如下：</p>
    <p>标段（包）001第一标段：</p>
    <p>1、中标候选人基本情况</p>
    <p>排序 中标候选人名称 费率报价（%） 质量 服务期</p>
    <p>1 甲能源有限公司 光伏收益的4.95%/年、其他收益的9.4%/年 合格 五年</p>
    <p>2、中标候选人按照招标文件要求承诺的项目负责人情况</p>
    <p>标段（包）002第二标段：</p>
    <p>1、中标候选人基本情况</p>
    <p>排序 中标候选人名称 投标报价（元） 质量 工期</p>
    <p>1 乙建设有限公司 575049.13 合格 10天</p>
    <p>2、中标候选人按照招标文件要求承诺的项目负责人情况</p>
    """
    _, candidate, _, _, _ = BitbidParser.parse(
        "candidate",
        {"hxrInfo": {
            "id": 1,
            "gongGaoMingCheng": "测试项目中标候选人公示",
            "neiRong": candidate_html,
        }},
    )
    normalized = canonicalize_notice_data(
        "中标候选人公示",
        candidate,
        include_parser_diagnostics=True,
    )
    assert normalized["项目编号"] == "M1100000048005562001"
    assert normalized["招标编号"] == "SXHS-2026-027"
    assert normalized["中标候选人名称"] == ["甲能源有限公司", "乙建设有限公司"]
    assert normalized["中标候选人报价"] == [
        "光伏收益的4.95%/年、其他收益的9.4%/年",
        Decimal("575049.13"),
    ]
    assert [row["标段"] for row in normalized["中标候选人明细"]] == [
        "001第一标段",
        "002第二标段",
    ]

    tender_html = """
    <p>曲沃监狱药品采购招标公告</p>
    <p>（招标编号：AGENT-2026-01）</p>
    <p>本项目（招标项目编号：M1100000048005626001）进行采购。</p>
    <p>开标时间：2026年8月26日9时00分</p>
    <p>采购人：山西省曲沃监狱</p><p>联系人：贺先生</p><p>联系电话：15300000000</p>
    <p>采购代理机构：山西测试项目管理有限公司</p><p>项目联系人：薛女士</p><p>电话：0357-4000000</p>
    """
    _, tender, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 2,
            "gongGaoMingCheng": "曲沃监狱药品采购招标公告",
            "gongGaoNeiRong": tender_html,
        }},
    )
    normalized_tender = canonicalize_notice_data("招标公告", tender)
    assert normalized_tender["项目编号"] == "M1100000048005626001"
    assert normalized_tender["招标编号"] == "AGENT-2026-01"
    assert str(normalized_tender["开标时间"]) == "2026-08-26 09:00:00"
    assert normalized_tender["招标人/采购人名称"] == "山西省曲沃监狱"
    assert normalized_tender["招标代理机构"] == "山西测试项目管理有限公司"
    assert normalized_tender["招标代理机构联系人"] == "薛女士"


def test_malformed_duplicate_project_prefix_keeps_complete_identifier():
    text = "本项目（招标项目编号：M M1100000048002138001），已批准建设。"
    _, tender, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 4,
            "gongGaoMingCheng": "重复前缀项目招标公告",
            "gongGaoNeiRong": text,
        }},
    )
    assert tender["项目编号"] == "M1100000048002138001"


def test_identifier_stops_before_next_inline_field_label():
    text = (
        "项目编号：0773-2041GNOAHWGK3792项目名称：医疗设备购置项目"
        "预算金额：697.5758万元采购需求：共分5包"
    )
    _, tender, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 5,
            "gongGaoMingCheng": "内联字段项目招标公告",
            "gongGaoNeiRong": text,
        }},
    )
    assert tender["项目编号"] == "0773-2041GNOAHWGK3792"

    numbered = "招标编号：遂政采招[2018]83号2.3、招标范围：摄影大赛服务"
    _, numbered_tender, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 6,
            "gongGaoMingCheng": "内联编号字段项目招标公告",
            "gongGaoNeiRong": numbered,
        }},
    )
    assert numbered_tender["招标编号"] == "遂政采招[2018]83号"

    appended = "招标编号：0724-1800C37N2614涉及包号：/01"
    _, appended_tender, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 7,
            "gongGaoMingCheng": "涉及包号项目招标公告",
            "gongGaoNeiRong": appended,
        }},
    )
    assert appended_tender["招标编号"] == "0724-1800C37N2614"

    nested = "项目编号：GLZC2021-G1-990401-YZLZ（代理编号：YLGLG20211001-A）"
    _, nested_tender, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 8,
            "gongGaoMingCheng": "嵌套代理编号项目招标公告",
            "gongGaoNeiRong": nested,
        }},
    )
    assert nested_tender["项目编号"] == "GLZC2021-G1-990401-YZLZ"
    assert nested_tender["招标编号"] == "YLGLG20211001-A"

    prose = "招标项目编号：M1100000048002841001已由发展改革委批准建设"
    _, prose_tender, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 9,
            "gongGaoMingCheng": "紧接审批正文项目招标公告",
            "gongGaoNeiRong": prose,
        }},
    )
    assert prose_tender["项目编号"] == "M1100000048002841001"


def test_invalid_api_title_is_not_used_as_tender_identifier():
    _, tender, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 10,
            "gongGaoMingCheng": "某建设项目招标公告",
            "gongGaoBianHao": "涵江区安置区3#楼建设项目房建市政_经评审的最低投标价中标法A招标公告(网上投标)（施工）",
            "gongGaoNeiRong": "本公告未公开项目编号。",
        }},
    )
    assert tender["招标编号"] == ""


def test_waste_result_routes_to_correction_schema_and_cleans_project_name():
    title = "某岗位劳务外包采购废标结果公示"
    notice_type, data, _, _, _ = BitbidParser.parse(
        "award",
        {"zbjgInfo": {
            "id": 3,
            "gongGaoMingCheng": title,
            "neiRong": "某项目（招标编号：HP260901-097）进行公开招标，因有效投标人不足废标。",
        }},
    )
    assert notice_type == "更正结果公示"
    assert data["公共类型"] == "废标公告"
    assert data["项目名称"] == "某岗位劳务外包采购"
    assert data["招标编号"] == "HP260901-097"


def test_prequalification_and_correction_use_latest_schema():
    prequal_type, prequal, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 11,
            "gongGaoMingCheng": "某工程资格预审公告",
            "gongGaoNeiRong": (
                "<p>项目名称：某工程</p>"
                "<p>项目概况与招标范围：建设一栋厂房。</p>"
                "<p>申请人资格要求：具备建筑资质。</p>"
            ),
        }},
    )
    assert prequal_type == "资格预审公告"
    assert prequal["源站公告性质"] == "资格预审公告"
    assert prequal["项目概况与招标范围"] == "建设一栋厂房。"
    assert "招标内容与范围" not in prequal

    correction_type, correction, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 12,
            "gongGaoMingCheng": "某工程开标时间变更公告",
            "gongGaoNeiRong": (
                "<p>项目名称：某工程</p>"
                "<p>原开标时间：2026年8月20日9时</p>"
                "<p>变更后开标时间：2026年8月25日9时</p>"
                "<p>变更内容：开标时间延期。</p>"
            ),
        }},
    )
    assert correction_type == "更正结果公示"
    assert correction["公共类型"] == "变更公告"
    assert correction["开标时间"] == "2026年8月25日9时"
    assert correction["公告内容"] == "开标时间延期。"

    for notice_type, data in (
        (prequal_type, prequal),
        (correction_type, correction),
    ):
        expected = set(ANNOUNCEMENT_SCHEMAS[notice_type]) | set(
            PARSER_DIAGNOSTIC_FIELDS.get(notice_type, ())
        )
        assert set(data) == expected


def test_result_mixed_into_tender_column_uses_award_schema_and_route():
    notice_type, data, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 15,
            "gongGaoMingCheng": "某设备采购项目成交公告",
            "gongGaoNeiRong": (
                "<p>项目名称：某设备采购项目</p>"
                "<p>中标人：甲科技有限公司 中标价：98万元</p>"
            ),
        }},
    )
    assert notice_type == "中标结果公示"
    assert data["项目名称"] == "某设备采购项目"
    assert data["中标人名称"] == ["甲科技有限公司"]
    assert BitbidMultiFormatPipeline.ROUTES["award"] == (
        "比比网_中标结果公示", "中标结果公示"
    )


def test_candidates_mixed_into_tender_column_use_candidate_schemas():
    candidate_type, candidate, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 16,
            "gongGaoMingCheng": "某道路工程中标候选人公示",
            "gongGaoNeiRong": (
                "<p>项目名称：某道路工程</p>"
                "<p>第一中标候选人：甲建设有限公司</p>"
            ),
        }},
    )
    assert candidate_type == "中标候选人公示"
    assert candidate["项目名称"] == "某道路工程"
    assert candidate["中标候选人名称"] == ["甲建设有限公司"]

    final_type, final_data, _, _, _ = BitbidParser.parse(
        "tender",
        {"ggInfo": {
            "id": 17,
            "gongGaoMingCheng": "某道路工程定标候选人公示",
            "gongGaoNeiRong": """
                <p>项目名称：某道路工程</p>
                <table><tr><th>定标候选人名称</th><th>投标报价</th></tr>
                <tr><td>乙工程集团有限公司</td><td>1200万元</td></tr></table>
            """,
        }},
    )
    assert final_type == "定标候选人公示"
    assert final_data["定标候选人名称"] == ["乙工程集团有限公司"]
    assert final_data["定标候选人报价"] == ["1200万元"]
    assert set(final_data) == set(ANNOUNCEMENT_SCHEMAS["定标候选人公示"])


def test_candidate_html_table_and_narrative_keep_name_price_alignment():
    table_html = """
    <p>项目名称：联合体项目</p>
    <table><tr><th>排序</th><th>中标候选人名称</th><th>投标报价（元）</th></tr>
    <tr><td>1</td><td>甲建设有限公司、乙设计院联合体</td><td>1234567.89</td></tr>
    <tr><td>2</td><td>丙工程集团有限公司</td><td></td></tr></table>
    """
    _, table_data, _, _, _ = BitbidParser.parse(
        "candidate",
        {"hxrInfo": {
            "id": 13,
            "gongGaoMingCheng": "联合体项目中标候选人公示",
            "neiRong": table_html,
        }},
    )
    assert table_data["中标候选人名称"] == [
        "甲建设有限公司、乙设计院联合体", "丙工程集团有限公司"
    ]
    assert table_data["中标候选人报价"] == ["1234567.89", None]

    narrative = """项目名称：设备采购项目
第一中标候选人：普罗生物技术（上海）有限公司
第二中标候选人：颐思特（武汉）生物科技有限公司
"""
    _, narrative_data, _, _, _ = BitbidParser.parse(
        "candidate",
        {"hxrInfo": {
            "id": 14,
            "gongGaoMingCheng": "设备采购项目招标公告中标候选人公示",
            "neiRong": narrative,
        }},
    )
    assert narrative_data["项目名称"] == "设备采购项目"
    assert narrative_data["中标候选人名称"] == [
        "普罗生物技术（上海）有限公司", "颐思特（武汉）生物科技有限公司"
    ]
    assert narrative_data["中标候选人报价"] == [None, None]


def test_spider_defaults_and_exports():
    spider = BitbidSpider()
    assert spider.categories == ("plan", "tender", "candidate", "award")
    assert BitbidMultiFormatPipeline.ROUTES["award"][0] == "比比网_中标结果公示"


def test_list_requests_use_persistent_dupefilter_across_chunks():
    spider = BitbidSpider(categories="tender")

    first_page = spider._list_request("tender", 1)
    next_page = spider._list_request("tender", 2)

    assert first_page.dont_filter is False
    assert next_page.dont_filter is False


def test_spider_direct_mode_enables_access_guard():
    settings = Settings({
        "CRAWLER_OUTBOUND_MODE": "direct",
        "ITEM_PIPELINES": {},
        "DOWNLOADER_MIDDLEWARES": {},
    })
    BitbidSpider.update_settings(settings)
    middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
    assert middlewares[
        "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
    ] == 650
    assert middlewares[
        "crawler_scrapy.transport.proxy_middleware.StaticProxyMiddleware"
    ] is None
    assert settings.getbool("HTTPPROXY_ENABLED") is False
    pipelines = settings.getdict("ITEM_PIPELINES")
    assert pipelines[
        "crawler_scrapy.ai.hybrid_pipeline.HybridAiExtractionPipeline"
    ] == 200
    assert pipelines["crawler_scrapy.pipelines.AiHtmlExtractionPipeline"] is None
    assert "资格预审公告" in BitbidSpider.ai_candidate_fields
    assert "更正结果公示" in BitbidSpider.ai_candidate_fields


def test_export_keeps_trace_snapshot_and_standard_correction_code(tmp_path):
    class Stats:
        def __init__(self):
            self.values = {}

        def inc_value(self, key, count=1):
            self.values[key] = self.values.get(key, 0) + count

        def get_value(self, key, default=None):
            return self.values.get(key, default)

    spider = BitbidSpider(categories="award", parse_pdf="false")
    crawler = SimpleNamespace(
        spider=spider,
        settings=Settings(
            {
                "NOTICE_OUTPUT_ROOT": str(tmp_path),
                "NOTICE_SNAPSHOT_ENABLED": True,
                "NOTICE_EXPORT_INCLUDE_META": True,
                "NOTICE_EXPORT_DIAGNOSTICS": True,
                "NOTICE_EXPORT_TRACE": True,
            }
        ),
        stats=Stats(),
    )
    spider.crawler = crawler
    snapshot = HtmlSnapshotPipeline.from_crawler(crawler)
    schema = NoticeSchemaPipeline.from_crawler(crawler)
    exporter = BitbidMultiFormatPipeline.from_crawler(crawler)
    snapshot.open_spider()
    exporter.open_spider()

    raw_html = "<p>某项目（招标编号：HP260901-097）因有效投标人不足废标。</p>"
    item = spider.build_notice_item(
        notice_type="更正结果公示",
        notice_subtype="award",
        notice_id="waste-001",
        title="某岗位劳务外包采购废标结果公示",
        publish_time="2026-08-04 10:00:00",
        detail_url="https://www.bitbid.cn/bidding-detail.html?id=waste-001",
        data={
            "公共类型": "废标公告",
            "项目名称": "某岗位劳务外包采购",
            "招标编号": "HP260901-097",
            "发布网站": config.PLATFORM_NAME,
        },
        raw_data={"detail": {"id": "waste-001", "content": raw_html}},
        raw_html=raw_html,
        raw_text="某项目（招标编号：HP260901-097）因有效投标人不足废标。",
    )
    item = snapshot.process_item(item)
    item = schema.process_item(item)
    exporter.process_item(item)
    exporter.close_spider()

    digest = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
    rows = json.loads(
        (tmp_path / "bitbid/json/比比网_更正及其他公告.json").read_text(
            encoding="utf-8"
        )
    )
    row = rows[0]
    assert row["公告类型"] == "CORRECTION"
    assert row["公共类型"] == "废标公告"
    assert row["HTML快照SHA256"] == digest
    assert "rawHtml" not in row["_trace"]
    assert "rawText" not in row["_trace"]
    assert "exportMetadata" not in row["_trace"]
    assert (tmp_path / row["HTML快照路径"]).read_text(encoding="utf-8") == raw_html
