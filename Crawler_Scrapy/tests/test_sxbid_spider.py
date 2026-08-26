import hashlib
from types import SimpleNamespace

from scrapy.settings import Settings

from crawler_scrapy.sites.sxbid import config
from crawler_scrapy.sites.sxbid.exporter import SxbidMultiFormatPipeline
from crawler_scrapy.sites.sxbid.parser import (
    ParsedPage,
    SxbidParser,
    parse_detail_page,
    parse_list_records,
    parse_page_info,
)
from crawler_scrapy.spiders.sxbid import SxbidSpider


def _detail(title: str, body: str, *, iframe: str = "", heading: str = "招标公告") -> str:
    return f"""<html><body><div class='bg_panel'><div class='bid_title'>{heading}</div>
    <div class='page_panel noticeInfoDiv'><div class='page_name'>{title}</div>
    <div class='page_msg'><span>发布日期：2026-08-06</span>
    <span>来源：全国公共资源交易平台(山西省)</span><span>浏览次数：10</span></div>
    <table><tr><td><b>实施地：</b></td><td>太原市</td>
    <td><b>所属行业：</b></td><td>土木工程建筑业</td></tr>
    <tr><td><b>招标组织形式：</b></td><td>委托招标</td>
    <td><b>开标时间：</b></td><td>2026-08-20</td></tr></table>
    <div class='page_content'>{body}{iframe}</div></div></div></body></html>"""


def test_list_records_and_page_info_are_parsed():
    html = """<table class='content_table'><tbody><tr>
    <td class='text_left'><span>[太原市]</span>
    <a href='/f/new/notice/1/abc123' title='道路施工招标公告'>道路施工招标公告</a></td>
    <td class='text_center'>工程</td><td class='text_center'>2026-08-06</td>
    <td class='text_center'>20</td></tr></tbody></table>
    <div class='list_pages'>共952页 95123条记录</div>"""
    record = parse_list_records(html)[0]
    assert record.notice_id == "abc123"
    assert record.path_type == "1"
    assert record.region == "太原市"
    assert record.project_type == "工程"
    assert record.detail_url == "https://www.sxbid.com.cn/f/new/notice/1/abc123"
    assert parse_page_info(html) == (952, 95123)


def test_plan_structured_fields_and_public_attachment_are_parsed():
    html = """<html><body><div class='page_panel noticeInfoDiv'>
    <div class='page_name'>污水处理项目</div><div class='page_msg'>发布日期：2026-08-06 来源：本站 浏览次数：2</div>
    <div class='page_content'><table class='bid_msgTable'>
    <tr><td><b>投资项目统一代码：</b></td><td>2601-140100-89-01-1</td></tr>
    <tr><td><b>项目名称：</b></td><td>污水处理项目</td></tr>
    <tr><td><b>项目总投资：</b></td><td>1000（万元）</td></tr>
    <tr><td><b>招标人名称：</b></td><td>甲公司</td></tr>
    </table></div></div>
    <div class='bg_panel margin_top'><div class='bid_title'>附件下载</div>
    <a href='/downloadByFileName?fname=file-1&amp;type=1&amp;originName=1' title='批复.pdf'>批复.pdf</a>
    </div></body></html>"""
    page = parse_detail_page(html)
    parsed = SxbidParser.parse("plan", page)
    assert parsed.data["项目编号"] == "2601-140100-89-01-1"
    assert parsed.data["项目总投资"] == "1000（万元）"
    assert parsed.data["招标人名称"] == "甲公司"
    assert parsed.attachments[0]["source_file_id"] == "file-1"
    assert parsed.attachments[0]["file_name"] == "批复.pdf"


def test_pdf_iframe_and_prequalification_fields_are_parsed():
    html = _detail(
        "道路工程资格预审公告",
        "",
        iframe=(
            "<iframe src='/static/pdfjs/web/viewer.html?rdm=3&amp;"
            "file=/f/downloadByFileName%3Ftype%3D3%26fname%3Dbody-1'></iframe>"
        ),
        heading="资格预审公告",
    )
    page = parse_detail_page(html)
    text = """道路工程资格预审公告
    （招标编号：E140100001）
    招标项目编号：E140100001
    项目资金来源为财政资金
    二、项目概况与招标范围
    项目规模：建设道路10公里。
    三、申请人资格要求
    具备市政施工资质。
    四、资格预审文件的获取
    获取时间：2026-08-06 20:00至2026-08-10 20:00
    获取方式：网上获取
    五、资格预审申请文件的递交
    递交截止时间：2026-08-20 09:30:00
    递交方法：线上递交
    六、资格预审申请文件开启
    文件开启时间：2026-08-20 09:30:00
    文件开启方式：线上开启
    评审办法：有限数量制
    十一、联系方式
    招标人：甲公司
    地址：甲地址
    联系人：张三
    联系电话：0351-1234567
    招标代理机构：乙公司
    地址：乙地址
    联系人：李四
    联系电话：13800000000"""
    parsed = SxbidParser.parse("prequalification", page, pdf_text=text)
    assert page.body_pdf_url.endswith("type=3&fname=body-1")
    assert parsed.data["项目编号"] == "E140100001"
    assert parsed.data["招标编号"] == "E140100001"
    assert parsed.data["资金来源"] == "财政资金"
    assert parsed.data["递交截止时间"] == "2026-08-20 09:30:00"
    assert parsed.data["招标人/采购人名称"] == "甲公司"


def test_candidate_award_correction_and_contract_are_mapped_to_own_schemas():
    candidate_page = parse_detail_page(_detail("道路项目中标候选人公示", ""))
    candidate = SxbidParser.parse(
        "candidate",
        candidate_page,
        pdf_text="""招标编号：E1
        中标候选人基本情况
        1 甲建设有限公司 1000万元 合格
        2 乙建设有限公司 900万元 合格
        提出异议的渠道和方式""",
    )
    assert candidate.data["中标候选人名称"] == ["甲建设有限公司", "乙建设有限公司"]
    assert candidate.data["中标候选人报价"] == ["1000万元", "900万元"]

    award_page = parse_detail_page(_detail("道路项目中标结果公示", ""))
    award = SxbidParser.parse(
        "award", award_page,
        pdf_text="中标人：甲建设有限公司 中标价格：1000万元",
    )
    assert award.data["中标人名称"] == ["甲建设有限公司"]
    assert award.data["中标价"] == ["1000万元"]

    correction_page = parse_detail_page(_detail("道路项目变更公告", ""))
    correction = SxbidParser.parse(
        "correction", correction_page,
        pdf_text="原开标时间：2026-08-10\n现开标时间：2026-08-20",
    )
    assert correction.notice_type == "更正结果公示"
    assert "现开标时间" in correction.data["公告内容"]

    contract_html = """<html><body><div class='page_panel noticeInfoDiv'>
    <div class='page_name'>道路工程施工合同</div><div class='page_msg'>发布日期：2026-08-06</div>
    <div class='page_content'><table><tr><td><b>项目编号：</b></td><td>P-1</td>
    <td><b>项目名称：</b></td><td>道路工程</td></tr>
    <tr><td><b>合同名称：</b></td><td>道路工程施工合同</td></tr>
    <tr><td><b>招标人名称：</b></td><td>甲公司</td>
    <td><b>中标人名称：</b></td><td>乙公司</td></tr>
    <tr><td><b>合同金额（万元）：</b></td><td>100</td>
    <td><b>合同期限（年）：</b></td><td>2</td></tr>
    <tr><td><b>合同签署时间：</b></td><td>2026-08-05</td></tr>
    </table></div></div></body></html>"""
    contract = SxbidParser.parse("contract", parse_detail_page(contract_html))
    assert contract.data["项目编号"] == "P-1"
    assert contract.data["合同金额"] == "100万元"
    assert contract.data["中标人名称"] == ["乙公司"]


def test_spider_and_exporter_cover_all_eight_categories():
    spider = SxbidSpider(categories="plan,tender,contract")
    assert spider.categories == ("plan", "tender", "contract")
    assert len(SxbidMultiFormatPipeline.ROUTES) == 8
    assert SxbidMultiFormatPipeline.ROUTES["final_candidate"][1] == "定标候选人公示"
    assert config.list_form(2, 500)["pageSize"] == "100"


def test_sxbid_spaced_identifier_and_final_candidate_pdf_tables():
    page = parse_detail_page(_detail("绿化工程定标候选人公示", ""))
    text = """绿化工程定标候选人公示
    (招标编号：ZLZX 招【2022】1097 号)
    绿化工程（招标项目编号：E1401005031300573002），经评审
    一、评标情况
    1、定标候选人基本情况
    定标候选人 投标报价
    甲园林建设工程有限公司              6654669.43（元）
    乙园林绿化有限责任公司              6560757.67（元）
    2、定标候选人按照招标文件要求承诺的项目负责人情况
    3、定标候选人响应招标文件要求的资格能力条件
    定标候选人 响应情况
    甲园林建设工程有限公司              响应招标文件
    乙园林绿化有限责任公司              响应招标文件
    二、提出异议的渠道和方式"""
    parsed = SxbidParser.parse("final_candidate", page, pdf_text=text)
    assert parsed.data["项目编号"] == "E1401005031300573002"
    assert parsed.data["招标编号"] == "ZLZX招【2022】1097号"
    assert parsed.data["定标候选人名称"] == [
        "甲园林建设工程有限公司",
        "乙园林绿化有限责任公司",
    ]
    assert parsed.data["定标候选人报价"] == [
        "6654669.43（元）",
        "6560757.67（元）",
    ]


def test_sxbid_final_candidate_raw_multiline_table_is_reassembled():
    page = parse_detail_page(_detail("EPC定标候选人公示", ""))
    text = """EPC定标候选人公示
    招标编号：hp2306ffxx66
    招标项目编号：E1401005031300731001
    一、评标情况
    1、定标候选人基本情况
    排序
    定标候选人 投标报价
    1
    （牵头人）甲建设集团有限公司（成员单位）甲设计研究院有限公司联
    合体
    4242741000（元）
    合格
    270
    2
    （牵头人）乙建设集团有限公司（成员单位）乙设计研究院有限公司联合体
    4460960500（元）
    合格
    270
    2、定标候选人按照招标文件要求承诺的项目负责人情况"""
    parsed = SxbidParser.parse("final_candidate", page, pdf_text=text)
    assert parsed.data["定标候选人名称"] == [
        "（牵头人）甲建设集团有限公司（成员单位）甲设计研究院有限公司联合体",
        "（牵头人）乙建设集团有限公司（成员单位）乙设计研究院有限公司联合体",
    ]
    assert parsed.data["定标候选人报价"] == [
        "4242741000（元）",
        "4460960500（元）",
    ]


def test_sxbid_tender_real_world_labels_contacts_and_funding_are_parsed():
    page = parse_detail_page(_detail("道路工程重新招标公告", ""))
    text = """道路工程重新招标公告
    招标项目编号：E140100001
    项目资金来源为自有资金，招标人
    为甲建设有限公司。本项目已具备招标条件。
    二、项目概况和招标范围
    项目规模：道路全长10公里。
    招标内容与范围：施工图纸内全部工程。
    三、投标人资格要求
    具备施工资质。
    四、招标文件的获取
    获取方法：登录交易平台免费下载。
    五、投标文件的递交
    投标文件递交截止时间：2026-08-31 09:00
    十一、联系方式
    招 标 人：甲建设有限公司
    地 址：甲地址有限公
    司
    联 系 人：张三
    电 话：0351-1234567
    代理机构：乙代理有限公司
    地 址：乙地址有限公
    司
    联 系 人：李四
    电 话：13800000000
    招标人或招标代理机构主要负责人：（签章）"""
    parsed = SxbidParser.parse("tender", page, pdf_text=text)
    assert parsed.data["项目名称"] == "道路工程"
    assert parsed.data["资金来源"] == "自有资金"
    assert parsed.data["获取方式"] == "登录交易平台免费下载。"
    assert parsed.data["递交截止时间"] == "2026-08-31 09:00"
    assert parsed.data["招标人地址"] == "甲地址有限公司"
    assert parsed.data["招标人联系方式"] == "0351-1234567"
    assert parsed.data["招标代理机构"] == "乙代理有限公司"
    assert parsed.data["招标代理机构地址"] == "乙地址有限公司"
    assert parsed.data["招标代理机构联系方式"] == "13800000000"


def test_sxbid_prequalification_scope_and_get_method_aliases_are_parsed():
    page = parse_detail_page(_detail("医院工程资格预审公告", ""))
    text = """医院工程资格预审公告
    招标项目编号：E140100002
    二、项目概况和招标范围
    项目规模：装饰装修工程。
    招标内容与范围：施工图纸范围内全部工程。
    三、申请人资格要求
    具备建筑装修资质。
    四、资格预审文件的获取
    获取时间：2026-08-01 09:00 -- 2026-08-06 09:00
    获取方法：网上免费下载。
    五、资格预审申请文件的递交"""
    parsed = SxbidParser.parse("prequalification", page, pdf_text=text)
    assert "装饰装修工程" in parsed.data["项目概况与招标范围"]
    assert "施工图纸范围内全部工程" in parsed.data["项目概况与招标范围"]
    assert parsed.data["获取方式"] == "网上免费下载。"


def test_sxbid_wrapped_candidate_name_and_publicity_period_are_recovered():
    page = parse_detail_page(_detail("种芯项目施工中标候选人公示", ""))
    text = """种芯项目施工中标候选人公示
    公示期：2026年08月06日至2026年08月09日
    一、评标情况
    1、中标候选人基本情况
    排序 中标候选人名称 投标报价（元） 质量 工期
    中甲建业（山西）建设工程
    1                       8360298.80      合格 240日历天
    有限公司
    2 太原益珉建筑工程有限公司 8396434.29 合格 240日历天
    2、中标候选人按照招标文件要求承诺的项目经理情况"""
    parsed = SxbidParser.parse("candidate", page, pdf_text=text)
    assert parsed.data["公示时间"] == "2026年08月06日至2026年08月09日"
    assert parsed.data["中标候选人名称"] == [
        "中甲建业（山西）建设工程有限公司",
        "太原益珉建筑工程有限公司",
    ]
    assert parsed.data["中标候选人报价"] == ["8360298.80元", "8396434.29元"]


def test_sxbid_award_sentence_consortium_and_unit_price_are_parsed():
    page = parse_detail_page(_detail("垃圾焚烧项目中标结果公示", ""))
    text = """垃圾焚烧项目中标结果公示
    项目名称：垃圾焚烧项目特许经营者招标
    项目编号：GC141000202600192001
    招标人确定北京中科润宇环保科技股份有限公司、中科环保科
    技（香港）国际有限公司（联合体）为本项目的中标人，现予以公示：
    中标金额：131.00元/吨（生活垃圾处理服务费初始单价）
    特许经营期：40年（含前期及建设期）
    项目负责人：孙宏佐"""
    parsed = SxbidParser.parse("award", page, pdf_text=text)
    assert parsed.data["项目名称"] == "垃圾焚烧项目特许经营者招标"
    assert parsed.data["中标人名称"] == ["北京中科润宇环保科技股份有限公司"]
    assert parsed.data["联合体成员"] == ["中科环保科技（香港）国际有限公司"]
    assert parsed.data["中标价"] == ["131.00元/吨"]
    assert parsed.data["工期"] == "40年（含前期及建设期）"


def test_sxbid_control_price_notice_suffix_is_not_part_of_project_name():
    page = parse_detail_page(_detail("道路建设工程(1标段)招标控制价", ""))
    parsed = SxbidParser.parse(
        "correction", page, pdf_text="项目编号：E1\n最高投标限价：100万元"
    )
    assert parsed.data["项目名称"] == "道路建设工程(1标段)"


def test_sxbid_dual_project_labels_are_mapped_to_project_and_tender_ids():
    page = parse_detail_page(_detail("学校供电工程中标候选人公示", ""))
    parsed = SxbidParser.parse(
        "candidate",
        page,
        pdf_text="""学校供电工程中标候选人公示
        （项目编号：ZLZX 招【2026】0487 号）
        本学校供电工程（招标项目编号：D3201150734fdpsb1v19），经评审。""",
    )
    assert parsed.data["项目编号"] == "D3201150734fdpsb1v19"
    assert parsed.data["招标编号"] == "ZLZX招【2026】0487号"


def test_sxbid_internal_chain_and_list_region_do_not_become_business_fields():
    html = _detail(
        "设备采购招标公告",
        "<script>fetch('/getRelatedContent/1/internal-chain-123')</script>",
    ).replace("<b>实施地：</b></td><td>太原市", "<b>实施地：</b></td><td>")
    page = parse_detail_page(html)
    parsed = SxbidParser.parse(
        "tender",
        page,
        list_record={"region": "太原市", "project_type": "货物"},
        pdf_text="招标内容与范围：采购设备。",
    )
    assert page.project_chain_id == "internal-chain-123"
    assert parsed.data["项目编号"] == ""
    assert parsed.data["项目地点"] == ""


def test_sxbid_submission_address_is_not_opening_place():
    page = parse_detail_page(_detail("道路工程招标公告", ""))
    parsed = SxbidParser.parse(
        "tender",
        page,
        pdf_text="递交地址：太原市交易中心\n递交方法：现场递交",
    )
    assert parsed.data["开启地点"] == ""
    assert parsed.data["递交方法"] == "现场递交"


def test_sxbid_plan_notice_type_is_not_project_nature():
    html = """<div class='page_panel noticeInfoDiv'><div class='page_name'>建设项目招标计划</div>
    <div class='page_content'><table><tr><td><b>项目名称：</b></td><td>建设项目</td></tr>
    </table></div></div>"""
    parsed = SxbidParser.parse("plan", parse_detail_page(html))
    assert parsed.data["项目性质"] == ""


def test_sxbid_acquisition_method_stops_at_fullwidth_numbered_heading():
    text = """4．资格预审文件的获取
获取方式：登录公共资源平台免费下载获取资格预审文件及其他资料。
5．资格预审申请文件的递交
递交截止时间：2024年10月24日09时30分
递交方法：现场递交
递交地址：太原市为民服务中心"""
    assert SxbidParser._paragraph_label(text, "获取方式") == (
        "登录公共资源平台免费下载获取资格预审文件及其他资料。"
    )


def test_sxbid_body_pdf_is_cached_during_notice_phase(tmp_path):
    content = b"%PDF-1.4\nbody\n%%EOF"
    attachment = {
        "source_file_id": "body-1",
        "file_name": "notice.pdf",
        "file_url": "https://www.sxbid.com.cn/f/download?body-1",
    }
    page = ParsedPage(
        title="测试公告",
        publish_time="2026-08-19",
        source_name="",
        raw_text="",
        content_html="",
        headers={},
        body_pdf_url=attachment["file_url"],
        attachments=[attachment],
        project_chain_id="",
    )
    spider = SxbidSpider(categories="award", max_records=1)
    spider.crawler = SimpleNamespace(
        settings=Settings({"NOTICE_OUTPUT_ROOT": str(tmp_path)})
    )
    relative = spider._cache_body_pdf(
        category="award",
        list_record={"id": "notice-1"},
        page=page,
        content=content,
        text_extracted=True,
    )
    target = tmp_path / relative
    assert target.read_bytes() == content
    assert attachment["storage_path"] == relative
    assert attachment["file_hash"] == hashlib.md5(
        content, usedforsecurity=False
    ).hexdigest()
    assert attachment["parse_status"] == "TEXT_EXTRACTED"


def test_sxbid_quota_is_seeded_from_existing_json_after_restart(tmp_path):
    json_dir = tmp_path / "sxbid" / "json"
    json_dir.mkdir(parents=True)
    (json_dir / "award.json").write_text(
        '[{"公告子类型":"award"},{"公告子类型":"award"}]',
        encoding="utf-8",
    )
    spider = SxbidSpider(categories="award", max_records=2)
    spider.crawler = SimpleNamespace(
        settings=Settings({"NOTICE_OUTPUT_ROOT": str(tmp_path)})
    )
    spider._load_existing_counts()
    assert spider._counts["award"] == 2
    assert spider._emitted_counts["award"] == 2
    assert spider._reserve_output_slot("award") is False


def test_sxbid_real_pdf_wrapped_consortium_name_keeps_percent_unit():
    text = """中标候选人基本情况
投标报价（%）
太原市市政工程设计研究
1 院;中铁工程设计咨询集团 80 合格
有限公司
2 同济大学建筑设计研究院（集团）有限公司 80 合格
中标候选人按照招标文件要求承诺的项目负责人情况"""
    rows = SxbidParser._ranked_candidate_details(text, final=False)
    assert rows[0]["候选人名称"] == (
        "太原市市政工程设计研究院;中铁工程设计咨询集团有限公司"
    )
    assert rows[0]["候选人报价"] == "80%"


def test_sxbid_real_award_sentence_contacts_and_nested_suffix_are_cleaned():
    text = """招标人：洪洞县政府工程建设服务中心
地址：洪洞县住建局南院二层
联系人：张立玮
电话：13623437171
电子邮箱：owner@example.com
招标代理机构：山西可名工程项目管理有限公司
地 址：科技街3号
联 系 人：张力登
电 话：15386873113
电 子 邮 箱：agency@example.com
招标方式：公开招标
招标人确定山西路桥市政工程有限公司（联合体牵头人）、
山西省安装集团股份有限公司（联合体成员）为该项目的中标人。
中标价：21536.7350万元"""
    contacts = SxbidParser._contacts(text)
    rows, consortium = SxbidParser._award_details_sxbid(text)
    assert contacts["agency"]["phone"] == "15386873113"
    assert rows[0]["中标人名称"] == "山西路桥市政工程有限公司"
    assert rows[0]["中标价"] == "21536.7350万元"
    assert consortium == ["山西省安装集团股份有限公司"]
    assert SxbidParser._project_name_sxbid(
        {}, "道路工程招标公告二次延期公告", text
    ) == "道路工程"


def test_sxbid_contact_parser_ignores_inline_objection_roles():
    text = """接收异议的联系人: 招标人：程智新；招标代理机构：刘斌
电 话: 招标人：18734883595；招标代理机构：18234042738
十一、联系方式
招 标 人: 静乐县恒源新能源有限公司
联 系 人: 程智新
电 话: 18734883595
招标代理机构: 山西安盛达项目管理有限公司
联 系 人: 刘斌
电 话: 18234042738
招标人或招标代理机构主要负责人: （签章）"""
    contacts = SxbidParser._contacts(text)
    assert contacts["owner"]["name"] == "静乐县恒源新能源有限公司"
    assert contacts["agency"]["name"] == "山西安盛达项目管理有限公司"
    assert contacts["agency"]["phone"] == "18234042738"


def test_sxbid_award_accepts_explicit_unit_and_name_labels():
    rows, _ = SxbidParser._award_details_sxbid(
        "开标方式：网上开标\n中标单位：山西八建集团有限公司\n"
        "中标价：607.166600 万元\n项目经理：武泽宇"
    )
    assert rows == [{
        "标段": "",
        "中标人名称": "山西八建集团有限公司",
        "中标价": "607.166600 万元",
    }]
    rows, _ = SxbidParser._award_details_sxbid(
        "中标人名称 中化二建集团有限公司\n中标价格 10238.093639万元"
    )
    assert rows[0]["中标人名称"] == "中化二建集团有限公司"
    assert rows[0]["中标价"] == "10238.093639万元"
    rows, _ = SxbidParser._award_details_sxbid(
        "公示期结束后由招标人确定永和县兴达工程服务队为隰县经济林"
        "提质增效项目(6标段)的\n中标人，现予以公示。\n"
        "投标报价：178.518192万元"
    )
    assert rows[0]["中标人名称"] == "永和县兴达工程服务队"
    assert rows[0]["中标价"] == "178.518192万元"
