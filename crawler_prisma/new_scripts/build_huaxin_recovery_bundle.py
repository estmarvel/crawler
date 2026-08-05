#!/usr/bin/env python3
"""Build a validated recovery bundle from exact Huaxin detail API responses."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


CRAWLER_ROOT = Path("/home/intsig/Crawler_Scrapy")
sys.path.insert(0, str(CRAWLER_ROOT))

from crawler_scrapy.sites.huaxin.parser import HuaxinParser  # noqa: E402


EXPECTED = {
    "2082271166865661952": {
        "title": "山西晋西南天然气有限责任公司2026年度水害治理及站场维修改造工程施工中标候选人公示",
        "projectCode": "E1401005129000037011",
        "projectName": "山西晋西南天然气有限责任公司2026年度水害治理及站场维修改造工程施工",
        "tenderCode": "HXZB-GC20260612",
        "section": "hxr",
        "subtype": "hxr",
        "noticeType": "中标候选人公示",
        "desiredStatus": "CANDIDATE",
    },
    "2082273418351271936": {
        "title": "山西蓝焰煤层气集团有限责任公司勘探分公司2026年钻井液泥浆材料购置中标候选人公示",
        "projectCode": "E1401005129000002382",
        "projectName": "山西蓝焰煤层气集团有限责任公司勘探分公司2026年钻井液泥浆材料购置",
        "tenderCode": "HXZB-HW20260701",
        "section": "hxr",
        "subtype": "hxr",
        "noticeType": "中标候选人公示",
        "desiredStatus": "CANDIDATE",
    },
    "2082274666353844224": {
        "title": "山西蓝焰煤层气集团有限责任公司勘探分公司2026年固井材料购置中标候选人公示",
        "projectCode": "E1401005129000002381",
        "projectName": "山西蓝焰煤层气集团有限责任公司勘探分公司2026年固井材料购置",
        "tenderCode": "HXZB-HW20260702",
        "section": "hxr",
        "subtype": "hxr",
        "noticeType": "中标候选人公示",
        "desiredStatus": "CANDIDATE",
    },
    "2081938626271305728": {
        "title": "山西蓝焰煤层气集团有限责任公司压力容器、脱硫前置过滤器购置(002标段)中标结果公示",
        "projectCode": "E1401005129000002380",
        "projectName": "山西蓝焰煤层气集团有限责任公司压力容器、脱硫前置过滤器购置",
        "tenderCode": "HXZB-HW20260626",
        "section": "gs",
        "subtype": "zbjg",
        "noticeType": "中标结果公示",
        "desiredStatus": "AWARD",
    },
}

NON_EXTRACTION_FIELDS = {
    "平台名称",
    "平台代码",
    "公告ID",
    "公告类型",
    "公告子类型",
    "公告标题",
    "发布时间",
    "公告正文",
    "公告内容",
    "解析状态",
    "内容指纹",
    "抽取方式",
    "抽取版本",
    "是否已核验",
    "爬虫时间",
    "详情页链接",
    "HTML快照路径",
    "HTML快照SHA256",
    "附件",
    "缺失字段",
}


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def parse_response(path: Path, notice_id: str) -> dict[str, Any]:
    response = json.loads(path.read_text(encoding="utf-8"))
    if response.get("code") != 200 or not isinstance(response.get("data"), dict):
        raise ValueError(f"{path}: Huaxin response is not successful")
    detail = response["data"]
    expected = EXPECTED[notice_id]
    if str(detail.get("annId") or "") != notice_id:
        raise ValueError(f"{path}: annId mismatch")
    if detail.get("annTitle") != expected["title"]:
        raise ValueError(f"{path}: title mismatch")

    subtype, notice_type, parsed_fields, attachments = HuaxinParser.parse(
        expected["section"], detail
    )
    fields = {
        key: json_value(value)
        for key, value in parsed_fields.items()
        if key not in NON_EXTRACTION_FIELDS
    }
    if subtype != expected["subtype"] or notice_type != expected["noticeType"]:
        raise ValueError(f"{path}: unexpected parsed type {subtype}/{notice_type}")
    parsed_tender_code = (
        fields.get("招标编号/项目编号")
        or fields.get("项目编号/招标编号")
        or detail.get("purDiyCode")
    )
    if parsed_tender_code != expected["tenderCode"]:
        raise ValueError(f"{path}: tender code mismatch")
    if attachments:
        raise ValueError(f"{path}: unexpected attachments require separate recovery")

    raw_html = HuaxinParser.raw_html(detail) or None
    raw_text = HuaxinParser.raw_text(detail) or None
    if not raw_html or not raw_text:
        raise ValueError(f"{path}: parsed body is empty")
    return {
        "sourceNoticeId": notice_id,
        "title": expected["title"],
        "expectedProjectCode": expected["projectCode"],
        "expectedProjectName": expected["projectName"],
        "desiredProjectStatus": expected["desiredStatus"],
        "noticeType": notice_type,
        "extractionModel": "huaxin-rule-parser",
        "extractionVersion": "huaxin-v9",
        "rawHtml": raw_html,
        "rawText": raw_text,
        "extractedFields": fields,
        "sourceTextSnippet": raw_text[:4000],
        "sourcePayload": {
            "detail_source": "primary",
            "detail": json_value(detail),
        },
        "recoverySource": "huaxin-public-detail-api",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = []
    for notice_id in EXPECTED:
        source = args.input_dir / f"huaxin_{notice_id}.json"
        if not source.is_file():
            raise FileNotFoundError(source)
        records.append(parse_response(source, notice_id))

    bundle = {
        "schemaVersion": 1,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(records)} validated records to {args.output}")


if __name__ == "__main__":
    main()
