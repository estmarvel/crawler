from scrapy.http import HtmlResponse, Request

from crawler_scrapy.sites.llggzy import config
from crawler_scrapy.sites.llggzy.parser import LlggzyParser
from crawler_scrapy.spiders.llggzy import LlggzySpider


def test_all_frontend_modules_and_leaf_categories_are_configured():
    assert len(config.MODULES) == 8
    assert len(config.DEFAULT_FEEDS) == 60
    assert "engineering.plan" in config.DEFAULT_FEEDS
    assert "property.failure" in config.DEFAULT_FEEDS
    assert "mining.transfer" in config.DEFAULT_FEEDS
    assert "other.contract" in config.DEFAULT_FEEDS


def test_static_cms_pagination_and_list_parser():
    html = """<ul class="notice-list"><li><a href="/gcjsZbgg/60573.htm?pa=1" title="项目施工">[招标公告]项目施工</a><span>2026-08-14</span></li></ul>"""
    spider = LlggzySpider(feeds="engineering.tender", max_records=1)
    response = HtmlResponse(url=config.list_url("engineering.tender"), body=html.encode(), encoding="utf-8",
                            request=Request(config.list_url("engineering.tender")))
    requests = list(spider.parse_list(response, "engineering.tender", 1))
    assert len(requests) == 1
    assert requests[0].url.endswith("/gcjsZbgg/60573.htm?pa=1")


def test_pdf_text_is_parsed_with_site_specific_fields():
    text = """前合会村上下水管网建设项目施工
招标公告
（招标编号：SS141127202608100185）
招标项目所在地区：山西省 吕梁市 岚县
2.3 建设地点：岚县上明乡前合会村
2.5 资金来源：县级财政帮扶资金
2.6 计划工期：90天
4.1获取时间：2026年8月14日17时00分至2026年9月4日09时30分。
6.1 开标时间：2026年9月4日09时30分。
招 标 人：岚县上明乡人民政府
地 址：岚县上明乡上明村
联 系 人：兰先生
联系电话：15935176771
招标代理机构：山西建瓴全咨项目管理有限公司
地 址：太原市小店区长治路89号
联 系 人：张先生
电 话：17335080303"""
    parsed, html = LlggzyParser.parse("engineering.tender", "前合会村上下水管网建设项目施工",
                                      "2026年08月14 16:23:00", text, "http://x/60573.htm", [])
    assert parsed.notice_type == "招标公告"
    assert parsed.data["项目编号/招标编号"] == "SS141127202608100185"
    assert parsed.data["开标时间"]
    assert parsed.data["招标人联系人"] == "兰先生"
    assert parsed.data["招标代理机构联系人"] == "张先生"
    assert 'data-source="llggzy-cms-embedded-pdf"' in html


def test_source_category_forces_correction_and_candidate_schema():
    correction, _ = LlggzyParser.parse(
        "engineering.clarification", "项目变更公告", "2026-08-14",
        "招标编号：E001\n原开标时间：2026年8月20日09:30\n现变更为：2026年8月25日09:30",
        "http://x/1.htm", [],
    )
    assert correction.notice_type == "更正结果公示"
    assert correction.data["公共类型"] == "变更公告/澄清答疑"
    assert correction.data["开标时间"] == "2026年8月25日09:30"
    candidate, _ = LlggzyParser.parse("water.candidate", "某项目", "2026-08-14", "招标编号：E002",
                                      "http://x/2.htm", [])
    assert candidate.notice_type == "中标候选人公示"


def test_other_tender_uses_all_page_because_frontend_link_is_wrong():
    assert config.feed_info("other.tender")["path"] == "qtgc"
    assert config.feed_info("other.award")["path"] == "zbgg"


def test_real_pdf_plan_labels_are_not_lost_by_line_wrapping():
    text = """一、项目代码：2603-141124-89-05-346948
二、项目名称：山西省临县地质灾害综合治理项目
三、建设内容及规模：拟对三个隐患点进行工程治理。
四、建设地点：山西省临县三个村
五、项目总投资(万元)：1698.19
六、招标内容：施工
七、项目类型：国土资源
八、招标方式：公开招标
九、招标人名称：临县自然资源局
十、发布单位：某公司
十一、行政监督部门：吕梁市规划和自然资源局
十二、招标公告预计发布时间：2026-09"""
    parsed, _ = LlggzyParser.parse("other.plan", "项目招标计划", "2026-08-10", text, "http://x/3.htm", [])
    assert parsed.data["项目编号"] == "2603-141124-89-05-346948"
    assert parsed.data["项目总投资"] == "1698.19"
    assert parsed.data["招标人名称"] == "临县自然资源局"
    assert parsed.data["建设地点"] == "山西省临县三个村"


def test_real_pdf_candidate_table_and_contract_sentence_are_extracted():
    candidate_text = """公示开始时间：2026年08月14日16：00
公示结束时间：2026年08月17日16：00
序号 中标候选人名称 投标报价（元） 工期
1 江苏雷耀电梯工程有限公司 3687000.00 90日历天
2 山西杰创电梯销售有限公司 3605000.00 90日历天"""
    parsed, _ = LlggzyParser.parse("engineering.candidate", "项目中标候选人公示", "2026-08-14",
                                    candidate_text, "http://x/4.htm", [])
    assert parsed.data["中标候选人名称"] == ["江苏雷耀电梯工程有限公司", "山西杰创电梯销售有限公司"]
    assert parsed.data["中标候选人报价"] == ["3687000.00元", "3605000.00元"]
    assert "2026年08月14日" in parsed.data["公示时间"]
    contract_text = """2026年8月10日，招标人柳林县交通运输局（统一社会信用代码A）与中标人笙凌建设有限公司（统一社会信用代码B）签订《柳林县公路工程建设工程施工合同》，合同金额为1212.680080万元。"""
    parsed, _ = LlggzyParser.parse("transport.contract", "柳林县公路工程施工项目", "2026-08-14",
                                    contract_text, "http://x/5.htm", [])
    assert parsed.data["招标人名称"] == "柳林县交通运输局"
    assert parsed.data["中标人名称"] == "笙凌建设有限公司"
    assert parsed.data["合同金额"] == "1212.680080万元"
    assert parsed.data["合同签署时间"] == "2026年8月10日"


def test_award_table_does_not_export_heading_as_winner_and_datetime_is_clean():
    award_text = """一、中标人信息：
排序 中标人名称 投标报价（元） 质量 工期
1 山西东盛亿电力工程有限
公司 17691518.95元 合格 365日历天
项目经理：张一峰
证书名称：二级注册建造师
编号：晋142021202370676"""
    parsed, _ = LlggzyParser.parse("power.award", "项目中标结果公示", "2026-08-01",
                                    award_text, "http://x/6.htm", [])
    assert parsed.data["中标人名称"] == ["山西东盛亿电力工程有限公司"]
    assert parsed.data["中标价"] == ["17691518.95元"]
    tender_text = "投标截止时间：2026 年 9 月 15 日 9:30，投标人应使用电子交易平台提交"
    parsed, _ = LlggzyParser.parse("power.tender", "项目招标公告", "2026-08-01",
                                    tender_text, "http://x/7.htm", [])
    assert str(parsed.data["开标时间"]) in {"2026-09-15 09:30:00", "2026-09-15 09:30:00.000"}
