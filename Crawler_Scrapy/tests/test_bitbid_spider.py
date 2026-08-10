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
from crawler_scrapy.schemas.notice_fields import canonicalize_notice_data
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


def test_tender_parses_html_and_direct_api_fields():
    payload = {
        "xmInfo": {"faBaoMingCheng": "场馆运营服务项目", "shengName": "山西省", "shiName": "晋中市"},
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
    assert data["项目规模"] == "场馆运营。"
    assert data["工期/服务期/供货日期"] == "10年"
    assert data["项目编号/招标编号"] == "SXHRZB-2026060"
    assert data["项目编号"] == ""
    assert data["招标编号"] == "SXHRZB-2026060"
    assert attachments[0]["file_url"].startswith("http://www.bitbid.cn/auth/")
    assert attachments[0]["file_url"].endswith("zbGongGao.id=10689965")
    assert "场馆运营" in text


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


def test_waste_result_keeps_termination_semantics_and_clean_project_name():
    title = "某岗位劳务外包采购废标结果公示"
    _, data, _, _, _ = BitbidParser.parse(
        "award",
        {"zbjgInfo": {
            "id": 3,
            "gongGaoMingCheng": title,
            "neiRong": "某项目（招标编号：HP260901-097）进行公开招标，因有效投标人不足废标。",
        }},
    )
    assert data["源站公告性质"] == "废标结果公示"
    assert data["项目名称"] == "某岗位劳务外包采购"
    assert data["招标编号"] == "HP260901-097"


def test_spider_defaults_and_exports():
    spider = BitbidSpider()
    assert spider.categories == ("plan", "tender", "candidate", "award")
    assert BitbidMultiFormatPipeline.ROUTES["award"][0] == "比比网_中标结果公示"


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


def test_export_keeps_trace_snapshot_and_termination_code(tmp_path):
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
        notice_type="中标结果公示",
        notice_subtype="award",
        notice_id="waste-001",
        title="某岗位劳务外包采购废标结果公示",
        publish_time="2026-08-04 10:00:00",
        detail_url="https://www.bitbid.cn/bidding-detail.html?id=waste-001",
        data={
            "项目名称": "某岗位劳务外包采购",
            "源站公告性质": "废标结果公示",
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
        (tmp_path / "bitbid/json/比比网_中标结果公示.json").read_text(
            encoding="utf-8"
        )
    )
    row = rows[0]
    assert row["公告类型"] == "TERMINATION"
    assert row["HTML快照SHA256"] == digest
    assert row["_trace"]["rawHtml"] == raw_html
    assert row["_trace"]["integrity"]["rawHtmlSha256"] == digest
    assert (tmp_path / row["HTML快照路径"]).read_text(encoding="utf-8") == raw_html
