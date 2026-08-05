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
    assert data["项目性质"] == "招标项目"
    assert data["源站公告性质"] == "招标（预审）公告"
    assert data["项目名称"]
    assert data["发布网站"] == config.PLATFORM_NAME
    assert attachments[0]["file_name"] == "招标公告.pdf"
    assert attachments[0]["file_url"].endswith("/zcpt/2026-07-21/example.pdf")


def test_source_types_remain_distinct_while_reusing_framework_schemas():
    cases = {
        "zbgg": ("招标公告", "招标公告"),
        "cggg": ("采购公告", "招标公告"),
        "hxr": ("中标候选人公示", "中标候选人公示"),
        "cjhxr": ("成交候选人公示", "中标候选人公示"),
        "zbjg": ("中标结果公示", "中标结果公示"),
        "cjgg": ("成交公告", "中标结果公示"),
        "zzgg": ("终止公告", "招标公告"),
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
    subtype, notice_type, data, _ = SxjmParser.parse("zbxm", "zzgg", _detail(4))
    assert subtype == "zzgg"
    assert notice_type == "招标公告"
    assert data["源站公告性质"] == "终止公告"


def test_spider_defaults_and_section_validation():
    spider = SxjmSpider()
    assert spider.channels == ("yfxm", "zbxm", "fzxm", "jycg")
    assert len(spider.feeds) == 16
    assert ("yfxm", "zbgg", "1") not in spider.feeds
    assert ("yfxm", "zbgg", "8") in spider.feeds
    request = spider._list_request("zbxm", "zbgg", "1", 1)
    assert "announcement_type=1" in request.url
    assert "category=3" in request.url

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


def test_sxjm_snapshot_matches_json_trace_and_database_export_metadata(tmp_path):
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
    assert row["_trace"]["rawHtml"] == raw_html
    assert row["_trace"]["integrity"]["rawHtmlSha256"] == digest
    assert row["_trace"]["exportMetadata"]["snapshotPath"] == item["snapshot_path"]
    assert row["_trace"]["exportMetadata"]["snapshotSha256"] == digest


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
    assert data["项目性质"] == "非招项目"
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
    assert data["中标候选人报价"] == []
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

    _, _, data, _ = SxjmParser.parse("fzxm", "cggg", detail)

    assert data["源站公告性质"] == "撤销（终止）公告"
    assert SxjmMultiFormatPipeline._is_termination("fzxm.cggg", data)


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
    assert item["raw_data"]["transport"]["list"]["pagination"]["total"] == 1
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
