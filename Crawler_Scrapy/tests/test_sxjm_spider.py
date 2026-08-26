from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from scrapy import Request
from scrapy.http import TextResponse
from scrapy.settings import Settings

from crawler_scrapy.items import NoticeItem
from crawler_scrapy.pipelines import (
    HtmlSnapshotPipeline,
    NoticeFilesPipeline,
    NoticeSchemaPipeline,
)
from crawler_scrapy.schemas.notice_fields import (
    ANNOUNCEMENT_SCHEMAS,
    PARSER_DIAGNOSTIC_FIELDS,
)
from crawler_scrapy.sites.sxjm import config
from crawler_scrapy.sites.sxjm.audit import audit_record
from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline
from crawler_scrapy.sites.sxjm.parser import (
    SxjmParser,
    clean_html_keep_lines,
    decrypt_envelope,
)
from crawler_scrapy.spiders.sxjm import SxjmSpider


def _encrypt(value):
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    encrypted = AES.new(config.AES_KEY, AES.MODE_CBC, config.AES_IV).encrypt(
        pad(raw, AES.block_size)
    )
    return base64.b64encode(encrypted).decode("ascii")


def _detail(announcement_type=1):
    return {
        "id": 44849,
        "announcement_type": announcement_type,
        "title": "华晋焦煤有限责任公司设备采购招标公告",
        "project_name": "华晋焦煤有限责任公司设备采购",
        "content": "<p>项目编号：SJZBHJ06000026H057V18</p><p>招标人：华晋焦煤有限责任公司</p>",
        "publish_time_format": "2026-07-21 18:00:00",
        "created_at_format": "2026-07-21 17:00:00",
        "bid_opening_date_format": "2026-08-12 09:00:00",
        "tender_number": "SJZBHJ06000026H057V18",
        "code": "SJZBHJ06000026H057V18/001",
        "project_type": 10,
        "industry_category": "采矿业",
        "region": "山西省吕梁市",
        "tendering_agency": "山西焦煤集团招标有限公司",
        "document": [
            {
                "id": 53634,
                "original_name": "招标公告.pdf",
                "mime_type": "application/pdf",
                "path": "zcpt/2026-07-21/example.pdf",
            }
        ],
    }


def test_decrypt_envelope_round_trip():
    expected = {"data": [{"id": 44849}], "total": 1}
    assert decrypt_envelope({"errcode": 0, "result": _encrypt(expected)}) == expected


def test_list_params_match_tender_project_frontend():
    assert config.list_params("zbxm", "4", 2, 50) == {
        "page": 2,
        "per_page": 50,
        "announcement_type": "4",
        "project_type": "",
        "category": 3,
    }


def test_parser_maps_tender_notice_and_attachment():
    subtype, notice_type, data, attachments = SxjmParser.parse("zbxm", "zbgg", _detail())
    assert subtype == "zbgg"
    assert notice_type == "招标公告"
    assert data["项目性质"] == ""
    assert data["源站公告性质"] == "招标（预审）公告"
    assert data["项目名称"]
    assert data["发布网站"] == config.PLATFORM_NAME
    assert attachments[0]["file_name"] == "招标公告.pdf"
    assert attachments[0]["file_url"].endswith("/zcpt/2026-07-21/example.pdf")


def test_parser_maps_only_evidenced_project_nature_enums():
    assert SxjmParser.parse("yfxm", "zbgg", _detail())[2]["项目性质"] == "依法必须招标"
    assert SxjmParser.parse("fzxm", "cggg", _detail(5))[2]["项目性质"] == "非依法招标"
    assert SxjmParser.parse("jycg", "cggg", _detail(5))[2]["项目性质"] == "非依法招标"
    assert SxjmParser.parse("zbxm", "zbgg", _detail())[2]["项目性质"] == ""


def test_parser_outputs_exact_latest_schema_fields_for_supported_types():
    cases = (
        ("yfxm", "zbjh", _detail(19), "招标计划"),
        (
            "zbxm",
            "zbgg",
            {**_detail(1), "title": "设备采购资格预审公告"},
            "资格预审公告",
        ),
        ("zbxm", "zbgg", _detail(1), "招标公告"),
        ("zbxm", "hxr", _detail(2), "中标候选人公示"),
        ("zbxm", "zbjg", _detail(3), "中标结果公示"),
        ("zbxm", "zzgg", _detail(4), "更正结果公示"),
    )
    for channel, section, detail, expected_type in cases:
        _, notice_type, data, _ = SxjmParser.parse(channel, section, detail)
        expected_fields = set(ANNOUNCEMENT_SCHEMAS[expected_type]) | set(
            PARSER_DIAGNOSTIC_FIELDS.get(expected_type, ())
        )
        assert notice_type == expected_type
        assert set(data) == expected_fields


def test_parser_uses_prequalification_schema_and_separate_export_route():
    detail = _detail(1)
    detail["title"] = "材料框架协议采购项目资格预审公告"
    detail["project_name"] = detail["title"]
    detail["content"] = (
        "<p>一、采购范围及相关要求</p>"
        "<p>二、申请人资格要求：</p><p>申请人须具备独立法人资格。</p>"
        "<p>三、资格预审文件的获取：</p><p>获取方式：登录平台下载。</p>"
    )

    subtype, notice_type, data, _ = SxjmParser.parse("zbxm", "zbgg", detail)

    assert subtype == "zbgg"
    assert notice_type == "资格预审公告"
    assert data["项目名称"] == "材料框架协议采购项目"
    assert data["项目概况与招标范围"] == ""
    assert data["申请人资格要求/投标人资格要求"] == "申请人须具备独立法人资格。"
    route = "__sxjm_prequalification_zbxm.zbgg__"
    assert SxjmMultiFormatPipeline._route_config(route) == (
        "招标项目_资格预审公告",
        "资格预审公告",
    )


def test_parser_prioritizes_inline_tender_project_code_over_business_number():
    detail = _detail(1)
    detail["invest_project_code"] = ""
    detail["tender_number"] = "SJZBZH04160625G003V32"
    detail["content"] = (
        "<p>项目编号：SJZBZH04160625G003V32</p>"
        "<p>本项目（招标项目编号：D1401000936001700b13），已具备招标条件。</p>"
    )

    _, _, data, _ = SxjmParser.parse("yfxm", "zbgg", detail)

    assert data["项目编号"] == "D1401000936001700b13"
    assert data["招标编号"] == "SJZBZH04160625G003V32"
    assert data["项目编号/招标编号"] == (
        "D1401000936001700b13；SJZBZH04160625G003V32"
    )


def test_parser_does_not_treat_other_requirement_as_quality_and_prefers_deadline():
    detail = _detail(5)
    detail["bid_opening_date_format"] = "2026-08-12 09:00:00"
    detail["content"] = (
        "<p>2.5其他要求：卖方负责运输，费用和风险由卖方承担。</p>"
        "<p>5.1递交截止时间：2026年8月12日8时30分</p>"
        "<p>6.1开标时间：2026年8月12日9时00分</p>"
    )

    _, _, data, _ = SxjmParser.parse("jycg", "cggg", detail)

    assert data["质量要求"] == ""
    assert data["递交截止时间"] == "2026年8月12日8时30分"
    assert data["开启时间"] == "2026-08-12 09:00:00"


def test_parser_rejects_person_account_as_agency_but_keeps_explicit_organization():
    detail = _detail(5)
    detail["tendering_agency"] = "王国玺(FX)"
    detail["content"] = "<p>采购人：山西汾西矿业（集团）有限责任公司</p>"

    _, _, data, _ = SxjmParser.parse("jycg", "cggg", detail)

    assert data["招标代理机构"] == ""
    assert data["组织形式"] == ""

    detail["content"] += "<p>采购代理机构：山西焦煤集团招标有限公司</p>"
    _, _, data, _ = SxjmParser.parse("jycg", "cggg", detail)
    assert data["招标代理机构"] == "山西焦煤集团招标有限公司"


def test_parser_removes_notice_round_markers_without_damaging_real_project_name():
    detail = _detail(5)
    detail["project_name"] = "【二次】供热服务项目采购公告"
    _, _, data, _ = SxjmParser.parse("jycg", "cggg", detail)
    assert data["项目名称"] == "供热服务项目"

    detail["project_name"] = "矿井工程(第2次招标)招标暂停公告"
    _, _, data, _ = SxjmParser.parse("zbxm", "zzgg", detail)
    assert data["项目名称"] == "矿井工程"

    detail["project_name"] = "二次供水工程招标公告"
    _, _, data, _ = SxjmParser.parse("zbxm", "zbgg", detail)
    assert data["项目名称"] == "二次供水工程"

    detail["project_name"] = "矿井设备采购项目（002标段）招标公告"
    _, _, data, _ = SxjmParser.parse("zbxm", "zbgg", detail)
    assert data["项目名称"] == "矿井设备采购项目（002标段）"

    detail["project_name"] = "矿井设备采购项目招标三次延期公告"
    _, _, data, _ = SxjmParser.parse("yfxm", "zbgg", detail)
    assert data["项目名称"] == "矿井设备采购项目"


def test_parser_reads_cross_line_delivery_address_as_delivery_method():
    detail = _detail(5)
    detail["content"] = (
        "<p>5.响应文件递交</p>"
        "<p>5.2递交地址：</p>"
        "<p>登录山西焦煤电子招采平台上传响应文件。</p>"
        "<p>6.开启时间：2026年8月12日9时00分</p>"
    )

    _, _, data, _ = SxjmParser.parse("jycg", "cggg", detail)

    assert data["递交方法"] == "登录山西焦煤电子招采平台上传响应文件"


def test_parser_keeps_complete_bracket_identifier_and_rejects_prefix_only_code():
    detail = _detail()
    detail["content"] = "<p>项目编号：fxkynghw[2025]002</p>"
    detail["tender_number"] = "fxkynghw"
    _, _, data, _ = SxjmParser.parse("jycg", "cggg", detail)
    assert data["项目编号"] == "fxkynghw[2025]002"
    assert data["招标编号"] == ""

    detail["content"] = "<p>本公告未披露编号</p>"
    detail["invest_project_code"] = "DLXCGQT"
    _, _, data, _ = SxjmParser.parse("jycg", "cggg", detail)
    assert data["项目编号"] == ""


def test_parser_rejects_all_zero_plan_code_and_cleans_duplicate_location_heading():
    detail = _detail(19)
    detail.update(
        {
            "title": "设备项目招标计划变更公告",
            "project_name": "设备项目招标计划变更公告",
            "invest_project_code": "0000-000000-00-00-000000",
            "project_address": "四、山西省吕梁市中阳县武家庄镇吴家峁煤矿",
            "content": "",
        }
    )
    _, _, data, _ = SxjmParser.parse("yfxm", "zbjh", detail)

    assert data["项目编号"] == ""
    assert data["建设地点"] == "山西省吕梁市中阳县武家庄镇吴家峁煤矿"


def test_parser_keeps_acquisition_operation_and_scope_without_neighbor_fields():
    detail = _detail(5)
    detail["content"] = """
    <p>2.1采购范围：本项目采购支架搬运车。</p>
    <p>2.2交货地点：采购人指定地点。</p>
    <p>2.3交货期：合同签订后60天。</p>
    <p>3.供应商资格要求：具有有效营业执照。</p>
    <p>5.采购文件获取</p>
    <p>5.1获取时间：2026年8月18日至2026年8月23日。</p>
    <p>5.2获取方式：登录山西焦煤电子招采平台，通过“采购执行-我要参与”栏目获取。</p>
    <p>5.3采购文件的获取：平台使用费缴后不退。</p>
    <p>5.4联系人：供应商业务联系人。</p>
    <p>5.5客服电话：4000016188-1。</p>
    <p>6.响应文件递交</p>
    """
    _, _, data, _ = SxjmParser.parse("fzxm", "cggg", detail)

    assert data["招标内容与范围"] == "本项目采购支架搬运车。"
    assert data["获取方式"] == "登录山西焦煤电子招采平台，通过“采购执行-我要参与”栏目获取"
    assert "平台使用费" not in data["获取方式"]
    assert "客服电话" not in data["获取方式"]


def test_parser_rejects_table_header_as_project_scale():
    detail = _detail(5)
    detail["content"] = "<p>项目概况：标段</p><p>采购范围：采购瓦斯测量装置。</p>"
    _, _, data, _ = SxjmParser.parse("fzxm", "cggg", detail)
    assert data["项目规模"] == ""


def test_parser_keeps_real_project_overview_table_and_three_digit_lot_scope():
    detail = _detail(5)
    detail["content"] = """
    <p>1.5采购项目概况：</p><p>标段</p><p>设备名称</p>
    <p>004 第四标段</p><p>瓦斯抽放多参数测量装置</p><p>数量：30台</p>
    <p>2.采购范围及相关要求</p>
    <p>2.1采购范围：本采购项目划分为4个标段，本次采购为其中的：</p>
    <p>004 第四标段：瓦斯抽放多参数测量装置</p>
    <p>2.2交货地点：采购人指定地点。</p>
    """

    _, _, data, _ = SxjmParser.parse("fzxm", "cggg", detail)

    assert "004 第四标段" in data["项目规模"]
    assert "瓦斯抽放多参数测量装置" in data["项目规模"]
    assert "004 第四标段：瓦斯抽放多参数测量装置" in data["招标内容与范围"]


def test_source_types_remain_distinct_while_reusing_framework_schemas():
    cases = {
        "zbgg": ("招标公告", "招标公告"),
        "cggg": ("采购公告", "招标公告"),
        "hxr": ("中标候选人公示", "中标候选人公示"),
        "cjhxr": ("成交候选人公示", "中标候选人公示"),
        "zbjg": ("中标结果公示", "中标结果公示"),
        "cjgg": ("成交公告", "中标结果公示"),
        "zzgg": ("终止公告", "更正结果公示"),
    }
    for section, (source_type, schema_type) in cases.items():
        assert config.source_notice_type(section) == source_type
        assert config.schema_notice_type(section) == schema_type

    subtype, schema_type, _, _ = SxjmParser.parse(
        "fzxm", "cjhxr", _detail(6)
    )
    assert subtype == "cjhxr"
    assert schema_type == "中标候选人公示"


def test_audit_resolves_transaction_candidate_without_losing_schema_type():
    body = "排序\n成交候选人名称\n1\n甲公司"
    record = {
        "公告ID": "1",
        "公告类型": "CANDIDATE",
        "公告子类型": "fzxm.cjhxr",
        "公告标题": "维修服务成交候选人公示",
        "详情页链接": "https://www.sxccdzzcpt.cn/home/detail?id=1",
        "项目名称": "维修服务",
        "中标候选人名称": ["甲公司"],
        "公告正文": body,
        "附件": [],
        "_trace": {
            "rawText": body,
            "rawHtml": "<p>成交候选人名称：甲公司</p>",
            "payload": {"detail": {"id": 1, "project_name": "维修服务"}},
            "fieldMeta": {
                "source_notice_type": "成交候选人公示",
                "schema_notice_type": "中标候选人公示",
                "source_announcement_type": "6",
            },
        },
    }

    result = audit_record(record, "非招项目_成交候选人公示.json")

    assert result["sourceNoticeType"] == "成交候选人公示"
    assert result["schemaNoticeType"] == "中标候选人公示"
    assert result["databaseNoticeType"] == "成交候选人公示"
    assert result["errors"] == []


def test_parser_preserves_termination_nature():
    detail = _detail(4)
    detail["title"] = "运输系统项目四次招标终止公告"
    detail["project_name"] = detail["title"]
    detail["content"] = (
        "<p>三、招标项目编号</p><p>SJZBNY02260225H058V13</p>"
        "<p>五、招标终止原因</p><p>四次招标均不满足法定开标家数。</p>"
        "<p>六、监督部门</p><p>监督部门为某行政审批局。</p>"
    )
    subtype, notice_type, data, _ = SxjmParser.parse("zbxm", "zzgg", detail)
    assert subtype == "zzgg"
    assert notice_type == "更正结果公示"
    assert data["公共类型"] == "终止公告"
    assert data["项目名称"] == "运输系统项目"
    assert data["项目编号"] == "SJZBNY02260225H058V13"
    assert data["公告内容"] == "四次招标均不满足法定开标家数。"


def test_parser_keeps_change_block_and_uses_final_opening_time():
    detail = _detail(8)
    detail["title"] = "矿井设备采购项目招标三次延期公告"
    detail["project_name"] = detail["title"]
    detail["content"] = (
        "<p>一、内容</p>"
        "<p>现将该招标项目开标时间调整如下：</p>"
        "<p>原信息内容：</p><p>开标时间：2026-08-10 09:00</p>"
        "<p>现延期为：</p><p>开标时间：2026-08-20 08:30</p>"
        "<p>二、监督部门</p><p>监督部门为某行政审批局</p>"
        "<p>三、联系方式</p><p>招标人：某煤业有限公司</p>"
    )

    _, notice_type, data, _ = SxjmParser.parse("yfxm", "zbgg", detail)

    assert notice_type == "更正结果公示"
    assert data["公共类型"] == "延期公告"
    assert data["开标时间"] == "2026-08-20 08:30"
    assert "原信息内容" in data["公告内容"]
    assert "现延期为" in data["公告内容"]
    assert "监督部门为" not in data["公告内容"]


def test_spider_defaults_and_section_validation():
    spider = SxjmSpider()
    assert spider.channels == ("yfxm", "zbxm", "fzxm", "jycg")
    assert len(spider.feeds) == 16
    assert ("yfxm", "zbgg", "1") not in spider.feeds
    assert ("yfxm", "zbgg", "8") in spider.feeds
    request = spider._list_request("zbxm", "zbgg", "1", 1)
    assert "announcement_type=1" in request.url
    assert "category=3" in request.url
    assert request.dont_filter is False

    non_tender = SxjmSpider(channels="fzxm")
    assert {feed[2] for feed in non_tender.feeds} == {"4", "5", "6", "7"}


def test_direct_mode_enables_guard_and_disables_system_proxy():
    settings = Settings(
        {
            "CRAWLER_OUTBOUND_MODE": "direct",
            "DIRECT_CONCURRENT_REQUESTS": 1,
            "DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN": 1,
            "DIRECT_DOWNLOAD_DELAY": 6.0,
            "DIRECT_RETRY_TIMES": 2,
            "DIRECT_DOWNLOAD_TIMEOUT": 90,
        }
    )
    SxjmSpider.update_settings(settings)

    middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
    assert middlewares[
        "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
    ] == 650
    assert settings.getbool("HTTPPROXY_ENABLED") is False
    assert settings.getint("CONCURRENT_REQUESTS") == 1
    assert settings.getfloat("DOWNLOAD_DELAY") == 6.0
    assert settings.getint("RETRY_TIMES") == 2
    assert settings.getint("DOWNLOAD_TIMEOUT") == 90


def test_sxjm_snapshot_and_payload_are_referenced_without_inline_duplicates(tmp_path):
    class Stats:
        def __init__(self):
            self.values = {}

        def inc_value(self, key, count=1):
            self.values[key] = self.values.get(key, 0) + count

        def get_value(self, key, default=None):
            return self.values.get(key, default)

    spider = SxjmSpider(channels="zbxm", sections="zbgg")
    crawler = SimpleNamespace(
        spider=spider,
        settings=Settings(
            {
                "NOTICE_OUTPUT_ROOT": str(tmp_path),
                "NOTICE_SNAPSHOT_ENABLED": True,
                "NOTICE_SNAPSHOT_REQUIRED": False,
                "NOTICE_EXPORT_INCLUDE_META": True,
                "NOTICE_EXPORT_DIAGNOSTICS": True,
                "NOTICE_EXPORT_TRACE": True,
            }
        ),
        stats=Stats(),
    )
    spider.crawler = crawler
    snapshot = HtmlSnapshotPipeline.from_crawler(crawler)
    schema = NoticeSchemaPipeline.from_crawler(crawler)
    exporter = SxjmMultiFormatPipeline.from_crawler(crawler)
    snapshot.open_spider()
    exporter.open_spider()
    raw_html = "<p>项目编号：SXJM-SNAPSHOT-001</p><p>招标人：测试单位</p>"
    item = spider.build_notice_item(
        notice_type="招标公告",
        notice_subtype="zbxm.zbgg",
        notice_id="snapshot-001",
        title="HTML快照测试公告",
        publish_time="2026-08-04 10:00:00",
        detail_url="https://www.sxccdzzcpt.cn/home/detail?id=snapshot-001",
        data={"项目名称": "HTML快照测试", "发布网站": config.PLATFORM_NAME},
        raw_data={"detail": {"id": "snapshot-001", "content": raw_html}},
        raw_html=raw_html,
        raw_text="项目编号：SXJM-SNAPSHOT-001\n招标人：测试单位",
    )

    item = snapshot.process_item(item)
    item = schema.process_item(item)
    exporter.process_item(item)
    exporter.close_spider()

    digest = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
    snapshot_path = tmp_path / item["snapshot_path"]
    assert snapshot_path.read_text(encoding="utf-8") == raw_html
    assert item["snapshot_sha256"] == digest
    rows = json.loads(
        (tmp_path / "sxjm/json/招标项目_招标（预审）公告.json").read_text(
            encoding="utf-8"
        )
    )
    row = rows[0]
    assert row["HTML快照路径"] == item["snapshot_path"]
    assert row["HTML快照SHA256"] == digest
    trace = row["_trace"]
    assert trace["schemaVersion"] == "2.0"
    assert "rawHtml" not in trace
    assert "rawText" not in trace
    assert "exportMetadata" not in trace
    payload_path = tmp_path / trace["payloadSnapshot"]["path"]
    assert json.loads(payload_path.read_text(encoding="utf-8"))["detail"]["id"] == "snapshot-001"


def test_content_addressed_snapshot_repairs_existing_hash_mismatch(tmp_path):
    target = tmp_path / "notice_expectedhash.html"
    target.write_bytes(b"externally modified")
    expected = b"<p>source snapshot</p>"
    digest = hashlib.sha256(expected).hexdigest()

    HtmlSnapshotPipeline._write_content_addressed(target, expected, digest)

    assert target.read_bytes() == expected
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest


def test_export_basenames_cover_all_homepage_channels():
    routes = SxjmMultiFormatPipeline.ROUTES
    assert len(routes) == 16
    assert routes["zbxm.zbgg"][0] == "招标项目_招标（预审）公告"
    assert routes["yfxm.zbjh"][0] == "依法项目_招标计划"
    assert routes["fzxm.cjhxr"][0] == "非招项目_成交候选人公示"
    assert routes["jycg.cjgg"][0] == "简易采购限额以下_成交公告"


def test_parser_marks_other_channel_nature():
    _, notice_type, data, _ = SxjmParser.parse("fzxm", "cggg", _detail(5))
    assert notice_type == "招标公告"
    assert data["项目性质"] == "非依法招标"
    assert data["源站公告性质"] == "采购（预审）公告"


def test_type_eight_uses_specific_title_nature_before_other_fallback():
    detail = _detail(8)
    detail["title"] = "设备采购补充说明"
    detail["_crawler_announcement_type"] = "8"

    _, _, data, _ = SxjmParser.parse("yfxm", "zbgg", detail)

    assert data["源站公告性质"] == "补充公告"

    detail["title"] = "设备采购说明"
    _, _, data, _ = SxjmParser.parse("yfxm", "zbgg", detail)
    assert data["源站公告性质"] == "依法项目招标（预审）及其他公告"


def test_site_parser_extracts_plan_labels_without_huaxin_adapter():
    detail = _detail(19)
    detail.update({
        "content": "<p>一、项目名称：矿井工程</p><p>二、项目总投资：1200万元</p>"
                   "<p>三、建设地点：吕梁市</p><p>四、招标方式：公开招标</p>",
        "project_name": "", "contribution_scale": "", "project_address": "",
        "tender_mode": "",
    })
    _, notice_type, data, _ = SxjmParser.parse("yfxm", "zbjh", detail)
    assert notice_type == "招标计划"
    assert data["项目名称"] == "矿井工程"
    assert data["项目总投资"] == "1200万元"
    assert data["建设地点"] == "吕梁市"
    assert data["招标方式"] == "公开招标"


def test_site_parser_extracts_candidate_and_award_tables():
    candidate = _detail(6)
    candidate["content"] = (
        "<p>项目编号：CG-001</p><table><tr><th>排序</th><th>成交候选人名称</th>"
        "<th>报价</th></tr><tr><td>1</td><td>甲公司</td><td>123万元</td></tr></table>"
    )
    _, _, candidate_data, _ = SxjmParser.parse("fzxm", "cjhxr", candidate)
    assert candidate_data["中标候选人名称"] == ["甲公司"]
    assert candidate_data["中标候选人报价"] == ["123万元"]

    award = _detail(7)
    award["content"] = (
        "<table><tr><th>序号</th><th>成交人名称</th><th>成交金额</th></tr>"
        "<tr><td>1</td><td>乙公司</td><td>88万元</td></tr></table>"
    )
    _, _, award_data, _ = SxjmParser.parse("fzxm", "cjgg", award)
    assert award_data["中标人名称"] == ["乙公司"]
    assert award_data["中标价"] == ["88万元"]


def test_site_parser_extracts_vertical_award_table_with_candidate_header():
    detail = _detail(7)
    detail["content"] = (
        "<p>一、中标人信息</p><p>标段名称：设备采购标段四</p>"
        "<p>排序</p><p>中标候选人名称</p><p>1</p>"
        "<p>中煤科工集团上海有限公司</p>"
    )

    _, _, data, _ = SxjmParser.parse("zbxm", "zbjg", detail)

    assert data["中标人名称"] == ["中煤科工集团上海有限公司"]
    assert data["中标价"] == [None]


def test_site_parser_extracts_vertical_award_table_with_unit_header():
    detail = _detail(7)
    detail["content"] = (
        "<p>一、中标结果</p><p>项目名称：科研服务项目</p>"
        "<p>序号</p><p>中标单位名称</p><p>1</p><p>中国矿业大学</p>"
    )

    _, _, data, _ = SxjmParser.parse("zbxm", "zbjg", detail)

    assert data["中标人名称"] == ["中国矿业大学"]
    assert data["中标价"] == [None]


def test_site_parser_maps_multiple_tables_to_multiple_sections():
    detail = _detail(6)
    detail["content"] = (
        "<table><tr><th>排序</th><th>成交候选人名称</th></tr>"
        "<tr><td>1</td><td>甲公司</td></tr></table>"
        "<table><tr><th>排序</th><th>成交候选人名称</th></tr>"
        "<tr><td>1</td><td>乙公司</td></tr></table>"
        "<p>标段名称：第一标段</p><p>标段名称：第二标段</p>"
    )
    _, _, data, _ = SxjmParser.parse("fzxm", "cjhxr", detail)
    assert data["中标候选人明细"] == [
        {"标段": "第一标段", "候选人名称": "甲公司", "候选人报价": ""},
        {"标段": "第二标段", "候选人名称": "乙公司", "候选人报价": ""},
    ]


def test_site_parser_keeps_inline_sections_and_embedded_contacts():
    detail = _detail(5)
    detail["content"] = (
        "<p>2.1采购范围：设备维修及调试。</p>"
        "<p>2.2服务地点：柳林县。</p>"
        "<p>3.1供应商资格要求：具有独立法人资格。</p>"
        "<p>5.采购文件的获取</p><p>5.1获取方式：平台下载。</p>"
        "<p>二、联系方式招 标 人：甲单位</p><p>地 址：太原市</p>"
        "<p>联 系 人：张先生</p><p>电 话：12345678</p>"
    )
    _, _, data, _ = SxjmParser.parse("fzxm", "cggg", detail)
    assert data["招标内容与范围"].startswith("设备维修及调试")
    assert data["申请人资格要求/投标人资格要求"].startswith("具有独立法人资格")
    assert data["项目地点"] == "柳林县"
    assert data["招标人/采购人名称"] == "甲单位"
    assert data["招标人联系人"] == "张先生"


def test_site_parser_ignores_nested_table_duplicates_and_signature_contact():
    detail = _detail(2)
    detail["content"] = (
        "<table><tr><td><table><tr><th>排序</th><th>中标候选人名称</th>"
        "</tr><tr><td>1</td><td>甲公司</td></tr></table></td></tr></table>"
        "<p>招标人：真实招标人</p><p>联系人：张先生</p>"
        "<p>招标人或其招标代理机构：代理公司（签章）</p>"
    )
    _, _, data, _ = SxjmParser.parse("zbxm", "hxr", detail)
    assert data["中标候选人名称"] == ["甲公司"]
    assert data["招标人/采购人"] == "真实招标人"


def test_site_parser_deduplicates_responsive_table_copies():
    detail = _detail(2)
    table = (
        "<table><tr><th>排序</th><th>中标候选人名称</th><th>报价</th></tr>"
        "<tr><td>1</td><td>甲公司</td><td>100万元</td></tr></table>"
    )
    compact_table = (
        "<table><tr><th>排序</th><th>中标候选人名称</th></tr>"
        "<tr><td>1</td><td>甲公司</td></tr></table>"
    )
    detail["content"] = table * 2 + compact_table + "<p>标段名称：第一标段</p>"
    _, _, data, _ = SxjmParser.parse("yfxm", "hxr", detail)
    assert data["中标候选人名称"] == ["甲公司"]
    assert data["中标候选人报价"] == ["100万元"]


def test_site_parser_supports_award_unit_and_numbered_sections():
    detail = _detail(7)
    detail["content"] = (
        "<table><tr><th>序号</th><th>成交单位</th></tr>"
        "<tr><td>1</td><td>甲公司</td></tr></table>"
        "<table><tr><th>序号</th><th>成交单位</th></tr>"
        "<tr><td>1</td><td>乙公司</td></tr></table>"
        "<p>标段001：车辆一</p><p>标段002：车辆二</p>"
    )
    _, _, data, _ = SxjmParser.parse("fzxm", "cjgg", detail)
    assert data["中标人名称"] == ["甲公司", "乙公司"]
    assert data["中标价"] == [None, None]
    assert [x["标段"] for x in data["中标结果明细"]] == ["车辆一", "车辆二"]


def test_site_parser_supports_shortlisted_award_header():
    detail = _detail(7)
    detail["content"] = (
        "<table><tr><th>排序</th><th>成交(入围)人名称</th></tr>"
        "<tr><td>1</td><td>山西大晟贸易有限公司</td></tr></table>"
    )

    subtype, schema_type, data, _ = SxjmParser.parse("fzxm", "cjgg", detail)

    assert subtype == "cjgg"
    assert schema_type == "中标结果公示"
    assert data["中标人名称"] == ["山西大晟贸易有限公司"]


def test_site_parser_handles_spaced_term_and_project_service_period():
    tender = _detail(1)
    tender["content"] = "<p>2.4工 期：合同签订后450日历天。</p>"
    _, _, tender_data, _ = SxjmParser.parse("yfxm", "zbgg", tender)
    assert tender_data["工期/服务期/供货日期"] == "合同签订后450日历天"

    purchase = _detail(5)
    purchase["content"] = "<p>2.4项目服务期限：合同签订后30个工作日内。</p>"
    _, _, purchase_data, _ = SxjmParser.parse("fzxm", "cggg", purchase)
    assert purchase_data["工期/服务期/供货日期"] == "合同签订后30个工作日内"


def test_site_parser_infers_online_opening_from_site_wording():
    detail = _detail(5)
    detail["content"] = (
        "<p>登录山西焦煤电子招采平台（入口一）在线等待谈判通知。</p>"
    )
    _, _, data, _ = SxjmParser.parse("fzxm", "cggg", detail)
    assert data["开启方式"] == "线上开启"
    assert data["开启地点"] == "山西焦煤电子招采平台"

    detail["content"] = (
        "<p>6.2开标方式：通过山西焦煤电子招采平台（入口一）线上。</p>"
    )
    _, _, data, _ = SxjmParser.parse("yfxm", "zbgg", detail)
    assert data["开启地点"] == "山西焦煤电子招采平台"


def test_site_parser_excludes_agency_signatory_from_project_manager():
    detail = _detail(7)
    detail["content"] = (
        "<p>代理机构：山西焦煤集团招标有限公司（签章）</p>"
        "<p>项目负责人：裴亮（签名）</p>"
    )
    _, _, data, _ = SxjmParser.parse("fzxm", "cjgg", detail)
    assert data["项目经理"] == ""

    detail["content"] = "<p>项目负责人：张三</p>"
    _, _, data, _ = SxjmParser.parse("fzxm", "cjgg", detail)
    assert data["项目经理"] == "张三"


def test_clean_html_keeps_paragraph_and_table_in_source_order():
    source = (
        "<p>001第一标段：设备采购</p>"
        "<table><tr><th>排序</th><th>候选人名称</th></tr>"
        "<tr><td>1</td><td>甲公司</td></tr></table>"
        "<p>二、联系方式</p>"
    )

    lines = clean_html_keep_lines(source).splitlines()

    assert lines == [
        "001第一标段：设备采购", "排序", "候选人名称",
        "1", "甲公司", "二、联系方式",
    ]


def test_clean_html_keeps_heading_nodes_used_by_source_contacts():
    source = (
        "<p>四、联系方式</p>"
        "<h2><span>招</span> <span>标</span> <span>人：甲单位</span></h2>"
        "<h2><span>地</span>&nbsp;&nbsp;<span>址：太原市</span></h2>"
        "<h2><span>联</span> <span>系</span> <span>人：张先生</span></h2>"
    )

    detail = _detail(2)
    detail["content"] = source
    _, _, data, _ = SxjmParser.parse("zbxm", "hxr", detail)

    assert clean_html_keep_lines(source).splitlines() == [
        "四、联系方式",
        "招 标 人：甲单位",
        "地 址：太原市",
        "联 系 人：张先生",
    ]
    assert data["招标人/采购人"] == "甲单位"
    assert data["招标人地址"] == "太原市"
    assert data["招标人联系人"] == "张先生"


def test_site_parser_extracts_shortlisted_supplier_tables_and_sections():
    detail = _detail(6)
    detail["content"] = (
        "<p>001 第一标段:二级截齿</p>"
        "<table><tr><th>序号</th><th>入围候选供应商名称</th></tr>"
        "<tr><td>1</td><td>甲公司</td></tr></table>"
        "<p>002 第二标段:钻头钻杆</p>"
        "<table><tr><th>序号</th><th>入围候选供应商名称</th></tr>"
        "<tr><td>1</td><td>乙公司</td></tr></table>"
    )

    _, _, data, _ = SxjmParser.parse("fzxm", "cjhxr", detail)

    assert data["中标候选人名称"] == ["甲公司", "乙公司"]
    assert data["中标候选人报价"] == [None, None]
    assert data["中标候选人明细"] == [
        {
            "标段": "001 第一标段:二级截齿",
            "候选人名称": "甲公司",
            "候选人报价": "",
        },
        {
            "标段": "002 第二标段:钻头钻杆",
            "候选人名称": "乙公司",
            "候选人报价": "",
        },
    ]


def test_site_parser_extracts_delivery_deadline_variant():
    detail = _detail(1)
    detail["content"] = "<p>2.4交货期限：签订合同后30天。</p>"

    _, _, data, _ = SxjmParser.parse("zbxm", "zbgg", detail)

    assert data["工期/服务期/供货日期"] == "签订合同后30天"


def test_site_parser_extracts_deposit_method_from_standalone_section():
    detail = _detail(1)
    detail["content"] = (
        "<p>七、提交投标保证金的形式</p>"
        "<p>本项目可以采用现金保证金或银行保函、保证保险、担保机构保函、"
        "电子保函等非现金交易担保方式提交投标保证金。</p>"
        "<p>八、提出异议的渠道和方式</p><p>通过平台提出。</p>"
    )

    _, _, data, _ = SxjmParser.parse("yfxm", "zbgg", detail)

    assert data["投标保证金方式"] == (
        "本项目可以采用现金保证金或银行保函、保证保险、担保机构保函、"
        "电子保函等非现金交易担保方式提交投标保证金。"
    )


def test_site_parser_rejects_empty_source_template_placeholders():
    detail = _detail(7)
    detail.update({
        "title": "清扫服务结果发布",
        "project_name": "",
        "tenderer": "真实采购单位",
        "content": (
            "<table><tr><th>序号</th><th>成交人名称</th></tr>"
            "<tr><td>1</td><td></td></tr></table>"
            "<p>二、其他公示内容：</p><p>无</p><p>三、联系方式</p>"
            "<p>采 购 人：</p><p>地 址：</p><p>联 系 人：</p><p>电 话：</p>"
        ),
    })

    _, _, data, _ = SxjmParser.parse("jycg", "cjgg", detail)

    assert data["中标人名称"] == []
    assert data["中标价"] == []
    assert data["中标结果明细"] == []
    assert data["招标人/采购人"] == "真实采购单位"


def test_source_nature_and_exporter_detect_misfiled_termination():
    detail = _detail(5)
    detail["title"] = "抑尘车项目采购撤销（终止）公告"

    _, notice_type, data, _ = SxjmParser.parse("fzxm", "cggg", detail)

    assert notice_type == "更正结果公示"
    assert data["公共类型"] == "撤销公告"
    assert SxjmMultiFormatPipeline._route_config(
        "__sxjm_correction_fzxm.cggg__"
    ) == ("非招项目_更正及其他公告", "更正结果公示")


def test_spider_trace_keeps_list_and_detail_transport_context():
    spider = SxjmSpider(
        channels="zbxm", sections="zbgg", max_records=1, page_size=3
    )
    list_request = spider._list_request("zbxm", "zbgg", "1", 1)
    list_payload = {
        "data": [{"id": 44849, "title": "设备采购招标公告"}],
        "total": 1,
    }
    list_response = TextResponse(
        url=list_request.url,
        request=list_request,
        encoding="utf-8",
        body=json.dumps(
            {"errcode": 0, "errmsg": "ok", "result": _encrypt(list_payload)}
        ).encode(),
    )

    detail_request = next(
        value for value in spider.parse_list(
            list_response, "zbxm", "zbgg", "1", 1
        )
        if value.callback == spider.parse_detail
    )
    list_trace = detail_request.cb_kwargs["list_record"]["_crawler_list_trace"]
    assert list_trace["responseMetadata"]["requestKind"] == "list_api"
    assert list_trace["requestParams"]["category"] == 3
    assert list_trace["pagination"]["total"] == 1
    assert "result" not in list_trace["businessEnvelope"]

    detail_response = TextResponse(
        url=detail_request.url,
        request=detail_request,
        encoding="utf-8",
        body=json.dumps(
            {"errcode": 0, "errmsg": "ok", "result": _encrypt(_detail())}
        ).encode(),
    )
    item = next(spider.parse_detail(detail_response, **detail_request.cb_kwargs))

    assert "_crawler_list_trace" not in item["raw_data"]["list"]
    assert item["raw_data"]["detail"]["id"] == 44849
    assert "transport" not in item["raw_data"]
    assert item["response_metadata"]["relatedRequests"]["list"]["requestKind"] == "list_api"
    assert item["field_meta"]["site_parser"] == spider.parser_version


def test_spider_uses_created_time_when_source_publish_time_is_unix_epoch():
    spider = SxjmSpider(
        channels="yfxm", sections="zbjh", max_records=1, page_size=3
    )
    list_record = _detail(19)
    list_record.update({
        "publish_time_format": "1970-01-01 08:00:00",
        "created_at_format": "2026-06-29 21:21:37",
    })
    detail_request = Request(config.DETAIL_URL.format(notice_id=list_record["id"]))
    detail_response = TextResponse(
        url=detail_request.url,
        request=detail_request,
        encoding="utf-8",
        body=json.dumps(
            {"errcode": 0, "errmsg": "ok", "result": _encrypt(list_record)}
        ).encode(),
    )

    item = next(spider.parse_detail(
        detail_response,
        channel="yfxm",
        section="zbjh",
        announcement_type="19",
        list_record=list_record,
        list_fingerprint="list-fingerprint",
    ))

    assert spider._record_time(list_record).year == 2026
    assert item["publish_time"].isoformat(sep=" ") == "2026-06-29 21:21:37"


def test_attachment_storage_path_handles_long_chinese_file_name():
    pipeline = NoticeFilesPipeline.__new__(NoticeFilesPipeline)
    long_name = "山西焦煤集团超长工程项目" * 15 + ".pdf"
    item = NoticeItem(
        platform_code="sxjm",
        notice_type="TERMINATION",
        notice_id="45310",
        attachments=[
            {
                "source_file_id": "54104",
                "file_name": long_name,
                "file_url": "https://www.sxccdzzcpt.cn/source.pdf",
            }
        ],
    )
    request = Request(
        "https://www.sxccdzzcpt.cn/source.pdf",
        meta={"_notice_attachment_index": 0},
    )

    path = pipeline.file_path(request, item=item)
    component = path.rsplit("/", 1)[-1]

    assert len(component.encode("utf-8")) <= 240
    assert component.startswith("54104_")
    assert component.endswith(".pdf")


def test_attachment_request_uses_dedicated_timeout_and_retry_budget():
    pipeline = NoticeFilesPipeline.__new__(NoticeFilesPipeline)
    pipeline.enabled = True
    pipeline.files_urls_field = "file_urls"
    pipeline.files_result_field = "files"
    pipeline.attachment_download_timeout = 600.0
    pipeline.attachment_retry_times = 2
    item = NoticeItem(
        detail_url="https://www.sxccdzzcpt.cn/home/detail?id=44849",
        attachments=[
            {
                "source_file_id": "53634",
                "file_name": "招标公告.pdf",
                "file_url": "https://www.sxccdzzcpt.cn/zcpt/example.pdf",
            }
        ],
        file_urls=["https://www.sxccdzzcpt.cn/zcpt/example.pdf"],
    )

    request = pipeline.get_media_requests(item, None)[0]

    assert request.meta["download_timeout"] == 600.0
    assert request.meta["max_retry_times"] == 2
    assert request.meta["allow_offsite"] is True
    assert request.headers[b"Referer"] == (
        b"https://www.sxccdzzcpt.cn/home/detail?id=44849"
    )
