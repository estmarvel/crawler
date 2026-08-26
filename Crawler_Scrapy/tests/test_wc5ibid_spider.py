from scrapy.http import HtmlResponse

from crawler_scrapy.sites.wc5ibid import config
from crawler_scrapy.sites.wc5ibid.parser import Wc5ibidParser
from crawler_scrapy.sites.wc5ibid.exporter import Wc5ibidMultiFormatPipeline
from crawler_scrapy.spiders.wc5ibid import Wc5ibidSpider


def test_six_visible_categories_are_configured():
    assert config.DEFAULT_CATEGORIES == ("zbgg", "kzj", "zbhxgs", "zbjg", "bggg", "fbgg")
    assert config.list_url("zbgg", 2).endswith("/Liems/zbggList/2.html")


def test_gbk_list_structure():
    html = """
    <div class="item-title-cc">项目编号：ABC-001</div>
    <li class="select-search-item"><a href="/Liems/zbggDetail/123/01.html">
      <div class="select-search-title"><div class="computer-name"></div>测试工程招标公告</div>
      <div class="select-item-desc"><span>甲单位</span><span>建筑业</span><span>山西省-太原市</span><span>2026-08-10</span></div>
    </a></li>
    """
    response = HtmlResponse("https://www.5ibid.net/Liems/zbggList/1.html", body=html.encode(), encoding="utf-8")
    records = Wc5ibidSpider._list_records(response)
    assert records[0]["project_no"] == "ABC-001"
    assert records[0]["industry"] == "建筑业"
    assert records[0]["date"] == "2026-08-10"


def test_detail_params_body_and_attachment_mapping():
    html = """
    <div class="ggxq-content-wrap">
      <div class="ggxq-info-title">测试工程招标公告</div>
      <div class="fb-time">发布时间：2026-08-10</div>
      <span class="xgfj-wrap"><a href="/Liems/ShowFileContent?docId=1" title="公告.pdf">下载</a></span>
      <div class="ggxq-params-wrap">
        <div class="row-line"><div class="ggxq-param-name">招标项目名称：</div>测试工程</div>
        <div class="row-line"><div class="ggxq-param-name">招标项目编号：</div>ABC-001</div>
        <div class="row-line"><div class="ggxq-param-name">所属行业：</div>建筑业</div>
        <div class="row-line"><div class="ggxq-param-name">招标项目地址：</div>太原市</div>
      </div>
      <div class="zbnr-content">项目概况与招标范围：道路施工。\n投标人资格要求：具备施工资质。</div>
    </div>
    """
    typ, data, attachments, _, _, title, published = Wc5ibidParser.parse("zbgg", html, {})
    assert typ == "招标公告"
    assert title == "测试工程招标公告" and published == "2026-08-10"
    assert data["项目名称"] == "测试工程"
    assert data["项目编号/招标编号"] == "ABC-001"
    assert data["所属行业"] == "建筑业" and data["项目地点"] == "太原市"
    assert attachments[0]["file_name"] == "公告.pdf"
    assert data["发布网站"] == "旺采网"


def test_legacy_change_timeline_opening_time_and_footer_are_parsed_safely():
    html = """
    <div class="ggxq-info-title">测试工程招标延期公告</div>
    <div class="ggxq-params-wrap">
      <div class="row-line"><div class="ggxq-param-name">招标项目名称：</div>测试工程</div>
      <div class="row-line"><div class="ggxq-param-name">招标项目编号：</div>ABC-001</div>
      <div class="row-line"><div class="ggxq-param-name">招标项目地址：</div>山西省-忻州市</div>
    </div>
    <div class="ht-row ht-row-lastshow">
      <div class="ht-time-hour">18/08/21</div><div class="ht-time-date">09:00</div>
      <div class="ht-desc-title">开标时间</div><div class="ht-desc-con"></div>
    </div>
    <div class="zbnr-content">
      原公告：测试工程招标公告
      <div class="wc-r9"><div>关于我们</div><div>公司地址： 中国江苏省南京市江宁区长青街23号</div></div>
    </div>
    """
    typ, data, _, raw_html, text, _, _ = Wc5ibidParser.parse("bggg", html, {})
    assert typ == "更正结果公示"
    assert data["开标时间"] == "2018/08/21 09:00"
    assert "关于我们" not in text and "公司地址" not in raw_html
    assert data["招标人地址"] == ""


def test_project_address_is_never_used_as_owner_address_for_any_notice_schema():
    params = {"招标项目地址": "山西省-忻州市"}
    for data in (
        {"招标人地址": "山西省-忻州市"},
        {"招标人地址": "山西省-忻州市", "中标候选人名称": []},
        {"招标人地址": "山西省-忻州市", "中标人名称": "某公司"},
        {"招标人地址": "山西省-忻州市", "公共类型": "变更公告"},
    ):
        Wc5ibidParser._sanitize_owner_address(data, params, "公告正文")
        assert data["招标人地址"] == ""


def test_explicit_owner_address_is_preserved_instead_of_project_address():
    data = {"招标人地址": "山西省-忻州市"}
    params = {"招标项目地址": "山西省-忻州市", "招标人地址": "太原市小店区测试路1号"}
    Wc5ibidParser._sanitize_owner_address(data, params, "")
    assert data["招标人地址"] == "太原市小店区测试路1号"


def test_category_schema_mapping():
    assert Wc5ibidParser._notice_type_5ibid("zbgg", "项目资格预审公告", "") == "资格预审公告"
    assert Wc5ibidParser._notice_type_5ibid("kzj", "控制价", "") == "更正结果公示"
    assert Wc5ibidParser._notice_type_5ibid("zbhxgs", "候选人", "") == "中标候选人公示"
    assert Wc5ibidParser._notice_type_5ibid("zbjg", "结果", "") == "中标结果公示"


def test_slash_in_source_category_is_safe_for_output_filename():
    filename, schema = Wc5ibidMultiFormatPipeline._route_config(
        "__wc5ibid__招标/预审公告|招标公告"
    )
    assert filename == "旺采网_招标及预审公告_招标公告"
    assert schema == "招标公告"
