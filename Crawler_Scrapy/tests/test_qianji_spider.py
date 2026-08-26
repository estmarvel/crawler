from __future__ import annotations
import base64
import json
from types import SimpleNamespace

from itemadapter import ItemAdapter
from scrapy.settings import Settings

from crawler_scrapy import settings as project_settings
from crawler_scrapy.sites.qianji import config
from crawler_scrapy.sites.qianji.ai_review import (
    audit_identifiers,
    extract_identifier_evidence,
    validate_model_result,
)
from crawler_scrapy.sites.qianji.parser import QianjiParser
from crawler_scrapy.sites.qianji.exporter import QianjiMultiFormatPipeline
from crawler_scrapy.sites.qianji.hybrid_ai import (
    HybridCandidate,
    HybridReviewResult,
    QianjiHybridAiExtractionPipeline,
    QianjiHybridAiService,
    candidate_is_grounded,
    candidate_matches_field,
    semantically_equal,
)
from crawler_scrapy.ai.html_extractor import AiExtractionConfig
from crawler_scrapy.schemas.notice_fields import ANNOUNCEMENT_SCHEMAS
from crawler_scrapy.ai.field_contracts import (
    FIELD_DEFINITIONS,
    FIELD_LABELS,
    build_candidate_windows,
    get_field_contract,
    normalize_contract_value,
    normalize_project_location,
    normalize_project_nature,
)
from crawler_scrapy.spiders.qianji import QianjiSpider

def enc(s): return base64.b64encode(s.encode()).decode()
def test_all_public_feeds_are_configured():
    assert len(config.FEEDS)==13
    assert config.FEEDS['change.goods'][1]=='货物'
    assert 'pageNum=2' in config.list_url('award.service',2,50)


def test_qianji_export_routes_merge_project_types_by_notice_category(tmp_path):
    class Stats:
        def __init__(self):
            self.values = {}

        def inc_value(self, key, count=1):
            self.values[key] = self.values.get(key, 0) + count

        def get_value(self, key, default=None):
            return self.values.get(key, default)

    spider = QianjiSpider(categories="tender", project_types="engineering,goods,service")
    crawler = SimpleNamespace(
        spider=spider,
        settings=Settings(
            {
                "NOTICE_OUTPUT_ROOT": str(tmp_path),
                "NOTICE_EXPORT_INCLUDE_META": True,
                "NOTICE_EXPORT_DIAGNOSTICS": True,
                "NOTICE_EXPORT_TRACE": True,
            }
        ),
        stats=Stats(),
    )
    spider.crawler = crawler

    def export(subtype, project_type, notice_id):
        item = spider.build_notice_item(
            notice_type="招标公告",
            notice_subtype=subtype,
            notice_id=notice_id,
            title=f"{project_type}测试招标公告",
            data={
                "项目名称": f"{project_type}测试项目",
                "项目类型/行业分类": project_type,
                "发布网站": config.PLATFORM_NAME,
            },
            raw_text=f"{project_type}测试公告正文",
        )
        exporter.process_item(item)

    exporter = QianjiMultiFormatPipeline.from_crawler(crawler)
    exporter.open_spider()
    export("tender.engineering", "工程", "merged-001")
    export("tender.goods", "货物", "merged-002")
    export("tender.service", "服务", "merged-003")
    exporter.close_spider()

    json_dir = tmp_path / "qianji/json"
    csv_dir = tmp_path / "qianji/csv"
    assert sorted(path.name for path in json_dir.glob("*.json")) == [
        "千极链_招标公告.json"
    ]
    assert sorted(path.name for path in csv_dir.glob("*.csv")) == [
        "千极链_招标公告.csv"
    ]
    rows = json.loads((json_dir / "千极链_招标公告.json").read_text())
    assert [row["公告子类型"] for row in rows] == [
        "tender.engineering",
        "tender.goods",
        "tender.service",
    ]
    assert [row["项目类型/行业分类"] for row in rows] == ["工程", "货物", "服务"]

    # 重新启动导出器时继续追加同一个合法 JSON 数组，不覆盖前三条。
    exporter = QianjiMultiFormatPipeline.from_crawler(crawler)
    exporter.open_spider()
    export("tender.goods", "货物", "merged-004")
    exporter.close_spider()
    rows = json.loads((json_dir / "千极链_招标公告.json").read_text())
    assert [row["公告ID"] for row in rows] == [
        "merged-001",
        "merged-002",
        "merged-003",
        "merged-004",
    ]


def test_qianji_has_merged_source_routes_and_separate_result_correction_route():
    assert len(QianjiMultiFormatPipeline.ROUTES) == 13
    assert len(QianjiMultiFormatPipeline.OUTPUT_ROUTES) == 6
    assert QianjiMultiFormatPipeline._route("tender.engineering") == (
        QianjiMultiFormatPipeline._route("tender.service")
    )
    assert QianjiMultiFormatPipeline._route_config("__qianji_tender__") == (
        "千极链_招标公告",
        "招标公告",
    )
    assert QianjiMultiFormatPipeline._route_config("__qianji_correction__") == (
        "千极链_更正结果公示",
        "更正结果公示",
    )
def test_base64_plan_mapping():
    d={'id':'p1','title':'项目','noticeStartTime':'2026-08-01 10:00','noticeEndTime':'202609','content':enc('<table><tr><td>项目名称：</td><td>测试项目</td></tr><tr><td>项目总投资（万元/人民币）：</td><td>440</td></tr><tr><td>招标方式：</td><td>公开招标</td></tr><tr><td>招标公告预计发布时间：</td><td>20260915000000</td></tr></table>')}
    typ,data,_,html,text=QianjiParser.parse('plan.all',d)
    assert typ=='招标计划' and data['项目名称']=='测试项目' and data['招标方式']=='公开招标'
    assert data['项目总投资'] == '440万元'
    assert data['招标公告（资格预审公告）预计发布时间'] == '2026-09-15'
    assert '<table>' in html and '440' in text


def test_qianji_rule_values_keep_roles_units_and_short_business_phrases():
    detail = {
        'id': 'rule-cleanup',
        'title': '测试项目招标公告',
        'content': enc(
            '<p>招标项目所在地区：山西省 阳泉市 平定县</p>'
            '<p>建设资金来源为自筹资金，招标人为甲单位。</p>'
            '<p>建设地址：阳泉市平定县。</p>'
            '<p>七、提交投标保证金的形式</p>'
            '<p>7.1本项目可以采用银行转账、银行保函、保证保险、电子保函等非现金交易担保方式提交投标保证金。</p>'
        ),
    }

    _, data, _, _, _ = QianjiParser.parse('tender.engineering', detail)

    assert data['资金来源'] == '自筹资金'
    assert data['项目地点'] == '阳泉市平定县。'
    assert data['投标保证金方式'] == (
        '银行转账、银行保函、保证保险、电子保函等非现金交易担保方式'
    )
def test_tender_uses_site_metadata():
    d={'id':'x','title':'道路工程招标公告','projectCode':'I001','bidSituation':'依法招标','bidTypeName':'公开招标','zbUnitName':'甲单位','dlUnitName':'乙代理','noticeStartTime':'2026-08-04 12:00','content':enc('<p>项目所在地：山西省晋城市</p><p>项目规模：道路改造</p>')}
    _,data,_,_,_=QianjiParser.parse('tender.engineering',d)
    assert data['项目编号/招标编号']=='I001' and data['项目类型/行业分类']=='工程'
    assert data['招标人/采购人名称']=='甲单位' and data['招标代理机构']=='乙代理'
    assert data['项目性质']=='依法必须招标'


def test_qianji_project_name_removes_notice_stage_and_round_suffixes():
    cases = {
        '测试道路工程二次招标公告': '测试道路工程',
        '测试道路工程招标二次变更公告': '测试道路工程',
        '测试道路工程招标暂停/终止公告': '测试道路工程',
        '测试道路工程招标控制价': '测试道路工程',
        '测试道路工程招标延期公告': '测试道路工程',
        '测试道路工程招标二次延期公告': '测试道路工程',
        '测试道路工程招标控制价变更': '测试道路工程',
        '测试道路工程中标结果公示更正': '测试道路工程',
    }
    for title, expected in cases.items():
        _, data, _, _, _ = QianjiParser.parse(
            'change.engineering', {'title': title}
        )
        assert data['项目名称'] == expected


def test_qianji_does_not_use_generic_notice_category_as_project_nature():
    _, data, _, _, _ = QianjiParser.parse(
        'award.engineering',
        {'title': '测试项目中标结果公示', 'bidSituation': '招标信息'},
    )
    assert data['项目性质'] == ''


def test_qianji_opening_time_is_not_copied_from_bid_opening_time():
    detail = {
        'title': '测试项目招标公告',
        'content': enc(
            '<p>开标时间：2026年8月25日10时00分</p>'
            '<p>递交截止时间：2026年8月25日10时00分</p>'
        ),
    }
    _, data, _, _, _ = QianjiParser.parse('tender.engineering', detail)
    assert data['开标时间'] == '2026-08-25 10:00:00'
    assert data['开启时间'] == ''


def test_qianji_compound_scope_label_does_not_fall_back_to_whole_section():
    detail = {
        'title': '测试项目招标公告',
        'content': enc(
            '<p>二、项目概况与招标范围</p>'
            '<p>1、项目名称：测试项目</p>'
            '<p>2、建设规模及内容：建设道路10公里</p>'
            '<p>5、招标内容及范围：本次招标为道路施工全部工作。</p>'
            '<p>三、投标人资格要求</p>'
        ),
    }
    _, data, _, _, _ = QianjiParser.parse('tender.engineering', detail)
    assert data['招标内容与范围'] == '本次招标为道路施工全部工作。'

def test_standard_project_and_tender_identifiers_are_separated():
    body='''<p>项目名称：测试工程</p><p>招标项目编号：I1400005446000339001</p><p>招标编号：QJ-2026-001</p>'''
    d={'id':'x','title':'测试工程招标公告','content':enc(body)}
    _,data,_,_,_=QianjiParser.parse('tender.engineering',d)
    assert data['项目编号']=='I1400005446000339001'
    assert data['招标编号']=='QJ-2026-001'
    assert data['项目编号/招标编号']=='I1400005446000339001；QJ-2026-001'
def test_feed_filtering():
    spider=QianjiSpider(categories='change,award',project_types='goods')
    assert spider.feeds==('change.goods','award.goods')

def test_qianji_datetime_funding_guarantee_and_agency_variants():
    text='''资金来源为上级补助及自筹。\n六、开标时间及地点\n开标时间：2026年8月25日10：00（北京时间）\n开标方式：线上开标\n七、提交投标保证金的形式\n本项目采用现金保证金或银行保函提交。\n八、联系方式\n招标人：甲单位\n地址：甲地址\n联系人：张三\n电话：111\n招标代理：乙公司\n地址：乙地址\n联系人：李四\n联系方式：222\n招标人或其招标代理机构主要负责人：签名\n招标人或其招标代理机构：（盖章）'''
    assert QianjiParser._datetime_value(QianjiParser._last_fuzzy_label(text,'开标时间'))=='2026-08-25 10:00:00'
    assert QianjiParser._funding_source(text)=='上级补助及自筹'
    assert '现金保证金' in QianjiParser._guarantee_method(text)
    contacts=QianjiParser._contacts_qianji(text)
    assert contacts['agency']=={'name':'乙公司','address':'乙地址','contact':'李四','phone':'222'}

def test_change_uses_last_changed_time():
    text='''递交截止时间：2026-08-20 09：30\n递交截止时间前，将电子版投标文件上传网站\n开标时间：2026-08-20 09：30\n现变更为：\n递交截止时间：2026-08-27 09：30\n电子版投标文件上传平台\n开标时间：2026-08-27 09：30'''
    assert QianjiParser._last_fuzzy_label(text,'开标时间').startswith('2026-08-27')
    assert QianjiParser._datetime_value(QianjiParser._last_exact_label(text,'递交截止时间'))=='2026-08-27 09:30:00'
    assert QianjiParser._online_submission(text)

def test_qianji_datetime_allows_spaces_around_chinese_units():
    assert QianjiParser._datetime_value('2026年08 月28 日10时00分') == '2026-08-28 10:00:00'
    assert QianjiParser._datetime_value('2026年8月17日上午9时00分') == '2026-08-17 09:00:00'
    assert QianjiParser._datetime_value('2026年8月17日下午2时30分') == '2026-08-17 14:30:00'
    assert QianjiParser._datetime_value('2026年7月30日9时') == '2026-07-30 09:00:00'
    assert QianjiParser._datetime_value('2026年7月30日9 时') == '2026-07-30 09:00:00'


def test_qianji_parses_hour_only_deadline_and_guarantee_section_variant():
    detail = {
        "title": "测试项目招标公告",
        "content": enc(
            "<p>4.1 递交的截止时间：2026年7月30日9 时；</p>"
            "<p>7 提交保证金的方式</p>"
            "<p>本项目可以采用银行转账、电子保函提交投标保证金。</p>"
            "<p>8 提出异议的渠道和方式</p>"
        ),
    }
    _, data, _, _, _ = QianjiParser.parse("tender.service", detail)
    assert data["递交截止时间"] == "2026-07-30 09:00:00"
    assert data["投标保证金方式"] == "银行转账、电子保函"


def test_qianji_submission_method_drops_address_and_dangling_template_word():
    detail = {
        "title": "测试项目招标公告",
        "content": enc(
            "<p>递交方法：截止前在网站上传电子版投标文件（加密）届时。"
            "递交地址：千极数采电子交易平台</p>"
        ),
    }
    _, data, _, _, _ = QianjiParser.parse("tender.service", detail)
    assert data["递交方法"] == "截止前在网站上传电子版投标文件（加密）"
    assert not candidate_matches_field(
        "递交方法",
        "截止前在网站上传电子版投标文件（加密）届时",
        ["递交方法：截止前在网站上传电子版投标文件（加密）届时。"],
    )

def test_detail_project_code_is_kept_when_body_has_tender_number():
    d={
        'id':'x',
        'title':'测试项目招标控制价',
        'projectCode':'I1400005446000341001',
        'content':enc('<p>招标编号：WGZB-2026-036</p><p>招标控制价总价：11058751.64元</p>'),
    }
    _,data,_,_,_=QianjiParser.parse('change.engineering',d)
    assert data['项目编号']=='I1400005446000341001'
    assert data['招标编号']=='WGZB-2026-036'
    assert data['招标金额']=='11058751.64元'
    assert data['源站公告性质']=='招标控制价公告'


def test_qianji_api_project_code_overrides_different_body_project_number():
    d = {
        'id': 'x',
        'title': '杨家坪村红白理事厅工程招标延期公告',
        'projectCode': 'I1400005446000340001',
        'content': enc('<p>（项目编号：DAHYGC-2026-019）</p>'),
    }
    _, data, _, _, _ = QianjiParser.parse('change.engineering', d)
    assert data['项目编号'] == 'I1400005446000340001'
    assert data['招标编号'] == 'DAHYGC-2026-019'


def test_qianji_api_project_code_wins_over_segment_and_investment_codes():
    d = {
        'id': 'x',
        'title': '大来药业中药丸剂车间建设项目三次招标公告',
        'projectCode': 'I1400005446000260001',
        'content': enc('''<p>（招标编号：ZXZX招-2026-051号-2）</p>
<p>招标项目编号：I1400005446000260001003</p>
<p>项目代码：2601-140521-89-05-367437</p>'''),
    }
    _, data, _, _, _ = QianjiParser.parse('tender.goods', d)
    assert data['项目编号'] == 'I1400005446000260001'
    assert data['招标编号'] == 'ZXZX招-2026-051号-2'


def test_qianji_plan_identifier_can_follow_label_on_next_line():
    d = {
        'id': 'p1',
        'title': '测试招标计划',
        'content': enc('<p>投资项目统一代码：</p><p>2603-140581-89-05-163265</p>'),
    }
    _, data, _, _, _ = QianjiParser.parse('plan.all', d)
    assert data['项目编号'] == '2603-140581-89-05-163265'
    assert data['招标编号'] == ''

def test_tender_contract_and_supervision_period_variants():
    for body, expected in (
        ('<p>合同履行期限：合同签订后50天内完成</p>', '合同签订后50天内完成'),
        ('<p>2.6 监理周期：同施工总工期（16个月）</p>', '同施工总工期（16个月）'),
    ):
        d={'id':'x','title':'测试招标公告','content':enc(body)}
        _,data,_,_,_=QianjiParser.parse('tender.service',d)
        assert data['工期/服务期/供货日期']==expected

def test_contact_blocks_do_not_mix_owner_supervision_and_agency():
    text='''四、监督部门\n电话：13753629288\n五、联系方式
招标人：陵川县住房和城乡建设管理局
地址：陵川县梅园西街240号
联系人：赵先生
电话：13935620820
招标代理：山西智行全过程工程咨询有限公司
地址：晋城市凤台东街131号
联系人：索先生
电话：13068050566
招标人或其招标代理机构主要负责人：（签名）'''
    contacts=QianjiParser._contacts_qianji(text)
    assert contacts['owner']=={
        'name':'陵川县住房和城乡建设管理局',
        'address':'陵川县梅园西街240号',
        'contact':'赵先生',
        'phone':'13935620820',
    }
    assert contacts['agency']=={
        'name':'山西智行全过程工程咨询有限公司',
        'address':'晋城市凤台东街131号',
        'contact':'索先生',
        'phone':'13068050566',
    }

def test_award_information_sections_and_spaced_winner_label():
    body='''<p>项目编号：DLXZBCG260073</p>
<p>一、中标人基本情况</p>
<p>中 标 人：吕梁焱坤建筑装饰有限公司</p>
<p>中标价格：2878165.00元</p>
<p>合同履行期限：签订合同后30日内供货安装完毕</p>
<p>四、联系方式</p>
<p>1、招标人信息</p>
<p>名 称：孝义市中阳楼文化旅游开发有限公司</p>
<p>地址：孝义市中阳楼街道办事处楼西村北</p>
<p>联系人：高先生</p>
<p>联系方式：13228031793</p>
<p>2、招标代理机构信息</p>
<p>采购代理机构：山西鼎立鑫项目管理有限公司</p>
<p>地址：太原市平阳路426号</p>
<p>联系人：王女士</p>
<p>电话：13834765801</p>'''
    d={'id':'x','title':'测试项目中标结果公示','content':enc(body)}
    _,data,_,_,_=QianjiParser.parse('award.engineering',d)
    assert data['中标人名称']==['吕梁焱坤建筑装饰有限公司']
    assert data['中标价']==['2878165.00元']
    assert data['工期']=='签订合同后30日内供货安装完毕'
    assert data['招标人/采购人']=='孝义市中阳楼文化旅游开发有限公司'
    assert data['招标代理机构']=='山西鼎立鑫项目管理有限公司'

def test_award_stacked_table_text_fallback():
    rows=QianjiParser._award_stacked_details(
        '一、中标人信息：\n中标人\n山西智华能源投资有限公司\n预计用电量\n总合计(元)\n9391499'
    )
    assert rows==[{
        '标段':'',
        '中标人名称':'山西智华能源投资有限公司',
        '中标价':'9391499元',
    }]


def test_qianji_candidate_table_preserves_composite_prices_without_fake_total():
    html = '''<table>
    <tr><th>排序</th><th>中标候选人名称</th><th>建安工程费 (元)</th><th>设计费 (元)</th></tr>
    <tr><td>1</td><td>甲建设公司</td><td>265234200</td><td>2780000</td></tr>
    <tr><td>2</td><td>乙建设公司</td><td>265453200</td><td>2791100</td></tr>
    </table>'''
    rows = QianjiParser._candidate_table_details(html)
    assert rows == [
        {
            '标段': '',
            '候选人名称': '甲建设公司',
            '候选人报价': '建安工程费 (元)：265234200；设计费 (元)：2780000',
        },
        {
            '标段': '',
            '候选人名称': '乙建设公司',
            '候选人报价': '建安工程费 (元)：265453200；设计费 (元)：2791100',
        },
    ]


def test_qianji_candidate_table_prefers_explicit_total_over_components():
    html = '''<table>
    <tr><th>排序</th><th>中标候选人名称</th><th>投标总价 (元)</th><th>分公司报价 (元)</th></tr>
    <tr><td>1</td><td>甲服务公司</td><td>9391499</td><td>100</td></tr>
    </table>'''
    assert QianjiParser._candidate_table_details(html)[0]['候选人报价'] == '9391499'


def test_qianji_candidate_table_keeps_all_names_when_price_is_not_published():
    html = '''<table>
    <tr><th>序号</th><th>中标候选人名称</th><th>排名</th></tr>
    <tr><td>1</td><td>甲银行股份有限公司支行</td><td>1</td></tr>
    <tr><td>2</td><td>乙银行股份有限公司分行</td><td>2</td></tr>
    </table>'''
    rows = QianjiParser._candidate_table_details(html)
    assert [item['候选人名称'] for item in rows] == [
        '甲银行股份有限公司支行',
        '乙银行股份有限公司分行',
    ]
    assert [item['候选人报价'] for item in rows] == ['', '']


def test_qianji_candidate_repeated_no_price_table_does_not_duplicate_names():
    html = '''
    <table>
      <tr><th>中标候选人名称</th><th>投标总价 (元)</th></tr>
      <tr><td>甲建设公司</td><td>1000000</td></tr>
    </table>
    <table>
      <tr><th>中标候选人名称</th><th>项目负责人</th></tr>
      <tr><td>甲建设公司</td><td>张三</td></tr>
    </table>'''
    assert QianjiParser._candidate_table_details(html) == [
        {'标段': '', '候选人名称': '甲建设公司', '候选人报价': '1000000'}
    ]


def test_qianji_award_table_accepts_unit_name_and_multiple_winners_without_price():
    html = '''<table>
    <tr><th>序号</th><th>单位名称</th><th>服务期限</th><th>交货期限</th></tr>
    <tr><td>1</td><td>甲建材有限公司</td><td>暂定2年</td><td>按订单送达</td></tr>
    <tr><td>2</td><td>乙建材有限公司</td><td>暂定2年</td><td>按订单送达</td></tr>
    </table>'''
    rows = QianjiParser._award_table_details(html)
    assert rows == [
        {'标段': '', '中标人名称': '甲建材有限公司', '中标价': ''},
        {'标段': '', '中标人名称': '乙建材有限公司', '中标价': ''},
    ]


def test_qianji_award_table_accepts_bidder_name_and_tax_inclusive_total():
    html = '''<table>
    <tr><th>序号</th><th>投标人名称</th><th>统一社会信用代码</th><th>含税总价(元)</th></tr>
    <tr><td>1</td><td>甲商贸有限公司</td><td>91140000TEST</td><td>3339150.00元</td></tr>
    </table>'''
    assert QianjiParser._award_table_details(html) == [
        {'标段': '', '中标人名称': '甲商贸有限公司', '中标价': '3339150.00元'}
    ]


def test_qianji_award_admitted_list_preserves_names_and_null_prices():
    text = '''一、入围单位信息：
001 银行服务项目：
中国工商银行股份有限公司甲支行
中国建设银行股份有限公司乙支行
二、其他公示内容：
无'''
    assert QianjiParser._award_admitted_details(text) == [
        {'标段': '', '中标人名称': '中国工商银行股份有限公司甲支行', '中标价': ''},
        {'标段': '', '中标人名称': '中国建设银行股份有限公司乙支行', '中标价': ''},
    ]


def test_qianji_result_correction_uses_correction_schema_without_fake_winner():
    detail = {
        'title': '测试项目中标结果公示更正',
        'content': enc(
            '<p>一、内容：</p><p>原监督部门有误，现变更为：测试监督局。</p>'
            '<p>二、其他公示内容：无</p>'
            '<p>三、监督部门</p><p>地址：测试路1号</p>'
            '<p>联系人：张先生</p><p>电话：0351-1234567</p>'
        ),
    }
    notice_type, data, _, _, _ = QianjiParser.parse('award.service', detail)
    assert notice_type == '更正结果公示'
    assert data['项目名称'] == '测试项目'
    assert '现变更为' in data['公告内容']
    assert data['监督部门联系方式'] == '0351-1234567'


def test_qianji_control_price_prefers_numeric_lowercase_amount():
    detail = {
        'title': '测试项目招标控制价',
        'content': enc(
            '<p>最高投标限价总价：大写：柒佰贰拾壹万肆仟柒佰伍拾元整</p>'
            '<p>小写：7214750元；</p>'
        ),
    }
    _, data, _, _, _ = QianjiParser.parse('change.goods', detail)
    assert data['招标金额'] == '7214750元'
    assert not candidate_matches_field(
        '招标金额',
        '柒佰贰拾壹万肆仟柒佰伍拾元整',
        ['最高投标限价总价：大写：柒佰贰拾壹万肆仟柒佰伍拾元整'],
    )


def test_qianji_ai_audit_keeps_api_project_code_and_finds_ambiguous_body_code():
    record = {
        "公告ID": "change-1",
        "公告标题": "测试项目招标延期公告",
        "公告类型": "TENDER",
        "公告子类型": "change.engineering",
        "项目编号": "DAHYGC-2026-019",
        "招标编号": "",
        "公告正文": "测试项目招标延期公告\n（项目编号：DAHYGC-2026-019）",
        "_trace": {"payload": {"detail": {"projectCode": "I1400005446000340001"}}},
    }
    audit = audit_identifiers(record)
    assert "PROJECT_NUMBER_DIFFERS_FROM_API_PROJECT_CODE" in audit.issues
    assert "TENDER_NUMBER_MISSING_WITH_AVAILABLE_CANDIDATE" in audit.issues
    assert audit.project_candidates == ["I1400005446000340001"]
    assert audit.tender_candidates == ["DAHYGC-2026-019"]
    assert audit.api_project_code_in_body is False
    assert audit.api_project_code_body_labels == []


def test_qianji_identifier_metadata_distinguishes_api_only_and_body_values():
    detail = {
        "projectCode": "I1400005446000162002",
        "content": enc("<p>三、招标编号</p><p>SXCY202604020</p>"),
    }
    _, data, _, _, text = QianjiParser.parse("change.service", detail)
    metadata = QianjiParser.identifier_source_metadata(detail, text)
    assert data["项目编号"] == "I1400005446000162002"
    assert data["招标编号"] == "SXCY202604020"
    assert metadata["projectNumber"] == {
        "value": "I1400005446000162002",
        "source": "detail_api.projectCode",
        "visibleInBody": False,
        "bodyLabels": [],
    }
    assert metadata["tenderNumber"]["source"] == "body_exact_label"
    assert metadata["tenderNumber"]["bodyLabels"] == ["招标编号"]


def test_qianji_ai_audit_does_not_double_match_project_label():
    evidence = extract_identifier_evidence(
        "招标项目编号：I1400005446000339001\n招标编号：QJ-2026-001"
    )
    assert [(item.label, item.value) for item in evidence] == [
        ("招标项目编号", "I1400005446000339001"),
        ("招标编号", "QJ-2026-001"),
    ]


def test_qianji_ai_model_result_must_respect_candidates_and_api_lock():
    record = {
        "公告ID": "candidate-1",
        "公告标题": "测试项目中标候选人公示",
        "公告类型": "CANDIDATE",
        "公告子类型": "candidate.engineering",
        "项目编号": "I1400005446000312001",
        "招标编号": "I1400005446000332001",
        "公告正文": (
            "（招标编号：I1400005446000332001）\n"
            "测试项目（招标项目编号：I1400005446000312001）"
        ),
        "_trace": {"payload": {"detail": {"projectCode": "I1400005446000312001"}}},
    }
    audit = audit_identifiers(record)
    valid = {
        "项目编号": {
            "value": "I1400005446000312001",
            "source": "DETAIL_API",
            "bodyVisible": True,
            "evidenceLines": [],
            "decision": "KEEP",
        },
        "招标编号": {
            "value": "I1400005446000332001",
            "source": "BODY",
            "bodyVisible": True,
            "evidenceLines": ["L0001"],
            "decision": "KEEP",
        },
        "warnings": ["SOURCE_IDENTIFIER_CONFLICT"],
    }
    assert validate_model_result(audit, valid) == []
    invalid = {
        **valid,
        "项目编号": {
            "value": "MODEL-CREATED-001",
            "evidenceLines": [],
            "decision": "REPLACE",
        },
    }
    assert "MODEL_PROJECT_NUMBER_VIOLATES_API_LOCK" in validate_model_result(
        audit, invalid
    )


def test_hybrid_ai_rejects_label_only_and_placeholder_identifiers():
    assert not candidate_matches_field(
        "项目编号", "项目编号：", ["项目编号："], "项目编号："
    )
    assert not candidate_matches_field(
        "项目编号", "（项目编号）", ["（项目编号）"], "（项目编号）"
    )
    assert not candidate_matches_field(
        "招标编号", "暂无", ["招标编号：暂无"], "招标编号：暂无"
    )
    assert candidate_matches_field(
        "项目编号", "SJZBZH02270226F070V31",
        ["项目编号：SJZBZH02270226F070V31"],
        "项目编号：SJZBZH02270226F070V31",
    )


def test_qianji_ai_baseline_does_not_send_stable_identifier_or_location_by_default():
    spider = QianjiSpider()
    selected = spider.select_ai_extract_fields(
        "招标公告",
        ["项目编号", "招标编号", "项目地点", "发布日期"],
        {},
    )
    assert selected == []


def test_qianji_ai_dynamically_escalates_missing_labeled_field_and_locks_api():
    spider = QianjiSpider()
    item = spider.build_notice_item(
        notice_type="招标公告",
        notice_subtype="tender.service",
        notice_id="field-policy-1",
        title="测试项目招标公告",
        data={
            "项目编号": "API-PROJECT-1",
            "质量要求": "",
            "招标人联系方式": "",
        },
        raw_text="质量要求：合格\n招标人联系方式：13800000000",
        field_meta={"qianjiApiTrustedFields": ["项目编号"]},
    )
    pipeline = QianjiHybridAiExtractionPipeline(
        config=AiExtractionConfig(enabled=True, api_key="fake"),
        service=None,
    )
    pipeline.crawler = SimpleNamespace(spider=spider)
    fields = pipeline._fields(ItemAdapter(item), "招标公告")
    assert "项目编号" not in fields
    assert "质量要求" in fields
    assert "招标人联系方式" in fields


def test_qianji_complete_qualification_section_does_not_use_ai_routinely():
    spider = QianjiSpider()
    item = spider.build_notice_item(
        notice_type="招标公告",
        notice_subtype="tender.service",
        notice_id="qualification-policy-1",
        title="测试项目招标公告",
        data={
            "申请人资格要求/投标人资格要求": (
                "3.1投标人须具备相应资质；\n"
                "3.2项目负责人须具备相应资格；\n"
                "3.3本项目不接受联合体投标。"
            )
        },
        raw_text=(
            "三、投标人资格要求\n"
            "3.1投标人须具备相应资质；\n"
            "3.2项目负责人须具备相应资格；\n"
            "3.3本项目不接受联合体投标。\n"
            "四、招标文件的获取\n登录平台下载。"
        ),
    )
    pipeline = QianjiHybridAiExtractionPipeline(
        config=AiExtractionConfig(enabled=True, api_key="fake"),
        service=None,
    )
    pipeline.crawler = SimpleNamespace(spider=spider)

    fields = pipeline._fields(ItemAdapter(item), "招标公告")

    assert "申请人资格要求/投标人资格要求" not in fields


def test_qianji_feed_type_is_not_written_as_industry_and_project_code_is_not_basis():
    detail = {
        "projectCode": "I1400005446000001001",
        "content": enc(
            "<p>招标编号：QJ-001</p><p>中标人：测试公司</p>"
            "<p>中标价格：100万元</p>"
        ),
    }
    _, candidate, _, _, _ = QianjiParser.parse("candidate.engineering", detail)
    _, award, _, _, _ = QianjiParser.parse("award.engineering", detail)
    assert candidate["所属行业"] == ""
    assert award["所属行业"] == ""
    assert award["依据文号"] == ""


def test_qianji_extracts_total_investment_amount_variant():
    detail = {
        "content": enc("<p>2.5总投资额：约26834.47万元（其中建安费26554万元）</p>"),
    }
    _, data, _, _, _ = QianjiParser.parse("tender.service", detail)
    assert data["项目总投资/估算金额"].startswith("约26834.47万元")


def test_qianji_tender_scope_prefers_exact_subfield_over_whole_overview_section():
    detail = {
        "content": enc(
            "<p>二、项目概况与招标范围</p>"
            "<p>2.1项目名称：测试项目</p>"
            "<p>2.2招标编号：QJ-001</p>"
            "<p>2.3项目规模：建设一栋厂房。</p>"
            "<p>2.4招标内容与范围：本项目施工阶段监理服务。</p>"
            "<p>2.5总投资额：1000万元。</p>"
            "<p>三、投标人资格要求</p><p>具备相应资质。</p>"
        )
    }

    _, data, _, _, _ = QianjiParser.parse("tender.service", detail)

    assert data["招标内容与范围"] == "本项目施工阶段监理服务。"
    assert "招标编号" not in data["招标内容与范围"]
    assert "总投资额" not in data["招标内容与范围"]


def test_qianji_hybrid_candidate_requires_verbatim_evidence():
    text = "2.2招标编号：SXYJS-2026-033"
    assert candidate_is_grounded(
        "SXYJS-2026-033", ["招标编号：SXYJS-2026-033"], text
    )
    assert not candidate_is_grounded("MODEL-001", ["招标编号：MODEL-001"], text)


def test_qianji_hybrid_datetime_formats_are_semantically_equal():
    assert semantically_equal(
        "招标计划",
        "招标公告（资格预审公告）预计发布时间",
        "2026-08-22 00:00",
        "20260822000000",
    )
    assert semantically_equal(
        "招标公告", "开启时间", "2026-08-28 10:00:00", "2026年08月28日10时00分"
    )


def test_qianji_hybrid_rejects_delivery_or_owner_address_as_project_location():
    assert not candidate_matches_field(
        "开启地点", "太原市A路", ["递交地址：太原市A路"]
    )
    assert not candidate_matches_field(
        "项目地点", "太原市B路", ["招标人地址：太原市B路"]
    )
    assert candidate_matches_field(
        "项目地点", "灵丘县农业科技园区", ["实施地点：灵丘县农业科技园区"]
    )
    assert candidate_matches_field(
        "项目地点", "山西省文水中学校", ["项目地点：山西省文水中学校"]
    )
    assert candidate_matches_field(
        "项目编号",
        "1401212026CCS00089",
        ["原公告的采购项目编号：1401212026CCS00089"],
    )
    assert not candidate_matches_field(
        "招标编号",
        "1401212026CCS00089",
        ["原公告的采购项目编号：1401212026CCS00089"],
    )


def test_qianji_submission_ai_candidate_must_keep_explicit_channel():
    evidence = [
        "递交方法：通过千极数采电子交易平台上传经CA加密的投标文件"
    ]
    assert not candidate_matches_field(
        "递交方法", "上传经CA加密的投标文件", evidence
    )
    assert candidate_matches_field(
        "递交方法",
        "通过千极数采电子交易平台上传经CA加密的投标文件",
        evidence,
    )
    assert not candidate_matches_field(
        "递交方法",
        "千极数采电子交易平台（https://www.qianjilink.com/）",
        ["递交地址：千极数采电子交易平台（https://www.qianjilink.com/）"],
    )
    assert not candidate_matches_field(
        "递交方法",
        "递交地址：千极数采电子交易平台（https://www.qianjilink.com/）",
        ["递交地址：千极数采电子交易平台（https://www.qianjilink.com/）"],
    )
    assert not candidate_matches_field(
        "递交方法",
        "递交地址：中招联合平台，逾期递交的或者未正常递交投标文件，平台不予受理。",
        ["递交地址：中招联合平台，逾期递交的或者未正常递交投标文件，平台不予受理。"],
    )
    assert not candidate_matches_field(
        "递交方法",
        "在网站上传电子版投标文件（加密）届时。递交地址：交易平台",
        ["递交方法：在网站上传电子版投标文件（加密）届时。递交地址：交易平台"],
    )
    assert not candidate_matches_field(
        "获取方式",
        "《山西招标采购服务平台》《千极数采电子交易平台》",
        ["我单位在《山西招标采购服务平台》《千极数采电子交易平台》发布了公告"],
    )
    assert not candidate_matches_field(
        "公示时间",
        "2026-06-11",
        ["公示开始时间：2026-06-11 公示结束时间：2026-06-14"],
        "公示开始时间：2026-06-11 公示结束时间：2026-06-14",
    )
    assert candidate_matches_field(
        "公示时间",
        "2026-06-11至2026-06-14",
        ["公示开始时间：2026-06-11 公示结束时间：2026-06-14"],
        "公示开始时间：2026-06-11 公示结束时间：2026-06-14",
    )


def test_qianji_hybrid_rejects_status_only_funding_and_header_only_duration():
    assert not candidate_matches_field(
        "资金来源",
        "资金来源已落实",
        ["现资金来源已落实。"],
    )
    assert candidate_matches_field(
        "资金来源",
        "企业自筹，资金已落实",
        ["资金来源为企业自筹，资金已落实。"],
    )
    assert not candidate_matches_field(
        "工期",
        "服务期限\n交货期限",
        ["服务期限\n交货期限"],
    )
    assert candidate_matches_field(
        "工期",
        "暂定2年，服务期满或采购总量完成，二者以先到者为准",
        ["服务期限：暂定2年，服务期满或采购总量完成，二者以先到者为准"],
    )


def test_hybrid_rejects_wrong_role_and_truncated_candidates_found_in_site_audit():
    assert not candidate_matches_field(
        "招标代理机构",
        "千极数采电子交易平台",
        ["招标代理机构：山西某项目管理有限公司\n电子平台：千极数采电子交易平台"],
    )
    assert not candidate_matches_field(
        "项目经理",
        "王国玺（签名）",
        ["招标代理机构项目经理：王国玺（签名）"],
    )
    assert not candidate_matches_field(
        "联合体成员",
        ["晋城市建工集团有限公司", "中国医药集团联合工程有限公司"],
        [
            "牵头人单位名称:晋城市建工集团有限公司\n"
            "联合体单位名称:中国医药集团联合工程有限公司"
        ],
    )
    assert candidate_matches_field(
        "联合体成员",
        ["中国医药集团联合工程有限公司"],
        ["联合体单位名称:中国医药集团联合工程有限公司"],
    )
    assert not candidate_matches_field(
        "建设内容及规模",
        "建设教学楼。招标公告（资格预审公告）预计发布时间：2026年08月",
        ["建设内容及规模：建设教学楼。招标公告（资格预审公告）预计发布时间：2026年08月"],
    )
    assert not candidate_matches_field(
        "资金来源", "市本级政府财政及各", ["资金来源：市本级政府财政及各"]
    )
    assert not candidate_matches_field(
        "投标保证金方式", "银行保函或非现金交易担保", ["投标保证金方式：银行保函或非现金交易担保"]
    )
    assert candidate_matches_field(
        "投标保证金方式",
        "银行保函或非现金交易担保方式",
        ["投标保证金方式：银行保函或非现金交易担保方式"],
    )
    assert not candidate_matches_field(
        "递交方法",
        "逾期递交的或者未正常递交投标文件，电子交易平台不予受理。",
        [
            "逾期递交的或者未正常递交投标文件，电子交易平台不予受理。\n"
            "递交地址：使用CA数字证书加密上传到电子交易平台。"
        ],
    )


def test_field_contract_trims_neighbor_fields_from_quality_and_scale():
    assert normalize_contract_value(
        "质量要求",
        "服务地点：和顺县。服务期：一年。质量标准：达到国家现行规范规定合格标准。",
    ) == "达到国家现行规范规定合格标准"
    assert normalize_contract_value(
        "质量要求",
        "项目规模：建设烟囱。质量要求：合格。设计要求的质量标准：符合现行规范。"
        "施工要求的质量标准：满足设计要求。建设地点：厂区。",
    ) == "合格。设计要求的质量标准：符合现行规范。施工要求的质量标准：满足设计要求"
    assert normalize_contract_value(
        "项目规模",
        "建设100MW储能电站及附属设施。项目总投资：35000万元。",
    ) == "建设100MW储能电站及附属设施"


def test_qianji_long_field_local_slice_drops_neighbor_and_compound_heading():
    assert QianjiHybridAiService._strip_long_field_heading(
        "建设内容及规模",
        "2.项目名称：测试道路工程\n3.建设规模：路线全长7.491km。",
    ) == "路线全长7.491km。"
    assert QianjiHybridAiService._strip_long_field_heading(
        "招标内容与范围",
        "2.3招标范围及内容：施工图设计、采购、施工全部工作。",
    ) == "施工图设计、采购、施工全部工作。"
    assert QianjiHybridAiService._strip_long_field_heading(
        "招标内容与范围",
        "001 不分标段：\n招标内容与范围：货物供应和售后服务。",
    ) == "001 不分标段：\n货物供应和售后服务。"
    assert QianjiHybridAiService._strip_long_field_heading(
        "项目规模",
        "二、项目概况与招标范围\n2.1项目规模：建设道路10公里。",
    ) == "建设道路10公里。"
    assert not candidate_matches_field(
        "项目规模",
        "该项目主要建设内容包括：",
        ["项目规模：该项目主要建设内容包括："],
    )


def test_qianji_file_time_does_not_capture_url_colon_after_time_phrase():
    detail = {
        "title": "测试项目招标公告",
        "content": enc(
            "<p>获取时间：2026年7月28日18时45分至2026年8月4日18时45分。</p>"
            "<p>获取方法：请在文件发售时间内通过平台（https://www.qianjilink.com）购买。</p>"
        ),
    }
    _, data, _, _, _ = QianjiParser.parse("tender.goods", detail)
    assert data["预审文件获取时间"] == (
        "2026年7月28日18时45分至2026年8月4日18时45分。"
    )
    assert not data["预审文件获取时间"].startswith("//")


def test_qianji_hybrid_does_not_replace_long_section_with_incomplete_sentence():
    assert not QianjiHybridAiExtractionPipeline._long_section_candidate_is_complete(
        "项目概况及完整招标范围" * 100,
        "仅一条招标内容",
    )
    assert QianjiHybridAiExtractionPipeline._long_section_candidate_is_complete(
        "",
        "正文明确提取出的项目规模",
    )


def test_qianji_hybrid_ai_can_replace_wrong_rule_after_conflict_verification():
    spider = QianjiSpider()
    item = spider.build_notice_item(
        notice_type="招标公告",
        notice_subtype="tender.service",
        notice_id="hybrid-1",
        title="测试项目招标公告",
        data={
            "项目名称": "测试项目",
            "项目编号": "I001",
            "招标编号": "WRONG-001",
            "招标金额": "100万元",
            "发布网站": config.PLATFORM_NAME,
        },
        raw_text="招标编号：RIGHT-001\n最高投标限价：200万元",
        field_meta={"qianjiApiTrustedFields": ["项目编号"]},
    )
    pipeline = QianjiHybridAiExtractionPipeline(
        config=AiExtractionConfig(enabled=True, api_key="fake"),
        service=None,
    )
    pipeline.crawler = SimpleNamespace(spider=spider)
    result = HybridReviewResult(
        requested_fields=["招标编号", "招标金额"],
        candidates={
            "招标编号": HybridCandidate(
                value="RIGHT-001",
                evidence=["招标编号：RIGHT-001"],
                confidence=0.99,
                grounded=True,
            ),
            "招标金额": HybridCandidate(
                value="200万元",
                evidence=["最高投标限价：200万元"],
                confidence=0.99,
                grounded=True,
            ),
        },
        conflict_decisions={"招标编号": "AI", "招标金额": "AI"},
        success=True,
        calls=2,
    )
    returned = pipeline._apply_result(result, item)
    adapter = ItemAdapter(returned)
    assert adapter["data"]["招标编号"] == "RIGHT-001"
    assert str(adapter["data"]["招标金额"]) == "2000000.00"
    assert adapter["data"]["项目编号"] == "I001"
    assert adapter["field_meta"]["qianjiHybridAi"]["replacedFields"] == [
        "招标编号",
        "招标金额",
    ]
    assert adapter["field_meta"]["qianjiHybridAi"]["ruleValues"] == {
        "招标编号": "WRONG-001",
        "招标金额": "1000000.00",
    }
    assert adapter["field_meta"]["qianjiHybridAi"]["finalValues"] == {
        "招标编号": "RIGHT-001",
        "招标金额": "2000000.00",
    }


def test_qianji_hybrid_ai_fills_blank_with_grounded_independent_candidate():
    spider = QianjiSpider()
    item = spider.build_notice_item(
        notice_type="招标公告",
        notice_subtype="tender.service",
        notice_id="hybrid-2",
        title="测试公告",
        data={"项目名称": "测试项目", "项目地点": ""},
        raw_text="项目地点：太原市测试路1号",
    )
    pipeline = QianjiHybridAiExtractionPipeline(
        config=AiExtractionConfig(enabled=True, api_key="fake"),
        service=None,
    )
    pipeline.crawler = SimpleNamespace(spider=spider)
    result = HybridReviewResult(
        requested_fields=["项目地点"],
        candidates={
            "项目地点": HybridCandidate(
                value="太原市测试路1号",
                evidence=["项目地点：太原市测试路1号"],
                confidence=0.99,
                grounded=True,
            )
        },
        conflict_decisions={},
        success=True,
        calls=1,
    )
    returned = pipeline._apply_result(result, item)
    adapter = ItemAdapter(returned)
    assert adapter["data"]["项目地点"] == "太原市测试路1号"
    assert adapter["field_meta"]["qianjiHybridAi"]["filledFields"] == ["项目地点"]
    assert adapter["field_meta"]["qianjiHybridAi"]["ruleValues"] == {"项目地点": ""}
    assert adapter["field_meta"]["qianjiHybridAi"]["finalValues"] == {
        "项目地点": "太原市测试路1号"
    }


def test_qianji_hybrid_ai_rejects_scope_section_inside_project_scale():
    text = (
        "项目规模：道路硬化、新建围墙。\n"
        "招标内容与范围：本项目工程量清单范围内的全部内容。"
    )
    assert not candidate_matches_field("项目规模", text, [text], text)


def test_qianji_sparse_ai_fields_only_review_fields_present_in_record():
    spider = QianjiSpider()
    item = spider.build_notice_item(
        notice_type="中标结果公示",
        notice_subtype="award.service",
        notice_id="hybrid-sparse-1",
        title="测试中标结果公示",
        data={"中标人名称": ["测试公司"], "中标价": ["100万元"]},
        raw_text="中标人：测试公司\n中标价：100万元\n服务期限：两年",
    )
    pipeline = QianjiHybridAiExtractionPipeline(
        config=AiExtractionConfig(enabled=True, api_key="fake"),
        service=None,
    )
    pipeline.crawler = SimpleNamespace(spider=spider)

    fields = pipeline._fields(ItemAdapter(item), "中标结果公示")

    assert fields == ["工期"]
    assert "项目经理" not in fields
    assert "依据文件" not in fields


def test_qianji_uses_site_hybrid_ai_pipeline_instead_of_fill_only_pipeline():
    settings = Settings()
    settings.setmodule(project_settings, priority="project")
    QianjiSpider.update_settings(settings)
    pipelines = settings.getdict("ITEM_PIPELINES")
    assert pipelines["crawler_scrapy.pipelines.AiHtmlExtractionPipeline"] is None
    assert pipelines[
        "crawler_scrapy.sites.qianji.hybrid_ai.QianjiHybridAiExtractionPipeline"
    ] == 200
    assert settings.get("NOTICE_AI_API_KEY_ENV") == "ZHIPUAI_API_KEY"
    assert settings.get("NOTICE_AI_BASE_URL") == (
        "https://open.bigmodel.cn/api/paas/v4"
    )
    assert settings.get("NOTICE_AI_MODEL") == "glm-5.2"
    assert settings.getbool("NOTICE_AI_JSON_MODE") is True
    assert settings.getbool("NOTICE_AI_ENABLE_THINKING") is False


def test_business_field_contract_normalizes_nature_and_preserves_exact_location():
    assert normalize_project_nature("依法项目") == "依法必须招标"
    assert normalize_project_nature("招标信息") == ""
    assert normalize_project_location("山西省晋城市陵川县平城镇") == "山西省晋城市陵川县平城镇"
    assert normalize_project_location("沁水县郑村镇肖庄村") == "沁水县郑村镇肖庄村"
    assert normalize_project_location("太原市小店区平阳路1号") == "太原市小店区平阳路1号"
    assert normalize_project_location("陵川县六泉乡下河村、佛山村") == "陵川县六泉乡下河村、佛山村"
    assert normalize_project_location("陵川县、沁水县") == "陵川县、沁水县"
    assert normalize_project_location("项目位于山西省太原市小店区平阳路") == (
        "山西省太原市小店区平阳路"
    )
    assert normalize_project_location("供货地点：山西省文水中学校") == "山西省文水中学校"


def test_candidate_windows_keep_offsets_and_do_not_send_full_notice():
    text = (
        "一、项目概况\n"
        + "背景说明\n" * 20
        + "资金来源：财政资金\n"
        + "其他概况\n" * 10
        + "二、招标范围\n"
        + "本次招标包含道路工程。\n"
        + "三、投标人资格要求\n"
        + "须具备相应资质。\n"
        + "无关尾部\n" * 40
    )
    windows = build_candidate_windows(
        text,
        ["资金来源", "招标内容与范围"],
        {},
    )
    assert windows
    assert sum(len(window.text) for window in windows) < len(text)
    assert all(text[window.start:window.end].strip() == window.text for window in windows)
    assert all(window.window_id.startswith("C") for window in windows)
    assert any("资金来源：财政资金" in window.text for window in windows)
    assert not any("无关尾部\n无关尾部\n无关尾部" in window.text for window in windows)


def test_candidate_windows_prefer_explicit_scope_label_over_broad_heading():
    text = (
        "二、项目概况和招标范围\n"
        "2.1建设规模：建设道路十公里。\n"
        "2.2建设地址：太原市。\n"
        "2.3招标范围：本项目划分为一个标段：\n"
        "标段内容：工程量清单内全部内容。\n"
        "三、投标人资格要求\n"
        "具备相应资质。"
    )

    windows = build_candidate_windows(
        text,
        ["招标内容与范围"],
        {"招标内容与范围": "本项目划分为一个标段"},
        stage="expanded",
    )

    assert len(windows) == 1
    assert "2.3招标范围" in windows[0].text
    assert "标段内容：工程量清单内全部内容" in windows[0].text
    assert "2.1建设规模" not in windows[0].text


def test_long_section_window_keeps_numbered_subclauses_until_peer_field():
    text = (
        "二、项目概况与招标范围\n"
        "2.1项目规模：主要建设内容包括：\n"
        "1、35kV变电站改造；\n"
        "2、10kV线路改造。\n"
        "2.2招标内容与范围：施工监理。\n"
        "三、投标人资格要求\n"
        "3.1投标人须具备相应资质；\n"
        "3.2项目负责人须具备相应资格。\n"
        "四、招标文件的获取\n登录平台下载。"
    )

    scale = build_candidate_windows(text, ["项目规模"], {}, stage="expanded")
    qualification = build_candidate_windows(
        text,
        ["申请人资格要求/投标人资格要求"],
        {},
        stage="expanded",
    )

    assert len(scale) == 1
    assert "1、35kV变电站改造" in scale[0].text
    assert "2、10kV线路改造" in scale[0].text
    assert "2.2招标内容与范围" not in scale[0].text
    assert len(qualification) == 1
    assert "3.1投标人须具备相应资质" in qualification[0].text
    assert "3.2项目负责人须具备相应资格" in qualification[0].text
    assert "四、招标文件的获取" not in qualification[0].text


def test_qianji_c_strategy_expands_conflict_to_section_before_replacement():
    requests = []
    payloads = [
        {
            "fields": {
                "资金来源": {
                    "value": "财政资金",
                    "evidence": ["资金来源：财政资金"],
                    "window_id": "C001",
                    "confidence": 0.99,
                }
            }
        },
        {
            "fields": {
                "资金来源": {
                    "value": "财政资金",
                    "evidence": ["资金来源：财政资金"],
                    "window_id": "E001",
                    "confidence": 0.99,
                }
            }
        },
    ]

    class FakeCompletions:
        def create(self, **kwargs):
            requests.append(kwargs)
            payload = payloads.pop(0)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(payload, ensure_ascii=False)
                        )
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=30,
                    total_tokens=130,
                ),
            )

    fake = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    service = QianjiHybridAiService(
        AiExtractionConfig(
            enabled=True,
            api_key="fake",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-5.2",
            min_interval_seconds=0,
            retry_times=0,
            json_mode=True,
            enable_thinking=False,
        ),
        client=client,
    )
    text = (
        "一、项目概况\n"
        "项目名称：测试项目\n"
        "资金来源：财政资金\n"
        "招标人：甲公司\n"
        "二、招标范围\n"
        "道路改造。\n"
        "三、资格要求\n"
        "具备相应资质。\n"
        + "文末无关内容\n" * 60
    )
    result = service.review(
        notice_type="招标公告",
        title="测试项目招标公告",
        fields=["资金来源"],
        text=text,
        rule_data={"资金来源": "财政资金，招标人为甲公司"},
    )
    assert result.success
    assert result.calls == 2
    assert result.expanded_fields == ["资金来源"]
    assert result.candidates["资金来源"].stage == "expanded"
    assert result.conflict_decisions["资金来源"] == "AI"
    assert any(window["mode"] == "SECTION" for window in result.candidate_windows)
    assert "文末无关内容" not in requests[0]["messages"][1]["content"]
    assert "<公告原文>" not in requests[0]["messages"][1]["content"]
    assert requests[0]["model"] == "glm-5.2"
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["extra_body"] == {
        "thinking": {"type": "disabled"},
        "do_sample": False,
    }
    assert "temperature" not in requests[0]


def test_qianji_c2_prompt_uses_typed_contracts_and_line_spans():
    service = QianjiHybridAiService(AiExtractionConfig(), client=object())
    text = "资金来源：财政资金\n招标人：甲公司"
    windows = build_candidate_windows(text, ["资金来源"], {})

    messages = service._extract_messages(
        "招标公告", "测试公告", ["资金来源"], windows, "candidate"
    )
    prompt = messages[1]["content"]

    assert "L001|资金来源：财政资金" in prompt
    assert '"type":"string"' in prompt
    assert "line_start" in prompt and "line_end" in prompt
    assert '"confidence"' not in prompt
    assert '"evidence"' not in prompt


def test_qwen_c2_prompt_and_schema_use_nullable_evidence_candidates():
    service = QianjiHybridAiService(
        AiExtractionConfig(
            base_url="https://api.siliconflow.cn/v1",
            model="Qwen/Qwen3-8B",
            response_format="json_schema",
            enable_thinking=False,
        ),
        client=object(),
    )
    text = "资金来源：财政资金\n招标人：甲公司"
    windows = build_candidate_windows(text, ["资金来源"], {})
    messages = service._extract_messages(
        "招标公告", "测试公告", ["资金来源"], windows, "candidate"
    )
    response_format = service._response_format(
        ["资金来源"], wrapped_fields=True
    )

    assert "找不到证据时该字段整体返回null" in messages[0]["content"]
    assert '绝对不能返回字符串"null"' in messages[0]["content"]
    assert "long_text使用null" in messages[1]["content"]
    assert response_format is not None
    assert response_format["type"] == "json_schema"
    fields_schema = response_format["json_schema"]["schema"]["properties"]["fields"]
    assert fields_schema["required"] == ["资金来源"]
    candidate = fields_schema["properties"]["资金来源"]
    assert {value["type"] for value in candidate["anyOf"]} == {"object", "null"}


def test_qwen_c2_review_uses_schema_and_keeps_grounded_value():
    requests = []

    class FakeCompletions:
        def create(self, **kwargs):
            requests.append(kwargs)
            payload = {
                "fields": {
                    "资金来源": {
                        "window_id": "C001",
                        "line_start": "L001",
                        "line_end": "L001",
                        "value": "财政资金",
                    }
                }
            }
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(payload, ensure_ascii=False)
                    )
                )],
                usage=SimpleNamespace(
                    prompt_tokens=80, completion_tokens=20, total_tokens=100
                ),
            )

    service = QianjiHybridAiService(
        AiExtractionConfig(
            enabled=True,
            api_key="fake",
            base_url="https://api.siliconflow.cn/v1",
            model="Qwen/Qwen3-8B",
            min_interval_seconds=0,
            retry_times=0,
            json_mode=True,
            response_format="json_schema",
            enable_thinking=False,
        ),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )

    result = service.review(
        notice_type="招标公告",
        title="测试公告",
        fields=["资金来源"],
        text="资金来源：财政资金\n招标人：甲公司",
        rule_data={"资金来源": ""},
    )

    assert result.success and result.calls == 1
    assert result.candidates["资金来源"].value == "财政资金"
    assert result.candidates["资金来源"].grounded
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert requests[0]["extra_body"]["enable_thinking"] is False


def test_qwen_string_null_is_treated_as_missing_not_as_business_value():
    class FakeCompletions:
        def create(self, **_kwargs):
            payload = {
                "fields": {
                    "资金来源": {
                        "window_id": "C001",
                        "line_start": "L001",
                        "line_end": "L001",
                        "value": "null",
                    }
                }
            }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps(payload, ensure_ascii=False)
                ))],
                usage=SimpleNamespace(
                    prompt_tokens=30, completion_tokens=10, total_tokens=40
                ),
            )

    service = QianjiHybridAiService(
        AiExtractionConfig(
            enabled=True,
            api_key="fake",
            base_url="https://api.siliconflow.cn/v1",
            model="Qwen/Qwen3-8B",
            min_interval_seconds=0,
            retry_times=0,
            response_format="json_schema",
            enable_thinking=False,
        ),
        client=SimpleNamespace(chat=SimpleNamespace(
            completions=FakeCompletions()
        )),
    )

    result = service.review(
        notice_type="招标公告",
        title="测试公告",
        fields=["资金来源"],
        text="资金来源：财政资金",
        rule_data={"资金来源": "财政资金"},
    )

    assert result.success
    assert result.candidates["资金来源"].value is None
    assert "资金来源" not in result.conflict_decisions


def test_qianji_c2_long_field_is_sliced_from_source_instead_of_generated():
    payload = {
        "fields": {
            "招标内容与范围": {
                "window_id": "C001",
                "line_start": "L001",
                "line_end": "L003",
            }
        }
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(
                    prompt_tokens=80,
                    completion_tokens=15,
                    total_tokens=95,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=20),
                ),
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    service = QianjiHybridAiService(
        AiExtractionConfig(
            enabled=True,
            api_key="fake",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-5.2",
            min_interval_seconds=0,
            retry_times=0,
            json_mode=True,
            enable_thinking=False,
        ),
        client=client,
    )
    result = service.review(
        notice_type="招标公告",
        title="测试公告",
        fields=["招标内容与范围"],
        text=(
            "二、招标内容与范围\n"
            "本次招标包含道路工程。\n"
            "包括施工、验收和移交。\n"
            "三、投标人资格要求\n须具备相应资质。"
        ),
        rule_data={"招标内容与范围": ""},
    )

    candidate = result.candidates["招标内容与范围"]
    assert result.success and result.calls == 1
    assert candidate.value == "本次招标包含道路工程。\n包括施工、验收和移交。"
    assert candidate.grounded
    assert candidate.confidence == 1.0
    assert candidate.evidence_spans
    assert result.cached_prompt_tokens == 20


def test_qianji_c2_does_not_replace_rule_when_two_ai_stages_disagree():
    payloads = [
        {
            "fields": {
                "资金来源": {
                    "value": "财政资金",
                    "evidence": ["资金来源：财政资金"],
                    "window_id": "C001",
                    "confidence": 0.99,
                }
            }
        },
        {
            "fields": {
                "资金来源": {
                    "value": "企业自筹",
                    "evidence": ["资金来源：企业自筹"],
                    "window_id": "E001",
                    "confidence": 0.99,
                }
            }
        },
    ]

    class FakeCompletions:
        def create(self, **_kwargs):
            payload = payloads.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            )

    service = QianjiHybridAiService(
        AiExtractionConfig(
            enabled=True,
            api_key="fake",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-5.2",
            min_interval_seconds=0,
            retry_times=0,
            json_mode=True,
            enable_thinking=False,
        ),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )
    result = service.review(
        notice_type="招标公告",
        title="测试公告",
        fields=["资金来源"],
        text="资金来源：财政资金\n资金来源：企业自筹\n招标人：甲公司",
        rule_data={"资金来源": "财政资金，招标人为甲公司"},
    )

    assert result.calls == 2
    assert result.conflict_decisions["资金来源"] == "RULE"
    assert "资金来源" not in result.cross_stage_agreements


def test_all_eight_notice_schema_fields_have_explicit_ai_contracts():
    all_fields = set().union(*ANNOUNCEMENT_SCHEMAS.values())
    assert all(field in FIELD_DEFINITIONS for field in all_fields)
    assert all(
        field in FIELD_LABELS or get_field_contract(field).ai_policy == "DIRECT"
        for field in all_fields
    )
    assert get_field_contract("项目总投资").value_type == "rmb_amount"
    assert get_field_contract("发布日期").value_type == "datetime"
    assert get_field_contract("中标候选人名称").value_type == "string_list"
    assert get_field_contract("中标候选人报价").value_type == "amount_list"
    assert get_field_contract("招标内容与范围").value_type == "long_text"


def test_qianji_ai_authentication_failure_opens_process_circuit_breaker():
    class FakeAuthenticationError(RuntimeError):
        status_code = 401

    class FakeCompletions:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            raise FakeAuthenticationError("invalid token")

    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = QianjiHybridAiService(
        AiExtractionConfig(
            enabled=True,
            api_key="fake",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-5.2",
            min_interval_seconds=0,
            retry_times=2,
            json_mode=True,
            enable_thinking=False,
        ),
        client=client,
    )
    kwargs = {
        "notice_type": "招标公告",
        "title": "测试公告",
        "fields": ["资金来源"],
        "text": "资金来源：企业自筹",
        "rule_data": {"资金来源": "企业自筹"},
    }

    first = service.review(**kwargs)
    second = service.review(**kwargs)

    assert not first.success and not second.success
    assert completions.calls == 1
    assert service.call_count == 1
    assert "停止调用" in second.error
