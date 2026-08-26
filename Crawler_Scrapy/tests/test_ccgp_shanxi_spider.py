from crawler_scrapy.schemas.notice_fields import canonicalize_notice_data, get_notice_fields
from crawler_scrapy.sites.ccgp_shanxi.parser import CcgpShanxiParser
from crawler_scrapy.spiders.ccgp_shanxi import CcgpShanxiSpider


def detail(content, **extra):
    return {"articleId": "abc==", "title": "测试公告", "publishDate": 1787631938000, "content": content, **extra}


def test_procurement_notice_fields_and_contacts():
    html = """
    <h2>一、项目基本情况</h2><p>项目编号：1499002026AGK02537</p>
    <p>项目名称：仪器采购</p><p>预算金额（元）：1560000</p><p>最高限价（元）：1500000</p>
    <p>合同履约期限：合同签订后60日</p><p>本项目（否）接受联合体投标。</p>
    <h2>二、申请人的资格要求</h2><p>2.落实政府采购政策需满足的资格要求：专门面向中小企业</p>
    <p>3.本项目的特定资格要求：具备许可证</p>
    <h2>三、获取招标文件</h2><p>地点：政采云平台</p><p>方式：在线获取</p><p>售价（元）：0</p>
    <h2>四、提交投标文件截止时间、开标时间和地点</h2>
    <p>提交投标文件截止时间：2026年09月15日 09:00</p><p>开标时间：2026年09月15日 09:00</p><p>开标地点：太原市</p>
    <h2>七、对本次采购提出询问，请按以下方式联系</h2>
    <p>1.采购人信息</p><p>名称：太原理工大学</p><p>地址：迎泽西大街79号</p><p>联系方式：0351-1</p>
    <p>2.采购代理机构信息</p><p>名称：华夏公司</p><p>地址：国安大厦</p><p>联系方式：0351-2</p>
    <p>3.项目联系方式</p><p>项目联系人：张某</p><p>电话：0351-3</p>
    """
    notice_type, data, _, raw_html, raw_text = CcgpShanxiParser.parse(
        "notice.open",
        detail(html, projectCode="1499002026AGK02537", projectName="仪器采购"),
        {"gpCatalogName": "货物", "procurementMethod": "公开招标", "budgetPrice": "1560000", "purchaseName": "太原理工大学"},
    )
    assert notice_type == "采购公告"
    assert data["项目编号"] == "1499002026AGK02537"
    assert data["预算金额"] == "1560000"
    assert data["最高限价"] == "1500000"
    assert data["采购人地址"] == "迎泽西大街79号"
    assert data["采购代理机构"] == "华夏公司"
    assert data["项目联系人"] == "张某"
    assert raw_html == html and "仪器采购" in raw_text


def test_result_arrays_and_attachment_vo():
    html = """
    <p>一、项目编号：P-1</p><table><tr><th>供应商名称</th><th>供应商地址</th><th>中标（成交）金额</th><th>评审总得分</th></tr>
    <tr><td>甲公司</td><td>太原市</td><td>100000元</td><td>92.05</td></tr></table>
    <table><tr><th>标的名称</th><th>品牌</th><th>规格型号</th><th>数量</th><th>单价</th></tr>
    <tr><td>摄像机</td><td>海康</td><td>X1</td><td>2</td><td>50000</td></tr></table>
    """
    d = detail(html, projectCode="P-1", projectName="设备采购", attachmentVO={
        "domain": "https://files.example/", "attachments": [{"fileId": "a/test.pdf", "name": "结果.pdf", "isShow": True}]
    })
    _, data, attachments, _, _ = CcgpShanxiParser.parse("result.award", d, {"gpCatalogName": "货物"})
    assert data["中标/成交供应商名称"] == "甲公司"
    assert data["评审总得分"] == "92.05"
    assert data["主要标的信息"][0]["标的名称"] == "摄像机"
    assert attachments[0]["file_url"] == "https://files.example/a/test.pdf"


def test_intention_and_change_tables():
    intention = """<table><tr><th>序号</th><th>采购项目名称</th><th>采购需求概况</th><th>预算金额（元）</th><th>预计采购时间</th><th>是否专门面向中小企业采购</th><th>备注</th></tr>
    <tr><td>1</td><td>空气消毒机</td><td>采购设备</td><td>464200</td><td>2026年09月</td><td>否</td><td></td></tr></table>"""
    _, data, _, _, _ = CcgpShanxiParser.parse("intention", detail(intention, author="医院"), {})
    assert data["采购项目名称"] == "空气消毒机"
    assert data["意向明细"][0]["预算金额"] == "464200"
    change = """<p>原公告的采购项目编号：P1</p><p>原公告的采购项目名称：项目一</p><p>更正事项：采购文件</p>
    <table><tr><th>序号</th><th>更正项</th><th>更正前内容</th><th>更正后内容</th></tr><tr><td>1</td><td>时间</td><td>9:00</td><td>10:00</td></tr></table>"""
    _, data, _, _, _ = CcgpShanxiParser.parse("change.correction", detail(change, projectCode="P1", projectName="项目一"), {})
    assert data["更正事项"] == "采购文件"
    assert data["更正明细"][0]["更正后内容"] == "10:00"


def test_all_procurement_schemas_canonicalize():
    for notice_type in (
        "采购意向公开", "采购公告", "采购结果公告", "采购终止公告", "采购变更公告",
        "采购合同公告", "采购合同变更公告", "履约验收公告", "采购意见征询", "中小企业预留执行情况", "历史未归类公告",
    ):
        fields = get_notice_fields(notice_type)
        assert fields
        assert set(canonicalize_notice_data(notice_type, {})) == set(fields)


def test_ai_only_selects_ambiguous_missing_fields():
    spider = CcgpShanxiSpider(categories="notice.open", max_records=1)
    selected = spider.select_ai_extract_fields(
        "采购公告",
        ["项目编号", "预算金额", "项目实施地点", "采购人地址", "项目联系电话"],
        {},
    )
    assert "项目编号" not in selected
    assert "预算金额" not in selected
    assert selected == ["项目实施地点", "采购人地址", "项目联系电话"]
