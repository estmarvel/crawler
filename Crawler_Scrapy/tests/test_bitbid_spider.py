from __future__ import annotations

from crawler_scrapy.sites.bitbid import config
from crawler_scrapy.sites.bitbid.exporter import BitbidMultiFormatPipeline
from crawler_scrapy.sites.bitbid.parser import BitbidParser
from crawler_scrapy.spiders.bitbid import BitbidSpider


def test_category_and_url_mapping():
    assert config.CATEGORIES["plan"]["gg_type"] == 4
    assert "ggType=1" in config.list_url("tender", 1, 10)
    assert "timeType=1" in config.list_url("tender", 1, 10)
    assert config.detail_api_url("candidate", 73971).endswith("/hxrInfo/73971")
    assert "dbZhongBiaoJieGuoGongGao.id=222457" in config.pdf_url("award", 222457)


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


def test_spider_defaults_and_exports():
    spider = BitbidSpider()
    assert spider.categories == ("plan", "tender", "candidate", "award")
    assert BitbidMultiFormatPipeline.ROUTES["award"][0] == "比比网_中标结果公示"
