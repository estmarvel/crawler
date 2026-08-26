from __future__ import annotations

from crawler_scrapy.schemas.notice_fields import canonicalize_notice_data
from crawler_scrapy.sites.gxebidding import config
from crawler_scrapy.sites.gxebidding.parser import (
    DetailDocument,
    GxebiddingParser,
    parse_detail_document,
    parse_list_records,
    parse_page_info,
)


LIST_HTML = """
<html><body><ul class="newslist"><li>
  <a href="https://gx.e-bidding.org/sdny_bulletin/2026-08-11/11253.html"
     title="智慧城市数字底座项目（一期）EPC总承包二次招标公告">
    <dl class="newsinfo row">
      <dd>招标编号：<span></span></dd><dd>招标方式：<span>公开招标</span></dd>
      <dd>报名截止时间：<span>2026-08-18</span></dd>
    </dl>
    <div class="newsDate"><div>2026-08-11</div></div>
  </a>
</li></ul><div class="pages">共<label>32</label>页 当前页是第<label>1</label>页</div>
</body></html>
""".encode()


DETAIL_HTML = """
<html><body><iframe id="pdfContainer"
 src="https://gx.e-bidding.org/resource/css/pdfjs/web/viewer.html?file=https://gx.e-bidding.org/bidprocurement/datacenter-cebpubserver/cebpubserver/dataCeboubServerCommonController/openFileById?fileType%3D2%26id%3Dc79fb4590e334e2fa913798c1c19bc5f&page=1">
</iframe></body></html>
""".encode()


def test_list_and_detail_html_are_decoded_and_namespaced():
    records = parse_list_records(LIST_HTML)
    assert len(records) == 1
    assert records[0].title.startswith("智慧城市")
    assert records[0].deadline == "2026-08-18"
    assert records[0].path_family == "bulletin"
    assert parse_page_info(LIST_HTML) == (32, 1)
    detail = parse_detail_document(DETAIL_HTML)
    assert detail.file_type == "2"
    assert detail.file_id == "c79fb4590e334e2fa913798c1c19bc5f"
    assert "fileType=2" in detail.pdf_url
    assert config.source_notice_id("bulletin", "11253") == "bulletin:11253"


def test_tender_pdf_separates_project_and_tender_identifiers():
    text = """
智慧城市数字底座项目（一期）EPC总承包二次招标公告
招标编号：JLKJ-2026TZJS-01
招标项目所在地区：山西省临汾市
本智慧城市数字底座项目（一期）EPC总承包，招标项目编号：E1401005146001025002，
招标人为临汾济林智能科技有限公司，资金来源为企业自筹。
二、项目概况与招标范围
项目概况：建设智慧城市数字底座。
招标范围：设计、采购、施工。
三、投标人资格要求
具有相应资质。
四、招标文件的获取
获取时间：2026年8月11日16:00-2026年8月18日16:00
获取方法：网上获取
五、投标文件的递交
递交截止时间：2026年9月1日10点00分
递交方法：线上递交
六、开标时间及地点
开标时间：2026年9月1日10点00分
开标方式：线上开标
招标人：临汾济林智能科技有限公司
联系人：王女士
电话：0357-5738888
招标代理机构：山西尚德信达工程项目管理有限公司
联系人：许永杰
联系电话：0357-2030588
"""
    parsed = GxebiddingParser.parse(
        "lawful",
        "tender",
        {
            "title": "智慧城市数字底座项目（一期）EPC总承包二次招标公告",
            "publish_time": "2026-08-11",
        },
        parse_detail_document(DETAIL_HTML),
        pdf_text=text,
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    assert parsed.notice_type == "招标公告"
    assert data["项目名称"] == "智慧城市数字底座项目（一期）EPC总承包"
    assert data["项目编号"] == "E1401005146001025002"
    assert data["招标编号"] == "JLKJ-2026TZJS-01"
    assert data["项目地点"] == "山西省临汾市"
    assert data["招标人/采购人名称"] == "临汾济林智能科技有限公司"
    assert parsed.attachments[0]["source_file_id"] == "c79fb4590e334e2fa913798c1c19bc5f"


def test_candidate_wrapped_table_keeps_company_price_alignment():
    text = """
山西粤能望方山100MW风力发电项目EPC总承包监理中标候选人公示
（招标编号：SXFW-GK-26014）
公示开始时间：2026-07-31 19:00
公示结束时间：2026-08-03 19:00
本项目（招标项目编号：E1401005146001719001）
1、中标候选人基本情况
排序 中标候选人名称 投标报价（元） 服务期限 质量标准
    山西晔通建设工程项
1                   1295000                符合要求
    目管理有限公司
    中太工程建设咨询（天
2                   1230000                符合要求
    津）有限公司
2、中标候选人按照招标文件要求承诺的项目总监情况
略
3、中标候选人响应招标文件要求的资格能力条件
1 山西晔通建设工程项目管理有限公司 响应
2 中太工程建设咨询（天津）有限公司 响应
二、提出异议的渠道和方式
"""
    parsed = GxebiddingParser.parse(
        "lawful", "candidate",
        {"title": "山西粤能望方山100MW风力发电项目EPC总承包监理中标候选人公示", "publish_time": "2026-07-31"},
        DetailDocument("https://gx.e-bidding.org/a.pdf", "a", "5"),
        pdf_text=text,
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    assert data["中标候选人名称"] == [
        "山西晔通建设工程项目管理有限公司",
        "中太工程建设咨询（天津）有限公司",
    ]
    assert [str(value) for value in data["中标候选人报价"]] == [
        "1295000.00", "1230000.00"
    ]


def test_award_and_termination_map_to_database_types_without_losing_source_nature():
    award = GxebiddingParser.parse(
        "purchase", "award",
        {"title": "检测试剂采购项目成交结果公示", "publish_time": "2026-08-11"},
        DetailDocument("https://gx.e-bidding.org/a.pdf", "a", "4"),
        pdf_text=(
            "检测试剂采购项目成交结果公示\n项目编号：SXQC-2026-ZB-049\n"
            "成交供应商：山西星荣生物科技有限公司 成交金额：178130元\n"
        ),
    )
    award_data = canonicalize_notice_data(award.notice_type, award.data)
    assert award.notice_type == "中标结果公示"
    assert award_data["中标人名称"] == ["山西星荣生物科技有限公司"]
    assert str(award_data["中标价"][0]) == "178130.00"

    termination = GxebiddingParser.parse(
        "lawful", "termination",
        {"title": "某储能项目终止公告", "publish_time": "2026-06-12"},
        DetailDocument("https://gx.e-bidding.org/f.pdf", "f", "6"),
        pdf_text="某储能项目终止公告\n招标项目编号：E1401005146001999001\n因建设方案调整，本项目终止。",
    )
    termination_data = canonicalize_notice_data(
        termination.notice_type, termination.data
    )
    assert termination.notice_type == "更正结果公示"
    assert termination_data["公共类型"] == "终止公告"
    assert "本项目终止" in termination_data["公告内容"]


def test_bracketed_segment_and_correction_titles_produce_stable_project_names():
    detail = DetailDocument("https://gx.e-bidding.org/a.pdf", "a", "4")
    award = GxebiddingParser.parse(
        "lawful", "award",
        {
            "title": "[仪器设备采购项目]（第一标段） 中标结果公示",
            "publish_time": "2026-08-11",
        },
        detail,
        pdf_text="仪器设备采购项目中标结果公示\n中标人：甲有限公司 中标价：1元",
    )
    assert award.data["项目名称"] == "仪器设备采购项目（第一标段）"

    correction = GxebiddingParser.parse(
        "lawful", "change",
        {
            "title": "仪器设备采购项目中标结果公示更正",
            "publish_time": "2026-08-11",
        },
        DetailDocument("https://gx.e-bidding.org/c.pdf", "c", "3"),
        pdf_text="仪器设备采购项目中标结果公示更正\n更正中标金额。",
    )
    assert correction.data["项目名称"] == "仪器设备采购项目"


def test_wrapped_explicit_project_code_wins_and_generic_code_becomes_tender_code():
    parsed = GxebiddingParser.parse(
        "lawful", "tender",
        {"title": "供电工程招标公告", "publish_time": "2026-08-11"},
        DetailDocument("https://gx.e-bidding.org/a.pdf", "a", "2"),
        pdf_text=(
            "供电工程招标公告\n(项目编号:GXZB-2026-028)\n"
            "本项目供电工程(招标\n项目编号：E1401005146001635001)，"
            "招标人为甲有限公司。"
        ),
    )
    assert parsed.data["项目编号"] == "E1401005146001635001"
    assert parsed.data["招标编号"] == "GXZB-2026-028"


def test_raw_table_fallback_and_percentage_quote_keep_row_semantics():
    parsed = GxebiddingParser.parse(
        "lawful", "candidate",
        {"title": "EPC总承包项目中标候选人公示", "publish_time": "2026-08-11"},
        DetailDocument("https://gx.e-bidding.org/a.pdf", "a", "5"),
        pdf_text="1、中标候选人基本情况\n中标候选人名称 下浮率\n（%）\n",
        table_text=(
            "1、中标候选人基本情况\n排序\n中标候选人名称\n下浮率\n"
            "1\n山西八建集团有\n限公司\n99.73%\n合格\n"
            "2\n中铁城建集团第\n一工程有限公司\n99.95%\n合格\n"
            "2、中标候选人按照招标文件要求承诺的项目负责人情况\n"
        ),
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    assert data["中标候选人名称"] == [
        "山西八建集团有限公司", "中铁城建集团第一工程有限公司"
    ]
    assert data["中标候选人报价"] == ["99.73%", "99.95%"]


def test_award_multiline_consortium_and_tax_price_are_not_merged():
    parsed = GxebiddingParser.parse(
        "lawful", "award",
        {"title": "储能项目中标结果公示", "publish_time": "2026-08-11"},
        DetailDocument("https://gx.e-bidding.org/a.pdf", "a", "4"),
        pdf_text="储能项目中标结果公示",
        table_text=(
            "一、中标人信息：\n中标人名称：\n"
            "牵头人：中国建筑第六工程局有限公司\n"
            "联合体成员：中煤科工重庆设计研究院（集团）有限公司\n"
            "中 标 价（含税）：5,298,294.00 元\n二、其他公示内容\n"
        ),
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    assert data["中标人名称"] == ["中国建筑第六工程局有限公司"]
    assert data["联合体成员"] == ["中煤科工重庆设计研究院（集团）有限公司"]
    assert [str(value) for value in data["中标价"]] == ["5298294.00"]
