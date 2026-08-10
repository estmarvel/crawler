from __future__ import annotations
import base64
from crawler_scrapy.sites.qianji import config
from crawler_scrapy.sites.qianji.parser import QianjiParser
from crawler_scrapy.spiders.qianji import QianjiSpider

def enc(s): return base64.b64encode(s.encode()).decode()
def test_all_public_feeds_are_configured():
    assert len(config.FEEDS)==13
    assert config.FEEDS['change.goods'][1]=='货物'
    assert 'pageNum=2' in config.list_url('award.service',2,50)
def test_base64_plan_mapping():
    d={'id':'p1','title':'项目','noticeStartTime':'2026-08-01 10:00','content':enc('<table><tr><td>项目名称：</td><td>测试项目</td></tr><tr><td>项目总投资（万元）：</td><td>440</td></tr><tr><td>招标方式：</td><td>公开招标</td></tr></table>')}
    typ,data,_,html,text=QianjiParser.parse('plan.all',d)
    assert typ=='招标计划' and data['项目名称']=='测试项目' and data['招标方式']=='公开招标'
    assert '<table>' in html and '440' in text
def test_tender_uses_site_metadata():
    d={'id':'x','title':'道路工程招标公告','projectCode':'I001','bidSituation':'依法招标','bidTypeName':'公开招标','zbUnitName':'甲单位','dlUnitName':'乙代理','noticeStartTime':'2026-08-04 12:00','content':enc('<p>项目所在地：山西省晋城市</p><p>项目规模：道路改造</p>')}
    _,data,_,_,_=QianjiParser.parse('tender.engineering',d)
    assert data['项目编号/招标编号']=='I001' and data['项目类型/行业分类']=='工程'
    assert data['招标人/采购人名称']=='甲单位' and data['招标代理机构']=='乙代理'

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
