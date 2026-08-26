import base64
import json
from urllib.parse import quote

from crawler_scrapy.sites.sxty_ebidding import config
from crawler_scrapy.sites.sxty_ebidding.parser import (
    DetailMatch,
    SxtyEbiddingParser,
    contains_captcha,
    decode_dynamic_res,
    find_detail_match,
)
from crawler_scrapy.spiders.sxty_ebidding import SxtyEbiddingSpider


def _encoded(payload):
    source = quote(json.dumps(payload, ensure_ascii=False), safe="")
    return base64.b64encode(source.encode("utf-8")).decode("ascii")


def test_all_frontend_categories_and_payload_contract():
    assert len(config.DEFAULT_FEEDS) == 13
    assert config.FEEDS["engineering.tender"]["category_id"] == "7442520"
    assert config.FEEDS["enterprise.award"]["category_id"] == "7442150"
    payload = config.list_payload("engineering.tender", 2, 100)
    assert payload["pageNo"] == 2
    assert payload["pageSize"] == 50
    assert payload["dto"]["siteId"] == "744"


def test_detail_res_decode_and_exact_multi_package_match():
    payload = {
        "project": {"id": "p1", "name": "车辆配件采购项目"},
        "packages": [
            {
                "id": "pkg1", "code": "ABC/001", "isCurrent": "0",
                "categoryContents": [{
                    "contents": [{"id": "n1", "title": "错误标段公告"}]
                }],
            },
            {
                "id": "pkg2", "code": "ABC/002", "isCurrent": "1",
                "categoryContents": [{
                    "contents": [{"id": "n1", "title": "正确标段公告"}]
                }],
            },
        ],
    }
    decoded = decode_dynamic_res(_encoded(payload))
    match = find_detail_match(decoded, "n1", {"packageCode": "ABC/002"})
    assert match.package["id"] == "pkg2"
    assert match.content["title"] == "正确标段公告"


def test_tender_parser_uses_api_fields_but_keeps_two_identifiers_separate():
    match = DetailMatch(
        project={
            "id": "1276477932746833920",
            "outProjectId": "2090630335979495425",
            "name": "车辆相关零配件采购项目",
            "code": "SXJJZB-20260819",
            "tendereeOrgName": "大同市公共交通有限责任公司",
            "agencyOrgName": "山西金久工程项目管理有限公司",
        },
        package={"id": "pkg", "code": "SXJJZB-20260819/001"},
        content={
            "id": "notice1",
            "title": "车辆相关零配件采购项目招标公告",
            "categoryId": "7442520",
            "categoryName": "招标公告",
            "publishDate": "2026-08-24 09:47:34",
            "purchaseName": "公开招标",
            "provinceName": "山西省",
            "cityName": "大同市",
            "quoteEndTime": "2026-09-01 09:30:00",
            "tenderFileSaleBeginTime": "2026-08-24 10:00:00",
            "tenderFileSaleEndTime": "2026-08-31 10:00:00",
            "text": """
                <p>招标编号：SXJJZB-20260819</p>
                <p>招标项目编号：I3100000021B00931N13</p>
                <p>项目地点：山西省大同市</p>
                <p>开标时间：2026-09-01 09:30:00</p>
                <p>一、招标范围：车辆零配件采购。</p>
                <p>二、投标人资格要求：具备独立法人资格。</p>
            """,
            "resourceList": [{
                "id": "file1", "fileName": "招标公告.pdf",
                "fileFullPath": "/files/notice.pdf", "fileSize": 1234,
                "fileMd5": "0123456789abcdef0123456789abcdef",
            }],
        },
    )
    parsed = SxtyEbiddingParser.parse(
        "engineering.tender",
        match,
        {"id": "notice1", "packageCode": "SXJJZB-20260819/001"},
    )
    assert parsed.notice_type == "招标公告"
    assert parsed.data["项目名称"] == "车辆相关零配件采购项目"
    assert parsed.data["项目编号"] == "I3100000021B00931N13"
    assert parsed.data["招标编号"] == "SXJJZB-20260819"
    assert parsed.data["招标人/采购人名称"] == "大同市公共交通有限责任公司"
    assert parsed.data["招标代理机构"] == "山西金久工程项目管理有限公司"
    assert parsed.data["开标时间"] == "2026-09-01 09:30:00"
    assert parsed.data["开启时间"] == ""
    assert parsed.attachments[0]["file_url"].endswith("/files/notice.pdf")
    assert parsed.attachments[0]["file_hash"] == "0123456789abcdef0123456789abcdef"


def test_internal_snowflake_ids_are_never_business_codes():
    assert SxtyEbiddingParser._business_code("2090630335979495425") == ""
    assert SxtyEbiddingParser._business_code("SXZB-26051174F014/01")


def test_api_project_code_fills_project_number_not_tender_number():
    match = DetailMatch(
        project={"name": "询比项目", "code": "ZCXB20260825"},
        package={"id": "pkg", "code": "ZCXB20260825/001"},
        content={
            "id": "n2", "title": "询比项目采购公告",
            "publishDate": "2026-08-24 10:00:00",
            "text": "<p>询比项目采购公告</p><p>采购内容：设备改造。</p>",
            "resourceList": [],
        },
    )
    parsed = SxtyEbiddingParser.parse("enterprise.tender", match, {"id": "n2"})
    assert parsed.data["项目编号"] == "ZCXB20260825"
    assert parsed.data["招标编号"] == ""


def test_site_specific_candidate_and_award_tables_are_aligned():
    candidate_html = """
      <table><tr><td>序号</td><td>中标候选人名称</td><td>投标报价</td></tr>
      <tr><td>1</td><td>甲建设有限公司</td><td>395829.6元</td></tr></table>
    """
    assert SxtyEbiddingParser._candidate_details_local(candidate_html, "")[0] == {
        "标段": "", "候选人名称": "甲建设有限公司", "候选人报价": "395829.6元"
    }
    vertical = """
      <table><tr><td>中标人名称</td><td>乙公司</td></tr>
      <tr><td>中标价格</td><td>659200.00元</td></tr></table>
    """
    assert SxtyEbiddingParser._award_details_local(vertical, "")[0] == {
        "标段": "", "中标人名称": "乙公司", "中标价": "659200.00元"
    }
    horizontal = """
      <table><tr><td>成交人名称</td><td>成交价格</td><td>工期</td></tr>
      <tr><td>丙公司</td><td>1049483.54元</td><td>1个月</td></tr></table>
    """
    assert SxtyEbiddingParser._award_details_local(horizontal, "")[0] == {
        "标段": "", "中标人名称": "丙公司", "中标价": "1049483.54元"
    }
    spaced_text = "成交供应商：丁公司\n成 交 价：662725.23元"
    assert SxtyEbiddingParser._award_details_local("", spaced_text)[0] == {
        "标段": "", "中标人名称": "丁公司", "中标价": "662725.23元"
    }
    taxed_vertical = """
      <table><tr><td>中标人名称</td><td>戊公司</td></tr>
      <tr><td>中标价格（含税）</td><td>2829483.79元</td></tr></table>
    """
    assert SxtyEbiddingParser._award_details_local(taxed_vertical, "")[0] == {
        "标段": "", "中标人名称": "戊公司", "中标价": "2829483.79元"
    }


def test_html_rule_text_does_not_repeat_paragraphs_inside_tables():
    heading = "招标范围如下：" + "测试说明" * 30
    html = f"""<p>{heading}</p><table>
      <tr><td><p>001</p></td><td><p>设备供货</p></td></tr>
    </table>"""
    text = SxtyEbiddingParser._html_rule_text(html)
    assert text == f"{heading}\n001 | 设备供货"


def test_candidate_table_without_price_and_ranked_text_are_supported():
    no_price_table = """
      <table><tr><td>序号</td><td>供应商名称</td><td>排名</td></tr>
      <tr><td>1</td><td>甲供应商有限公司</td><td>1</td></tr>
      <tr><td>2</td><td>乙供应商有限公司</td><td>2</td></tr></table>
    """
    assert SxtyEbiddingParser._candidate_details_local(no_price_table, "") == [
        {"标段": "", "候选人名称": "甲供应商有限公司", "候选人报价": ""},
        {"标段": "", "候选人名称": "乙供应商有限公司", "候选人报价": ""},
    ]
    ranked_text = """一、中标候选人
第1名：甲建设有限公司
第2名：乙建设有限公司"""
    assert SxtyEbiddingParser._candidate_details_local("", ranked_text) == [
        {"标段": "", "候选人名称": "甲建设有限公司", "候选人报价": ""},
        {"标段": "", "候选人名称": "乙建设有限公司", "候选人报价": ""},
    ]


def test_site_specific_tender_section_headings_do_not_cross_sections():
    data = {"招标内容与范围": "", "申请人资格要求/投标人资格要求": ""}
    text = """2.项目概况与招标范围
2.1项目规模：建设一座停车场。
2.2招标内容与范围：施工图纸内全部工程。
3.投标人资质要求
3.1具备建筑工程施工总承包三级资质。
4.招标文件的获取
线上获取。"""
    SxtyEbiddingParser._merge_tender_sections_local(data, "招标公告", text)
    assert "施工图纸内全部工程" in data["招标内容与范围"]
    assert "投标人资质要求" not in data["招标内容与范围"]
    assert "建筑工程施工总承包三级资质" in data["申请人资格要求/投标人资格要求"]
    assert "招标文件的获取" not in data["申请人资格要求/投标人资格要求"]

    procurement = {"招标内容与范围": "", "申请人资格要求/投标人资格要求": ""}
    procurement_text = """5、采购需求：监理服务范围内的全部内容。
6、服务期限：两年。
二、申请人的资格要求：具有独立承担民事责任的能力。
三、获取询比采购文件：在线获取。"""
    SxtyEbiddingParser._merge_tender_sections_local(
        procurement, "招标公告", procurement_text
    )
    assert procurement["招标内容与范围"] == "监理服务范围内的全部内容。"
    assert procurement["申请人资格要求/投标人资格要求"] == (
        "具有独立承担民事责任的能力。"
    )

    table_scope = {"招标内容与范围": "", "申请人资格要求/投标人资格要求": ""}
    table_text = """二.招标内容与招标范围
本项目划分为1个标段：
标段 | 招标内容 | 招标范围 | 供货地点 | 合同履行期限
001 | 底盘类 | 零配件供应和售后服务 | 招标人指定地点 | 一年
三.投标人资格要求
具备独立法人资格。
四.招标文件的获取"""
    SxtyEbiddingParser._merge_tender_sections_local(
        table_scope, "招标公告", table_text
    )
    assert "零配件供应和售后服务" in table_scope["招标内容与范围"]
    assert "具备独立法人资格" not in table_scope["招标内容与范围"]


def test_short_contact_template_and_correction_nature():
    data = {
        "招标人/采购人名称": "", "招标人地址": "", "招标人联系人": "",
        "招标人联系方式": "", "招标代理机构": "", "招标代理机构地址": "",
        "招标代理机构联系人": "", "招标代理机构联系方式": "",
    }
    text = """8 联系方式
名称：甲采购单位
地址：甲路1号
联系人：张三
联系电话：13000000000
代理机构：乙代理公司
联系地址：乙路2号
联系人：李四
联系电话：0351-1234567"""
    SxtyEbiddingParser._merge_party_fields(data, "招标公告", text)
    assert data["招标人/采购人名称"] == "甲采购单位"
    assert data["招标人地址"] == "甲路1号"
    assert data["招标代理机构"] == "乙代理公司"
    assert data["招标代理机构地址"] == "乙路2号"
    assert SxtyEbiddingParser._correction_nature("测试项目招标延期公告", "其他公告") == "延期公告"
    assert SxtyEbiddingParser._correction_nature("测试项目招标补充公告", "其他公告") == "其他"
    assert SxtyEbiddingParser._notice_type_local(
        "other", "某项目招标控制价", "招标控制价为100万元"
    ) == "更正结果公示"


def test_explicit_contact_blocks_override_crossed_public_parser_values():
    data = {
        "招标人/采购人名称": "甲采购单位",
        "招标人地址": "", "招标人联系人": "误取的代理联系人",
        "招标人联系方式": "0351-0000000",
        "招标代理机构": "乙代理公司", "招标代理机构地址": "",
        "招标代理机构联系人": "", "招标代理机构联系方式": "",
    }
    text = """十、联系方式
招标人：甲采购单位
代理机构：乙代理公司
地址：乙路2号
联系人：李四
手机：13900000000"""
    SxtyEbiddingParser._merge_party_fields(data, "招标公告", text)
    assert data["招标人联系人"] == ""
    assert data["招标人联系方式"] == ""
    assert data["招标代理机构联系人"] == "李四"
    assert data["招标代理机构联系方式"] == "13900000000"


def test_plan_table_fields_do_not_absorb_adjacent_columns():
    data = {
        "招标方式": "", "项目类型": "", "项目总投资": "",
        "招标内容": "", "招标人名称": "", "行政监督部门": "",
        "建设内容及规模": "",
    }
    text = """项目类型： | 水利 | 项目总投资： | 490万元
招标内容： | 重要材料 | 招标方式： | 公开招标
招标人名称： | 甲单位 | 行政监督部门： | 乙部门
建设内容及规模： | 6500m球墨铸铁管
招标公告（资格预审公告）预计发布时间：2026-09"""
    SxtyEbiddingParser._merge_plan_table_fields_local(data, text)
    assert data["项目类型"] == "水利"
    assert data["项目总投资"] == "490万元"
    assert data["招标内容"] == "重要材料"
    assert data["招标方式"] == "公开招标"
    assert data["招标人名称"] == "甲单位"
    assert data["行政监督部门"] == "乙部门"
    assert data["建设内容及规模"] == "6500m球墨铸铁管"


def test_result_placeholders_are_removed_and_certificate_is_split():
    data = {
        "项目经理": "________(签名）",
        "项目经理证书名称": "中华人民共和国一级建造师注册证书、晋1142024202501467",
        "项目经理证书编号": "",
        "工期": "",
    }
    html = """<table><tr><td>成交人名称</td><td>成交价格</td><td>工期</td></tr>
    <tr><td>甲公司</td><td>100万元</td><td>60日历天</td></tr></table>"""
    text = "项目经理：张三\n证书名称及编号：一级建造师注册证书、晋2142023202485307"
    SxtyEbiddingParser._merge_award_execution_fields_local(data, html, text)
    assert data["项目经理"] == ""
    assert data["工期"] == "60日历天"
    assert data["项目经理证书名称"] == "一级建造师注册证书"
    assert data["项目经理证书编号"] == "晋2142023202485307"


def test_correction_only_keeps_change_section_and_never_copies_basis_code():
    text = """某项目招标变更公告
（招标编号：ZB-001）
一、内容
原开标时间：2026-08-01 09:00
现变更为：2026-08-05 09:00
二、监督部门
某监督部门
三、联系方式
招标人：甲单位"""
    assert SxtyEbiddingParser._correction_content_local(text) == (
        "原开标时间：2026-08-01 09:00\n现变更为：2026-08-05 09:00"
    )


def test_site_ai_only_reviews_known_problem_fields():
    spider = SxtyEbiddingSpider()
    assert spider.ai_metadata_key == "sxtyEbiddingHybridAi"
    assert spider.ai_extract_fields["更正结果公示"] == ("公告内容",)
    assert not spider.ai_extract_fields["招标公告"]
    assert spider.is_ai_field_suspicious(
        "招标公告",
        "招标内容与范围",
        "设备供货。\n质量要求：合格。",
        {},
        "",
    )
    assert not spider.is_ai_field_suspicious(
        "招标公告", "招标内容与范围", "设备供货。", {}, ""
    )


def test_captcha_markers_and_request_window():
    assert contains_captcha('<div id="captcha"></div><script>pointsVerify()</script>')
    assert not contains_captcha('{"code":0,"res":{"rows":[]}}')
    spider = SxtyEbiddingSpider(
        feeds="engineering.tender",
        start_date="2026-08-01",
        end_date="2026-08-24",
        page_size=20,
    )
    request = spider._list_request("engineering.tender", 1)
    body = json.loads(request.body)
    assert body["dto"]["publishDate"] == "2026-08-01T00:00:00.000Z"
    assert body["dto"]["publishEndDate"] == "2026-08-24T23:59:59.999Z"
