from crawler_scrapy.sites.sxjkzcpt import config
from crawler_scrapy.sites.sxjkzcpt.parser import (
    SxjkzcptParser,
    classify_category,
    extract_csrf,
    parse_list_records,
    parse_total_pages,
)
from crawler_scrapy.spiders.sxjkzcpt import SxjkzcptSpider


def _detail(title: str, body: str, *, nbgg: bool = False) -> str:
    return f"""<html><body>
    <h1 class='firth-tit'>{title}</h1>
    <table id='contentBody'><tr><th>项目名称:</th><td>测试高速项目</td>
    <th>交控集团招采认证编号:</th><td>JKJT-2026-001</td></tr></table>
    <div id='content'>{body}</div>
    <p class='remark'>发布时间：2026-08-05 12:30:01</p>
    <script>var isNbgg = {'true' if nbgg else 'false'};</script>
    </body></html>"""


def test_list_html_and_page_count_are_parsed():
    html = """<div class='erjizt-right-cont-dt'>
    <a onclick=\"toDetail('abc-1')\"><span class='dt-texta'>3</span>
    <span class='dt-textb' title='测试公告'>测试公告</span>
    <span class='dt-texte'>2026-08-05</span></a>
    </div><script>var totalPage = 17;</script>"""
    assert parse_list_records(html)[0].notice_id == "abc-1"
    assert parse_list_records(html)[0].title == "测试公告"
    assert parse_list_records(html)[0].publish_time == "2026-08-05"
    assert parse_total_pages(html) == 17
    assert extract_csrf("<input name='_csrf' value='token-1'>") == "token-1"


def test_mixed_source_rows_are_classified_by_actual_title():
    assert classify_category("award", "某项目流标公告")[0] == "termination"
    assert classify_category("candidate", "某项目中标候选人公示更正")[0] == "correction"
    assert classify_category("award", "某项目中标候选人公示")[0] == "candidate"
    assert classify_category("change", "某项目开标延期公告")[0] == "change"


def test_tender_fields_and_identifiers_are_separated():
    parsed = SxjkzcptParser.parse(
        "zbcg.tender",
        _detail(
            "测试高速项目招标公告",
            """<p>投资项目统一代码：2601-140100-89-01-123456</p>
            <p>招标编号：JKJT-2026-001</p><p>项目资金来源为：企业自筹</p>
            <p>二、项目概况和招标范围</p><p>项目规模：改造10公里</p>
            <p>三、投标人资格要求</p><p>具备公路资质</p>
            <p>十一、联系方式</p><p>招标人：甲公司</p><p>联系人：张三</p>
            <p>电话：123456</p>""",
        ),
    )
    assert parsed.notice_type == "招标公告"
    assert parsed.data["项目编号"] == "2601-140100-89-01-123456"
    assert parsed.data["招标编号"] == "JKJT-2026-001"
    assert parsed.data["项目编号/招标编号"] == (
        "2601-140100-89-01-123456；JKJT-2026-001"
    )
    assert parsed.data["资金来源"] == "企业自筹"
    assert parsed.data["发布日期"] == "2026-08-05 12:30:01"


def test_ca_restricted_detail_is_detected_and_not_extracted():
    parsed = SxjkzcptParser.parse(
        "qzbcg.tender", _detail("内部采购公告", "<p>受限正文</p>", nbgg=True)
    )
    assert parsed.access["requiresCa"] is True
    assert parsed.data == {}


def test_attachment_file_id_is_preserved_for_separate_download():
    parsed = SxjkzcptParser.parse(
        "qzbcg.award",
        _detail(
            "测试项目成交结果公告",
            "<p>成交供应商名称：乙公司</p>"
            "<button onclick=\"downloadFile('file_123')\">结果附件.pdf</button>",
        ),
    )
    assert parsed.attachments[0]["source_file_id"] == "file_123"
    assert parsed.attachments[0]["file_url"] == config.attachment_url("file_123")


def test_only_requested_public_feeds_are_selected():
    spider = SxjkzcptSpider(categories="tender,award", channels="qzbcg")
    assert spider.feeds == ("qzbcg.tender", "qzbcg.award")


def test_random_sample_page_plan_is_reproducible_and_spreads_records():
    first = SxjkzcptSpider(max_records=5, sample_mode="random", sample_seed=42)
    second = SxjkzcptSpider(max_records=5, sample_mode="random", sample_seed=42)
    plan = first._random_page_plan("zbcg.tender", 20)
    assert plan == second._random_page_plan("zbcg.tender", 20)
    assert len(plan) == 5
    assert sum(plan.values()) == 5
    assert all(quota == 1 for quota in plan.values())


def test_random_sample_uses_available_count_when_feed_has_one_page():
    spider = SxjkzcptSpider(max_records=5, sample_mode="random", sample_seed=42)
    assert spider._random_page_plan("zbcg.plan", 1) == {1: 5}


def test_candidate_parser_does_not_mix_later_person_and_response_tables():
    body = """
    <table><tr><td>排序</td><td>中标候选人名称</td><td>投标报价（元）</td></tr>
    <tr><td>1</td><td>甲建设有限公司</td><td>100.00</td></tr>
    <tr><td>2</td><td>乙建设有限公司</td><td>90.00</td></tr></table>
    <table><tr><td>序号</td><td>中标候选人名称</td><td>项目负责人名称</td></tr>
    <tr><td>1</td><td>甲建设有限公司</td><td>张三</td></tr></table>
    <table><tr><td>序号</td><td>中标候选人名称</td><td>响应情况</td></tr>
    <tr><td>1</td><td>甲建设有限公司</td><td>响应</td></tr></table>
    """
    parsed = SxjkzcptParser.parse(
        "qzbcg.candidate", _detail("测试项目中标候选人公示", body)
    )
    assert parsed.data["中标候选人名称"] == ["甲建设有限公司", "乙建设有限公司"]
    assert parsed.data["中标候选人报价"] == ["100.00", "90.00"]


def test_explicit_body_tender_number_wins_over_platform_certification_number():
    parsed = SxjkzcptParser.parse(
        "zbcg.tender",
        _detail(
            "电力迁改项目招标公告",
            "<p>（招标编号：2026-BUSINESS-001）</p>"
            "<p>招标项目编号：E1400000000001</p>",
        ),
    )
    assert parsed.data["项目编号"] == "E1400000000001"
    assert parsed.data["招标编号"] == "2026-BUSINESS-001"


def test_change_notice_uses_last_changed_open_time():
    parsed = SxjkzcptParser.parse(
        "zbcg.change",
        _detail(
            "某项目延期公告",
            """<p>开标时间：2026年7月31日9时00分</p>
            <p>现延期为：</p><p>开标时间：2026年8月4日9时00分</p>
            <p>投标文件递交截止时间：2026年8月4日9时00分</p>""",
        ),
    )
    assert parsed.data["开标时间"] == "2026年8月4日9时00分"
    assert parsed.data["开启时间"] == "2026年8月4日9时00分"


def test_tender_narrative_fields_are_extracted_without_false_open_place():
    parsed = SxjkzcptParser.parse(
        "zbcg.tender",
        _detail(
            "公路施工项目招标公告",
            """<p>项目资金来源为企业自有，招标人为甲公司。</p>
            <p>2.1项目规模</p><p>新建道路10公里。</p>
            <p>2.2招标范围：道路工程</p>
            <p>2.3计划工期</p><p>一标段6个月；二标段8个月。</p>
            <p>3.投标人资格要求：具备资质</p>
            <p>递交截止时间：2026年8月11日9时00分，投标人应在截止前上传文件。</p>
            <p>递交地址：某电子交易平台</p>
            <p>本次招标采用双信封综合评分法，评标办法详见附件。</p>""",
        ),
    )
    assert parsed.data["资金来源"] == "企业自有"
    assert parsed.data["项目规模"] == "新建道路10公里。"
    assert parsed.data["工期/服务期/供货日期"] == "一标段6个月；二标段8个月。"
    assert parsed.data["递交截止时间"] == "2026年8月11日9时00分"
    assert parsed.data["开启地点"] == ""
    assert parsed.data["评审办法"] == "双信封综合评分法"


def test_second_stage_competition_template_and_formal_contacts_are_extracted():
    parsed = SxjkzcptParser.parse(
        "qzbcg.tender",
        _detail(
            "库管及安保人员二阶段竞价采购公告",
            """<p>1.2 采购人：山西路桥第九工程有限公司。</p>
            <p>最高含税限价221206.52元；税率6%。</p>
            <p>2.2 供货期/服务期：合同签订后15日内。</p>
            <p>2.3 供货/服务地点：山西路桥第九工程有限公司。</p>
            <p>2.4 质量标准/服务要求：满足国家行业服务标准。</p>
            <p>6.3 履约保证金</p><p>联系人：梁先生</p><p>财务电话：18600000000</p>
            <p>7.1 竞价开始时间为 2026 年 8 月 06 日 10 时 00 分。</p>
            <p>7.2 竞价截止时间为竞价开始后30分钟。</p>
            <p>竞价地址：路桥科技大厦竞价室。</p>
            <p>10.联系方式</p><p>采 购 人：山西路桥第九工程有限公司</p>
            <p>地址：朔州市招远路</p><p>联系人：杨先生</p><p>电话：13903491585</p>""",
        ),
    )
    assert parsed.data["招标金额"] == "221206.52元"
    assert parsed.data["工期/服务期/供货日期"] == "合同签订后15日内。"
    assert parsed.data["项目地点"] == "山西路桥第九工程有限公司。"
    assert parsed.data["质量要求"] == "满足国家行业服务标准。"
    assert parsed.data["开标时间"] == "2026年8月06日10时00分"
    assert parsed.data["递交截止时间"] == "竞价开始后30分钟"
    assert parsed.data["开启地点"] == "路桥科技大厦竞价室。"
    assert parsed.data["招标人联系人"] == "杨先生"
    assert parsed.data["招标人联系方式"] == "13903491585"


def test_agency_project_manager_is_accepted_as_contact():
    parsed = SxjkzcptParser.parse(
        "zbcg.tender",
        _detail(
            "监理项目招标公告",
            """<p>十一、联系方式</p><p>招标人：甲公司</p>
            <p>联系人：张三</p><p>电话：123456</p>
            <p>招标代理机构：乙公司</p><p>项目负责人：吴春芳</p>
            <p>电话：0351-8868115</p>""",
        ),
    )
    assert parsed.data["招标代理机构联系人"] == "吴春芳"


def test_old_ca_prompt_is_restricted_even_when_is_nbgg_is_false():
    parsed = SxjkzcptParser.parse(
        "zbcg.tender",
        """<html><body><h1 class='firth-tit'>旧公告</h1>
        <div>登录 系统查看</div><div>交控集团及成员单位插入企业CA锁方可查看。</div>
        <div id='content'></div><script>var isNbgg = false;</script></body></html>""",
    )
    assert parsed.access["isNbgg"] is False
    assert parsed.access["requiresLogin"] is True
    assert parsed.access["requiresCa"] is True
    assert parsed.data == {}


def test_hidden_ca_template_prompt_does_not_block_public_body():
    parsed = SxjkzcptParser.parse(
        "zbcg.tender",
        """<html><body><h1 class='firth-tit'>公开招标公告</h1>
        <div style='display:none'>登录 系统查看；插入企业CA锁方可查看。</div>
        <div id='content'><p>项目名称：公开项目</p></div>
        <script>var isNbgg = false;</script></body></html>""",
    )
    assert parsed.access["publicContentPresent"] is True
    assert parsed.access["requiresLogin"] is False
    assert parsed.access["requiresCa"] is False
    assert parsed.data != {}


def test_truncated_heading_uses_body_to_recognize_lease_candidate_notice():
    body = """<p>某服务区加油站租赁承租候选人公示</p>
    <table><tr><td>排序</td><td>承租候选人名称</td><td>报价（元）</td></tr>
    <tr><td>1</td><td>中国石化销售股份有限公司山西分公司</td><td>11000000.00</td></tr></table>
    <p>三、联系方式</p><p>委托人：甲公司</p><p>联系人：孟先生</p>
    <p>电话：0351-7178328</p><p>产权交易机构：乙公司</p>
    <p>联系人：刘先生</p><p>电话：18635160035</p>"""
    parsed = SxjkzcptParser.parse(
        "zbcg.candidate", _detail("某服务区加油站租赁", body)
    )
    assert parsed.notice_type == "中标候选人公示"
    assert parsed.data["中标候选人名称"] == ["中国石化销售股份有限公司山西分公司"]
    assert parsed.data["中标候选人报价"] == ["11000000.00"]
    assert parsed.data["招标人联系人"] == "孟先生"
    assert parsed.data["招标代理机构联系人"] == "刘先生"


def test_candidate_total_price_header_is_recognized():
    body = """<table><tr><td>排序</td><td>中标候选人</td><td>含税总价（元）</td></tr>
    <tr><td>1</td><td>甲建设有限公司</td><td>77232039.34</td></tr></table>"""
    parsed = SxjkzcptParser.parse(
        "zbcg.candidate", _detail("园林项目中标候选人公示", body)
    )
    assert parsed.data["中标候选人报价"] == ["77232039.34"]


def test_award_consortium_and_placeholder_discount_are_cleaned():
    html = """<html><body><h1 class='firth-tit'>EPC项目中标结果公示</h1>
    <table id='contentBody'><tr><th>项目名称:</th><td>EPC项目</td>
    <th>成交供应商名称:</th><td>甲设计院有限公司</td>
    <th>中标金额(万元):</th><td>87.18%，换算后中标金额：/</td></tr></table>
    <div id='content'><p>中标人：甲设计院有限公司/乙建筑有限公司（联合体）</p>
    <p>中标价（折扣系数）：87.18%</p></div>
    <p class='remark'>发布时间：2026-08-05 12:30:01</p></body></html>"""
    parsed = SxjkzcptParser.parse("zbcg.award", html)
    assert parsed.data["中标人名称"] == ["甲设计院有限公司"]
    assert parsed.data["联合体成员"] == ["乙建筑有限公司"]
    assert parsed.data["中标价"] == ["87.18%"]


def test_change_with_conflicting_stale_header_does_not_save_old_open_time():
    html = """<html><body><h1 class='firth-tit'>项目延期公告</h1>
    <table id='contentBody'><tr><th>项目名称:</th><td>项目</td>
    <th>变更开标时间:</th><td>2024-12-11 09:00</td></tr></table>
    <div id='content'><p>原信息：</p><p>投标文件递交截止时间：2024年12月11日09时00分。</p>
    <p>现延期为：</p><p>投标文件递交截止时间：2024年12月17日09时00分。</p></div>
    <p class='remark'>发布时间：2024-12-01 12:00:00</p></body></html>"""
    parsed = SxjkzcptParser.parse("qzbcg.change", html)
    assert parsed.data["开标时间"] == ""
    assert parsed.data["递交截止时间"] == "2024年12月17日09时00分。"


def test_termination_reason_mentioning_tender_plan_stays_termination():
    parsed = SxjkzcptParser.parse(
        "qzbcg.change",
        _detail(
            "某项目招标撤销（终止）公告",
            "<p>现撤销该招标信息，原因：招标计划有变。</p>",
        ),
    )
    assert parsed.category == "termination"


def test_construction_funding_location_period_and_quality_labels_are_extracted():
    parsed = SxjkzcptParser.parse(
        "zbcg.tender",
        _detail(
            "住宅项目设计招标公告",
            """<p>建设资金来自企业自筹（资金来源），出资比例为100%。</p>
            <p>1、建设地址：河西新城规划四街西北角</p>
            <p>3、设计服务期限：自签订合同之日起112日历天。</p>
            <p>4、质量/技术标准：符合国家及地方设计要求。</p>""",
        ),
    )
    assert parsed.data["资金来源"] == "企业自筹"
    assert parsed.data["项目地点"] == "河西新城规划四街西北角"
    assert parsed.data["工期/服务期/供货日期"] == "自签订合同之日起112日历天。"
    assert parsed.data["质量要求"] == "符合国家及地方设计要求。"


def test_numbered_candidate_lines_keep_names_and_corresponding_prices():
    parsed = SxjkzcptParser.parse(
        "zbcg.candidate",
        _detail(
            "材料采购中标候选人公示",
            """<p>中标候选人1：甲商贸有限公司 投标报价：4500790.00元</p>
            <p>中标候选人2：乙商贸有限公司 投标报价：4,507,570.00元</p>""",
        ),
    )
    assert parsed.data["中标候选人名称"] == ["甲商贸有限公司", "乙商贸有限公司"]
    assert parsed.data["中标候选人报价"] == ["4500790.00元", "4,507,570.00元"]


def test_candidate_price_can_use_next_line_award_amount_label():
    parsed = SxjkzcptParser.parse(
        "qzbcg.candidate",
        _detail(
            "办公桌采购中标候选人公示",
            """<p>中标候选人名称：山西华宝科技有限公司</p>
            <p>中标金额：292500元（含税，税率13%）</p>""",
        ),
    )
    assert parsed.data["中标候选人名称"] == ["山西华宝科技有限公司"]
    assert parsed.data["中标候选人报价"] == ["292500元（含税，税率13%）"]


def test_award_body_fills_project_price_owner_and_consortium_parties():
    html = """<html><body><h1 class='firth-tit'>商务基地设计项目中标结果公示</h1>
    <div id='content'><p>商务基地设计项目（招标项目编号：M140100001），经评审确定中标人。</p>
    <p>中标人：牵头人：甲设计有限公司</p><p>联合体单位：乙顾问有限公司</p>
    <p>中标价：1191.5万元</p><p>四、联系方式：</p>
    <p>招标单位：建设单位有限公司</p><p>地址：太原市龙城大街1号</p>
    <p>联系人：崔先生</p><p>电话：0351-2531977</p></div>
    <p class='remark'>发布时间：2020-04-08 11:06:01</p></body></html>"""
    parsed = SxjkzcptParser.parse("zbcg.award", html)
    assert parsed.data["项目名称"] == "商务基地设计项目"
    assert parsed.data["中标人名称"] == ["甲设计有限公司"]
    assert parsed.data["联合体成员"] == ["乙顾问有限公司"]
    assert parsed.data["中标价"] == ["1191.5万元"]
    assert parsed.data["招标人/采购人"] == "建设单位有限公司"
    assert parsed.data["招标人联系方式"] == "0351-2531977"


def test_owner_embedded_phone_allows_parentheses_inside_company_name():
    parsed = SxjkzcptParser.parse(
        "zbcg.candidate",
        _detail(
            "材料采购中标候选人公示",
            """<p>招标人：某EPC总承包（包八）项目部（电话：13753640927）</p>
            <p>中标候选人名称：甲公司</p><p>中标价：100元</p>""",
        ),
    )
    assert parsed.data["招标人/采购人"] == "某EPC总承包（包八）项目部"
    assert parsed.data["招标人联系方式"] == "13753640927"


def test_known_evaluation_method_is_normalized_from_sentence():
    parsed = SxjkzcptParser.parse(
        "zbcg.tender",
        _detail(
            "检测项目招标公告",
            "<p>评标办法：本项目采用的评标办法为双信封合理低价法。</p>",
        ),
    )
    assert parsed.data["评审办法"] == "双信封合理低价法"


def test_construction_funding_stops_before_tenderer_clause():
    parsed = SxjkzcptParser.parse(
        "qzbcg.tender",
        _detail(
            "养护工程招标公告",
            "<p>建设资金来自山西交通控股集团有限公司临汾分公司，"
            "招标人为临汾管理有限公司，本项目已具备招标条件。</p>",
        ),
    )
    assert parsed.data["资金来源"] == "山西交通控股集团有限公司临汾分公司"


def test_chinese_bracket_tender_number_is_not_truncated():
    parsed = SxjkzcptParser.parse(
        "qzbcg.candidate",
        _detail(
            "装修工程中标候选人公示",
            "<p>（招标编号：ZLZX招【2025】0311号）</p>"
            "<p>中标候选人名称：甲公司</p><p>中标价：100元</p>",
        ),
    )
    assert parsed.data["招标编号"] == "ZLZX招【2025】0311号"
