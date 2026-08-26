from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from scrapy import Request
from scrapy.http import JsonRequest, TextResponse

from crawler_scrapy.schemas.notice_fields import canonicalize_notice_data
from crawler_scrapy.sites.runshihua import config
from crawler_scrapy.sites.runshihua import reparse_output
from crawler_scrapy.sites.runshihua.parser import RunshihuaParser
from crawler_scrapy.spiders.runshihua import RunshihuaSpider


def test_source_routes_payloads_and_namespaced_identity():
    assert config.category_for_record("notice", {"noticeType": "purchase"}) == "purchase"
    assert config.category_for_record("candidate", {"candidateType": "3"}) == "award_correction"
    assert config.category_for_record("other", {"noticeType": "delay"}) == "delay"
    assert config.source_notice_id("candidate", 1603) == "candidate:1603"
    payload = config.list_payload("candidate", 2, 999, "风电")
    assert payload["size"] == 400
    assert payload["pages"] == 2
    assert payload["platformCode"] == "100001"
    assert payload["data"]["candidateType"] == ""
    assert payload["data"]["sectionName"] == "风电"


def test_tender_uses_direct_api_fields_and_structured_body_without_pdf():
    detail = {
        "noticeName": "昔阳四期150MW风电项目设备采购-塔筒招标公告",
        "tenderingCode": "E1401000198010454006",
        "tenderingName": "昔阳四期150MW风电项目设备采购-塔筒",
        "noticeNumber": "RSH-HW-2608039",
        "projectAddress": "山西省晋中市昔阳县内",
        "fundSource": "企业自筹100.0%",
        "tenderingNode": "公开招标",
        "projectScale": "建设100MW风电场",
        "sectionNumberContentMap": {"001塔筒": "采购塔筒16套"},
        "sectionNameRequireMap": {"001塔筒": "具有钢结构制造资质"},
        "startDate": "2026-08-07 18:00:00",
        "finishDate": "2026-08-21 18:00:00",
        "endDate": "2026-08-28 10:00:00",
        "fileOpenDate": "2026-08-28 10:00:00",
        "fileOpenMethod": "网上开标",
        "tenderingContacts": "赵先生",
        "tenderingPhone": "15503684225",
        "tenderingAgencyContacts": "毛先生",
        "tenderingAgencyPhone": "0351-7210455",
        "noticePdf": "/100001/20260807/body.pdf",
    }
    parsed = RunshihuaParser.parse(
        "tender",
        detail,
        list_record={"remark": "B", "returnDate": "2026-08-07"},
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    assert data["项目编号"] == "E1401000198010454006"
    assert data["招标编号"] == "RSH-HW-2608039"
    assert data["所属行业"] == "货物"
    assert data["项目地点"] == "山西省晋中市昔阳县内"
    assert data["招标内容与范围"] == "001塔筒：采购塔筒16套"
    assert data["申请人资格要求/投标人资格要求"] == "001塔筒：具有钢结构制造资质"
    assert "项目名称：昔阳四期150MW风电项目设备采购-塔筒" in parsed.raw_text
    assert parsed.attachments[0]["file_url"].endswith("/100001/20260807/body.pdf")
    assert parsed.validation_warnings == []


def test_candidate_html_table_preserves_company_price_alignment():
    detail = {
        "candidateName": "电缆招标中标候选人公示",
        "candidateType": "0",
        "candidateNumber": "RSH-HW-2607035",
        "startDate": "2026-08-11 17:00:00",
        "endDate": "2026-08-14 17:00:00",
        "candidateUrl": "https://file.runshihua.com/files/c/100001/candidate.pdf",
        "gcjsPublicityContent": """
            <p>本电缆招标（招标项目编号：E1401000198010458001），现公示如下：</p>
            <p>1、中标候选人基本情况</p>
            <table><tr><td>排序</td><td>中标候选人名称</td><td>投标报价</td></tr>
            <tr><td>1</td><td>辽宁中德电缆有限公司</td><td>667.915885万元</td></tr>
            <tr><td>2</td><td>杭州电缆股份有限公司</td><td>640.393085万元</td></tr></table>
            <p>2、中标候选人按照招标文件要求承诺的项目负责人情况</p>
        """,
        "biddingCandidatePublicityTemplate": {
            "tenderingName": "电缆招标",
            "tenderingCode": "E1401000198010458001",
            "candidateNumber": "RSH-HW-2607035",
        },
    }
    parsed = RunshihuaParser.parse(
        "candidate", detail, list_record={"remark": "B"}
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    assert data["项目编号"] == "E1401000198010458001"
    assert data["招标编号"] == "RSH-HW-2607035"
    assert data["中标候选人名称"] == [
        "辽宁中德电缆有限公司",
        "杭州电缆股份有限公司",
    ]
    assert [str(value) for value in data["中标候选人报价"]] == [
        "6679158.85",
        "6403930.85",
    ]
    assert [row["候选人名称"] for row in parsed.data["中标候选人明细"]] == data[
        "中标候选人名称"
    ]
    assert "中标候选人明细" not in data


def test_award_and_delay_use_correct_schema_and_identifiers():
    award = RunshihuaParser.parse(
        "award",
        {
            "candidateName": "开关柜中标结果公示",
            "candidateNumber": "RSH-HW-2605031",
            "startDate": "2026-07-30 14:30:00",
            "candidateUrl": "https://file.runshihua.com/files/c/100001/award.pdf",
            "gcjsPublicityContent": """
                <p>本项目（招标项目编号：E1401000198010454001）</p>
                <p>中标人：山东泰开成套电器有限公司 中标价格：102.7731万元</p>
            """,
        },
        list_record={"remark": "B"},
    )
    award_data = canonicalize_notice_data(award.notice_type, award.data)
    assert award.notice_type == "中标结果公示"
    assert award_data["中标人名称"] == ["山东泰开成套电器有限公司"]
    assert str(award_data["中标价"][0]) == "1027731.00"

    delay = RunshihuaParser.parse(
        "delay",
        {
            "noticeName": "送出线路施工一次延期公告",
            "noticeNumber": "RSD-GC-26072001",
            "tenderingName": "送出线路施工招标公告",
            "noticeContent": "开标时间延期至2026年8月14日10:00",
            "createDate": "2026-08-07 16:41:04",
            "pdfUrl": "/100001/20260807/delay.pdf",
        },
        list_record={"remark": "A"},
    )
    delay_data = canonicalize_notice_data(delay.notice_type, delay.data)
    assert delay.notice_type == "更正结果公示"
    assert delay_data["公共类型"] == "延期公告"
    assert delay_data["项目名称"] == "送出线路施工"
    assert delay_data["招标编号"] == "RSD-GC-26072001"
    assert delay_data["公告内容"] == "开标时间延期至2026年8月14日10:00"


def test_correction_prefers_real_list_project_and_current_notice_date():
    parsed = RunshihuaParser.parse(
        "control_price",
        {
            "noticeName": "不分标段控制价公告",
            "tenderingName": "不分标段招标公告",
            "noticeNumber": "ZKGC2021-06-058",
            "releaseDate": "2021-12-06 15:00:00",
            "createDate": "2021-12-13 11:45:03",
            "noticeContent": "燃料公司宿舍片区改造项目控制价为100万元",
        },
        list_record={
            "tenderingName": "燃料公司宿舍片区改造项目10kV箱变新建工程",
            "createDate": "2021-12-13 00:00:00",
        },
    )
    data = canonicalize_notice_data(parsed.notice_type, parsed.data)
    assert data["项目名称"] == "燃料公司宿舍片区改造项目10kV箱变新建工程"
    assert data["发布日期"].strftime("%Y-%m-%d") == "2021-12-13"


def test_wrapped_candidate_names_and_proposed_award_price_are_recovered():
    candidate_text = """
1、中标候选人基本情况
排 中标候选人名称 响应报价 质量 工期/交货期
序
智弘科技（广东）股
1                    144.3883(万元)   合格 40天
份有限公司
佛山市鑫诺家具有限
2                    139.5337(万元)   合格 40天
公司
2、中标候选人按照招标文件要求承诺的项目负责人情况
排 中标候选人名称 项目负责人姓名 相关证书名称及编号
序
1       智弘科技（广东）股份有限公司          唐海华         /
2           佛山市鑫诺家具有限公司          朱旺         /
3、中标候选人响应招标文件要求的资格能力条件
"""
    candidate = RunshihuaParser.parse(
        "candidate",
        {"candidateName": "办公家具中标候选人公示"},
        pdf_text=candidate_text,
    )
    data = canonicalize_notice_data(candidate.notice_type, candidate.data)
    assert data["中标候选人名称"] == [
        "智弘科技（广东）股份有限公司",
        "佛山市鑫诺家具有限公司",
    ]
    assert [str(value) for value in data["中标候选人报价"]] == [
        "1443883.00",
        "1395337.00",
    ]

    award = RunshihuaParser.parse(
        "award",
        {"candidateName": "办公家具中标结果公示"},
        pdf_text=(
            "中标人：智弘科技（广东）股份有限公司\n"
            "拟中标价格：144.3883 万元\n"
        ),
    )
    award_data = canonicalize_notice_data(award.notice_type, award.data)
    assert str(award_data["中标价"][0]) == "1443883.00"


class _Stats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key):
        self.values[key] = self.values.get(key, 0) + 1


def _response(url: str, payload: dict, request: Request | None = None) -> TextResponse:
    request = request or Request(url)
    return TextResponse(
        url=url,
        request=request,
        encoding="utf-8",
        body=json.dumps(payload, ensure_ascii=False).encode(),
    )


def test_spider_list_detail_and_snapshot_payload_contract():
    spider = RunshihuaSpider(
        categories="candidate", max_records=1, page_size=100, parse_pdf=False
    )
    spider.crawler = SimpleNamespace(stats=_Stats())
    spider.check_notice_candidate = lambda **kwargs: (True, "list-fingerprint")
    list_record = {
        "id": 1603,
        "sectionId": 1309,
        "candidateType": "0",
        "sectionName": "电缆招标中标候选人公示",
        "startDate": "2026-08-11 17:00:00",
        "remark": "B",
    }
    outputs = list(spider.parse_list(
        _response(
            config.endpoint("candidate", "list"),
            {"code": "RESP200", "data": {"total": 1, "list": [list_record]}},
        ),
        "candidate",
        1,
    ))
    assert len(outputs) == 1
    detail_request = outputs[0]
    assert isinstance(detail_request, JsonRequest)
    assert detail_request.cb_kwargs["category"] == "candidate"

    detail = {
        "candidateName": "电缆招标中标候选人公示",
        "candidateNumber": "RSH-HW-2607035",
        "startDate": "2026-08-11 17:00:00",
        "candidateUrl": "https://file.runshihua.com/files/c/100001/candidate.pdf",
        "gcjsPublicityContent": (
            "<p>本项目（招标项目编号：E1401000198010458001）</p>"
        ),
        "biddingCandidatePublicityTemplate": {"tenderingName": "电缆招标"},
    }
    items = list(spider.parse_detail(
        _response(
            config.endpoint("candidate", "detail"),
            {"code": "RESP200", "data": detail},
            detail_request,
        ),
        **detail_request.cb_kwargs,
    ))
    assert len(items) == 1
    item = items[0]
    assert item["notice_id"] == "candidate:1603"
    assert item["notice_type"] == "CANDIDATE"
    assert item["attachments"][0]["file_type"] == "application/pdf"
    assert "gcjsPublicityContent" not in item["raw_data"]["detail"]
    assert item["raw_data"]["htmlFields"]["gcjsPublicityContent"]
    assert item["raw_html"].startswith("<p>")


def test_downloaded_pdf_is_reparsed_from_verified_payload(tmp_path, monkeypatch):
    payload = {
        "sourceFamily": "notice",
        "sourceCategory": "tender",
        "list": {"id": "20", "noticeType": "bidding", "remark": "B"},
        "detail": {
            "noticeName": "测试设备招标公告",
            "tenderingName": "测试设备",
            "releaseDate": "2026-08-11 10:00:00",
        },
        "htmlFields": {},
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    payload_path = tmp_path / "runshihua" / "payloads" / "20.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_bytes(payload_bytes)
    pdf_path = tmp_path / "runshihua" / "attachments" / "20.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-fake")
    row = {
        "公告ID": "notice:20",
        "项目编号": "",
        "招标编号": "",
        "附件": [{
            "file_name": "测试设备招标公告.pdf",
            "file_type": "application/pdf",
            "storage_path": "runshihua/attachments/20.pdf",
            "file_hash": "abc",
            "file_size_bytes": 9,
            "parse_status": "DOWNLOADED_NO_OCR",
        }],
        "_trace": {
            "payloadSnapshot": {
                "path": "runshihua/payloads/20.json",
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
            },
            "fieldMeta": {},
            "responseMetadata": {},
        },
    }
    monkeypatch.setattr(
        reparse_output,
        "extract_pdf_text",
        lambda *_args, **_kwargs: (
            "测试设备招标公告\n招标项目编号：E1401000198010999001\n"
            "招标编号：RSH-HW-2608999\n招标人：甲有限公司\n"
            "本公告包含足够的公开正文文字用于验证下载后离线解析和字段回填流程。" * 3
        ),
    )
    changed, status = reparse_output._update_row(row, tmp_path)
    assert changed is True
    assert status == "TEXT_EXTRACTED"
    assert row["项目编号"] == "E1401000198010999001"
    assert row["招标编号"] == "RSH-HW-2608999"
    assert row["附件"][0]["parse_status"] == "TEXT_EXTRACTED"
    assert row["_trace"]["fieldMeta"]["offlinePdfReparse"] is True


def test_pdf_without_text_layer_still_reparses_api_payload(tmp_path, monkeypatch):
    payload = {
        "sourceFamily": "other",
        "sourceCategory": "control_price",
        "list": {
            "id": "21",
            "noticeType": "controlPrice",
            "tenderingName": "真实项目名称",
            "createDate": "2026-08-11 10:00:00",
        },
        "detail": {
            "noticeName": "不分标段控制价公告",
            "tenderingName": "不分标段招标公告",
            "noticeNumber": "ZB-21",
        },
        "htmlFields": {},
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    payload_path = tmp_path / "runshihua" / "payloads" / "21.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_bytes(payload_bytes)
    pdf_path = tmp_path / "runshihua" / "attachments" / "21.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-image-only")
    row = {
        "公告ID": "other:21",
        "项目名称": "不分标段",
        "附件": [{
            "file_name": "控制价公告.pdf",
            "file_type": "application/pdf",
            "storage_path": "runshihua/attachments/21.pdf",
            "parse_status": "DOWNLOADED_NO_OCR",
        }],
        "_trace": {
            "payloadSnapshot": {
                "path": "runshihua/payloads/21.json",
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
            },
            "fieldMeta": {},
            "responseMetadata": {},
        },
    }
    monkeypatch.setattr(reparse_output, "extract_pdf_text", lambda *_a, **_k: "")
    changed, status = reparse_output._update_row(row, tmp_path)
    assert changed is True
    assert status == "PDF_TEXT_LAYER_UNUSABLE"
    assert row["项目名称"] == "真实项目名称"
    assert row["招标编号"] == "ZB-21"
    assert row["抽取版本"] == RunshihuaParser.parser_version
    assert row["附件"][0]["parse_status"] == "DOWNLOADED_NO_OCR"
