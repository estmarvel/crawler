"""Shanxi government procurement list/detail JSON parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Any, Mapping

from lxml import etree, html as lxml_html

from crawler_scrapy.sites.sxzfcg import config


@dataclass(frozen=True)
class ListRecord:
    article_id: str
    title: str
    publish_time: str
    source_category: str
    source_type: str
    purchaser: str
    purchase_method: str
    district_name: str
    project_code: str
    project_name: str


@dataclass
class ParsedNotice:
    notice_type: str
    title: str
    publish_time: str
    raw_html: str
    raw_text: str
    data: dict[str, Any]
    attachments: list[dict[str, Any]]
    structured: dict[str, Any]
    validation_warnings: list[str]


def _space(value: Any) -> str:
    text = unescape(str(value or "")).replace("\xa0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _compact_label(value: Any) -> str:
    return re.sub(r"[\s\xa0\u3000（）()：:]+", "", str(value or ""))


def _date_from_millis(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        from datetime import datetime

        return datetime.fromtimestamp(int(value) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _value(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def clean_html(value: str) -> str:
    source = str(value or "")
    if not source:
        return ""
    source = re.sub(r"<br\s*/?>", "\n", source, flags=re.I)
    source = re.sub(
        r"</(?:p|div|li|tr|h[1-6]|section|article|table)>",
        "\n",
        source,
        flags=re.I,
    )
    try:
        root = lxml_html.fromstring(source)
        for node in root.xpath("//style|//script|//noscript"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
        text = root.text_content()
    except (TypeError, ValueError):
        text = re.sub(r"<[^>]+>", " ", source)
    text = unescape(text).replace("\xa0", " ").replace("\u3000", " ")
    lines = [_space(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def parse_list_records(value: Mapping[str, Any], category: str) -> list[ListRecord]:
    result = value.get("result") if isinstance(value, Mapping) else {}
    data = result.get("data") if isinstance(result, Mapping) else {}
    records = []
    if isinstance(data, Mapping):
        records = data.get("data") or data.get("children") or []
    if not isinstance(records, list):
        return []
    parsed: list[ListRecord] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        article_id = _value(record, "articleId")
        if not article_id:
            continue
        publish_time = _value(record, "publishDateString", "publishTime")
        if not publish_time:
            publish_time = _date_from_millis(record.get("pubDate") or record.get("publishDate"))
        parsed.append(ListRecord(
            article_id=article_id,
            title=_value(record, "title"),
            publish_time=publish_time,
            source_category=category,
            source_type=_value(record, "typeName", "announcementTypeName"),
            purchaser=_value(record, "purchaseName", "purchaserOrgName"),
            purchase_method=_value(record, "purchaseMethod"),
            district_name=_value(record, "districtName"),
            project_code=_value(record, "projectCode"),
            project_name=_value(record, "projectName"),
        ))
    return parsed


def _detail_data(value: Mapping[str, Any]) -> Mapping[str, Any]:
    result = value.get("result") if isinstance(value, Mapping) else {}
    data = result.get("data") if isinstance(result, Mapping) else {}
    return data if isinstance(data, Mapping) else {}


def _label(text: str, *labels: str) -> str:
    normalized_labels = {_compact_label(label) for label in labels}
    for line in str(text or "").splitlines():
        if "：" not in line and ":" not in line:
            continue
        key, value = re.split(r"[：:]", line, 1)
        key_compact = _compact_label(re.sub(r"[（(].*?[）)]", "", key))
        if key_compact in normalized_labels:
            return _space(value).strip(" 。；;")

    compact = str(text or "")
    for label in labels:
        pattern = (
            rf"{re.escape(label)}(?:[（(][^）)]*[）)])?\s*[：:]\s*"
            rf"(.+?)(?=\n\s*[\u4e00-\u9fa5A-Za-z0-9（）()、 ,，/]+?\s*[：:]|"
            rf"\n[一二三四五六七八九十\d]+[、.．]|\Z)"
        )
        matched = re.search(pattern, compact, re.S)
        if matched:
            return _space(matched.group(1)).strip(" 。；;")
    return ""


def _section(text: str, heading: str, *next_headings: str) -> str:
    lines = str(text or "").splitlines()
    start = -1
    heading_compact = _compact_label(heading)
    for index, line in enumerate(lines):
        if heading_compact in _compact_label(line):
            start = index + 1
            break
    if start < 0:
        return ""
    stop = len(lines)
    next_values = [_compact_label(value) for value in next_headings]
    for index in range(start, len(lines)):
        compact = _compact_label(lines[index])
        if any(value and value in compact for value in next_values):
            stop = index
            break
    return "\n".join(lines[start:stop]).strip()


def _section_label(text: str, heading: str, labels: tuple[str, ...], *next_headings: str) -> str:
    part = _section(text, heading, *next_headings)
    return _label(part, *labels) if part else ""


def _amount(text: str, *labels: str) -> str:
    value = _label(text, *labels)
    if not value:
        return ""
    matched = re.search(r"[-+]?\d[\d,，]*(?:\.\d+)?\s*(?:亿元|万元|元)?", value)
    return matched.group(0).replace(",", "").replace("，", "") if matched else value


def _project_name(title: str, detail: Mapping[str, Any], text: str) -> str:
    direct = _value(detail, "projectName", "projectNameAll")
    if direct:
        return direct
    labelled = _label(text, "项目名称", "采购项目名称")
    if labelled:
        return labelled
    cleaned = re.sub(
        r"(?:的)?(?:公开招标|竞争性磋商|竞争性谈判|询价|单一来源|框架协议)?"
        r"(?:采购)?(?:公告|结果公告|成交公告|中标公告|更正公告|变更公告|合同公告)$",
        "",
        str(title or "").strip(),
    )
    return cleaned.strip(" ：:")


def _notice_type(category: str, detail: Mapping[str, Any], title: str) -> str:
    text = "".join(str(x) for x in detail.get("categoryNames") or [])
    compact = f"{title}{text}"
    if category == "contract" or "合同" in compact:
        return "合同与履约"
    if category == "change" or any(x in compact for x in ("更正", "变更", "澄清")):
        return "更正结果公示"
    if category == "award" or any(x in compact for x in ("结果公告", "成交公告", "中标公告")):
        return "中标结果公示"
    return "招标公告"


def _attachments(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    domain = ""
    attachment_vo = detail.get("attachmentVO")
    if isinstance(attachment_vo, Mapping):
        domain = _value(attachment_vo, "domain")
        value = attachment_vo.get("attachments")
        if isinstance(value, list):
            candidates.extend(value)
    for key in ("attachmentVO", "attachmentList", "attachments", "fileList"):
        value = detail.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, Mapping):
            candidates.append(value)
    result: list[dict[str, Any]] = []
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, Mapping):
            continue
        file_name = _value(item, "fileName", "name", "attachmentName", "displayName")
        file_url = _value(item, "fileUrl", "url", "downloadUrl", "downloadURL")
        file_id = _value(item, "fileId", "id", "attachmentId")
        if not file_url and domain and file_id:
            file_url = f"{domain.rstrip('/')}/{file_id.lstrip('/')}"
        if not file_name and not file_url and not file_id:
            continue
        result.append({
            "file_name": file_name or f"附件{index}",
            "file_url": file_url,
            "source_file_id": file_id,
            "source_type": _value(item, "type", "fileType"),
        })
    return result


class SxzfcgParser:
    parser_version = "sxzfcg-v1-zcy-json-html-rule"

    @classmethod
    def parse(
        cls,
        category: str,
        value: Mapping[str, Any],
        *,
        list_record: Mapping[str, Any] | None = None,
    ) -> ParsedNotice:
        detail = _detail_data(value)
        record = dict(list_record or {})
        title = _value(detail, "title") or _value(record, "title")
        publish_time = (
            _date_from_millis(detail.get("publishDate"))
            or _value(record, "publish_time")
        )
        raw_html = _value(detail, "content")
        raw_text = clean_html(raw_html)
        notice_type = _notice_type(category, detail, title)
        project_name = _project_name(title, detail, raw_text)
        project_code = _value(detail, "projectCode") or _value(record, "project_code")
        purchaser = (
            _value(detail, "purchaseName", "purchaserOrgName")
            or _value(record, "purchaser")
            or _label(raw_text, "采购人", "招标人", "采购单位")
        )
        method = _value(detail, "purchaseMethod") or _value(record, "purchase_method")
        data: dict[str, Any] = {
            "项目名称": project_name,
            "项目编号": project_code or _label(raw_text, "项目编号", "采购项目编号"),
            "招标编号": _label(raw_text, "招标编号", "采购编号", "代理编号"),
            "发布日期": publish_time,
            "发布网站": config.PLATFORM_NAME,
        }
        if notice_type in {"招标公告", "资格预审公告"}:
            owner_section = _section(
                raw_text,
                "采购人信息",
                "采购代理机构信息",
                "项目联系方式",
                "附件信息",
            )
            agency_section = _section(
                raw_text,
                "采购代理机构信息",
                "项目联系方式",
                "附件信息",
            )
            project_section = _section(raw_text, "项目联系方式", "附件信息")
            data.update({
                "招标方式": method,
                "招标人/采购人名称": purchaser,
                "项目地点": _value(record, "district_name") or _label(raw_text, "地点", "项目地点"),
                "招标金额": _amount(raw_text, "预算金额", "采购预算", "最高限价"),
                "获取方式": _label(raw_text, "获取方式"),
                "预审文件获取时间": _label(raw_text, "时间", "获取采购文件时间", "获取招标文件时间"),
                "递交截止时间": _label(raw_text, "提交投标文件截止时间", "响应文件提交截止时间"),
                "开启时间": _label(raw_text, "开标时间", "开启时间"),
                "开启地点": _label(raw_text, "开标地点", "开启地点"),
                "申请人资格要求/投标人资格要求": _label(raw_text, "申请人的资格要求", "投标人资格要求"),
                "招标内容与范围": _label(raw_text, "采购需求", "招标范围"),
                "招标人地址": _label(owner_section, "地址", "地 址"),
                "招标人联系人": _label(project_section, "项目联系人", "联系人"),
                "招标人联系方式": _label(owner_section, "联系方式", "电话", "电 话"),
                "招标代理机构": _label(agency_section, "名称", "名 称"),
                "招标代理机构地址": _label(agency_section, "地址", "地 址"),
                "招标代理机构联系人": _label(project_section, "项目联系人", "联系人"),
                "招标代理机构联系方式": (
                    _label(project_section, "电话", "电 话")
                    or _label(agency_section, "联系方式", "电话", "电 话")
                ),
            })
        elif notice_type == "中标结果公示":
            supplier = _label(raw_text, "中标供应商", "成交供应商", "供应商名称", "中标人")
            amount = _label(raw_text, "中标金额", "成交金额", "中标价", "成交价")
            owner_section = _section(raw_text, "采购人信息", "采购代理机构信息", "项目联系方式", "附件信息")
            agency_section = _section(raw_text, "采购代理机构信息", "项目联系方式", "附件信息")
            data.update({
                "招标方式": method,
                "招标人/采购人": purchaser,
                "中标人名称": [supplier] if supplier else [],
                "中标价": [amount] if amount else [],
                "招标人地址": _label(owner_section, "地址", "地 址"),
                "招标人联系方式": _label(owner_section, "联系方式", "电话", "电 话"),
                "招标代理机构": _label(agency_section, "名称", "名 称"),
                "招标代理机构地址": _label(agency_section, "地址", "地 址"),
                "招标代理机构联系方式": _label(agency_section, "联系方式", "电话", "电 话"),
            })
        elif notice_type == "更正结果公示":
            data.update({
                "公共类型": "变更公告",
                "公告内容": raw_text,
            })
        elif notice_type == "合同与履约":
            data.update({
                "合同名称": _label(raw_text, "合同名称") or title,
                "招标人名称": purchaser,
                "中标人名称": _label(raw_text, "供应商", "乙方"),
                "合同金额": _label(raw_text, "合同金额"),
                "合同主要内容": raw_text,
            })
        warnings: list[str] = []
        if not raw_text:
            warnings.append("BODY_TEXT_MISSING")
        if not data.get("项目编号"):
            warnings.append("PROJECT_CODE_MISSING")
        return ParsedNotice(
            notice_type=notice_type,
            title=title,
            publish_time=publish_time,
            raw_html=raw_html,
            raw_text=raw_text,
            data=data,
            attachments=_attachments(detail),
            structured=dict(detail),
            validation_warnings=warnings,
        )


__all__ = ["ListRecord", "ParsedNotice", "SxzfcgParser", "parse_list_records", "clean_html"]
