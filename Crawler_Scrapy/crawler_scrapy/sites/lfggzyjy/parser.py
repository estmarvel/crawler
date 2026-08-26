"""临汾市公共资源交易平台列表与详情解析。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urljoin

from lxml import html as lxml_html

from crawler_scrapy.schemas.notice_fields import (
    canonicalize_notice_data,
    create_empty_notice_data,
)
from crawler_scrapy.sites.lfggzyjy import config


@dataclass(frozen=True)
class ListRecord:
    notice_id: str
    table_name: str
    title: str
    project_code: str
    region_code: str
    publish_time: str
    detail_url: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ParsedNotice:
    notice_type: str
    notice_subtype: str
    title: str
    publish_time: str
    data: dict[str, Any]
    raw_text: str
    validation_warnings: list[str]


class LfggzyjyParser:
    parser_version = "lfggzyjy-public-html-v1"

    @classmethod
    def parse(cls, list_record: Mapping[str, Any], html: bytes | str) -> ParsedNotice:
        record = normalize_list_record(list_record)
        raw_text = detail_text(html)
        page_text = all_text(html)
        title = detail_title(html) or record.title
        publish_time = detail_publish_time(raw_text) or detail_publish_time(page_text) or record.publish_time
        notice_type, schema_suffix = classify_notice(record.table_name, title)
        notice_subtype = f"engineering.{record.table_name}.{schema_suffix}"
        data = create_empty_notice_data(notice_type)
        data.update(
            {
                "项目名称": project_name(title),
                "项目编号": first_nonempty(
                    find_label(raw_text, ("项目代码", "投资项目统一代码", "项目编号")),
                    record.project_code,
                ),
                "招标编号": find_label(raw_text, ("招标编号", "招标项目编号")),
                "项目编号/招标编号": first_nonempty(
                    find_label(raw_text, ("招标编号", "招标项目编号", "项目编号")),
                    record.project_code,
                ),
                "招标编号/项目编号": first_nonempty(
                    find_label(raw_text, ("招标编号", "招标项目编号", "项目编号")),
                    record.project_code,
                ),
                "建设地点": find_label(raw_text, ("建设地点", "项目地点")),
                "项目地点": find_label(
                    raw_text,
                    (
                        "招标项目所在地",
                        "项目所在地",
                        "建设地点",
                        "项目地点",
                    ),
                ),
                "项目类型": find_label(raw_text, ("招标项目类型", "项目类型")),
                "招标方式": find_label(raw_text, ("招标方式",)),
                "项目总投资": find_amount(
                    raw_text,
                    ("项目总投资（万元）", "项目总投资", "总投资", "计划投资", "项目投资"),
                ),
                "项目总投资/估算金额": find_amount(
                    raw_text,
                    ("总投资", "计划投资", "项目投资"),
                ),
                "招标金额": find_amount(raw_text, ("招标金额", "本次招标金额")),
                "建设内容及规模": find_section(
                    raw_text,
                    ("建设内容及规模", "建设规模及主要内容", "建设规模", "建设内容"),
                ),
                "招标内容": find_section(raw_text, ("招标内容", "招标范围")),
                "项目规模": find_section(raw_text, ("项目规模", "建设规模")),
                "项目概况与招标范围": find_section(
                    raw_text,
                    ("项目概况和招标范围", "项目概况与招标范围"),
                ),
                "招标内容与范围": find_section(
                    raw_text,
                    ("招标内容与范围", "招标范围"),
                ),
                "申请人资格要求/投标人资格要求": find_section(
                    raw_text,
                    ("投标人资格要求", "申请人资格要求"),
                ),
                "预审文件获取时间": find_label(
                    raw_text,
                    ("获取时间", "电子招标文件获取时间", "招标文件获取时间"),
                ),
                "获取方式": find_section(raw_text, ("获取方法", "获取方式")),
                "递交截止时间": find_label(
                    raw_text,
                    ("递交截止时间", "投标文件递交截止时间", "投标截止时间"),
                ),
                "开标时间": find_label(raw_text, ("开标时间", "开启时间")),
                "公示时间": publicity_period(raw_text),
                "开启时间": find_label(raw_text, ("开标时间", "开启时间")),
                "开启地点": find_label(raw_text, ("开标地点", "开启地点")),
                "评审办法": find_label(raw_text, ("评标办法", "评审办法")),
                "资金来源": find_label(raw_text, ("资金来源", "项目资金来源")),
                "工期/服务期/供货日期": find_label(
                    raw_text,
                    ("计划工期", "工期", "服务期限", "服务期"),
                ),
                "质量要求": find_label(raw_text, ("质量标准", "质量要求")),
                "招标人名称": find_party(raw_text, ("招标人名称", "招标人", "采购人")),
                "行政监督部门": supervision_department(raw_text),
                "招标人/采购人名称": find_party(raw_text, ("招标人", "采购人")),
                "招标人/采购人": find_party(raw_text, ("招标人", "采购人")),
                "招标代理机构": find_party(raw_text, ("招标代理机构", "代理机构")),
                "中标候选人名称": candidate_names(raw_text),
                "中标候选人报价": candidate_prices(raw_text),
                "中标人名称": award_name(raw_text),
                "中标价": award_price(raw_text),
                "公告内容": raw_text,
                "发布日期": publish_time,
                "发布网站": config.PLATFORM_NAME,
                "源站公告性质": source_nature(title),
            }
        )
        data = canonicalize_notice_data(notice_type, data)
        warnings = []
        if not raw_text:
            warnings.append("detail_text_empty")
        return ParsedNotice(
            notice_type=notice_type,
            notice_subtype=notice_subtype,
            title=title,
            publish_time=publish_time,
            data=data,
            raw_text=raw_text,
            validation_warnings=warnings,
        )


def parse_list_response(value: bytes | str) -> tuple[list[dict[str, Any]], int]:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    payload = json.loads(text)
    rows = payload.get("obj") or payload.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    total = _int(payload.get("attribute") or payload.get("total") or 0)
    return [row for row in rows if isinstance(row, dict)], total


def normalize_list_record(value: Mapping[str, Any]) -> ListRecord:
    raw = dict(value)
    table_name = _text(raw.get("TABLE_NAME") or raw.get("tableName"))
    notice_id = _text(raw.get("ID") or raw.get("id"))
    source_url = _text(raw.get("URL") or raw.get("url"))
    return ListRecord(
        notice_id=notice_id,
        table_name=table_name,
        title=_text(raw.get("PROJECT_NAME") or raw.get("title") or raw.get("name")),
        project_code=_text(raw.get("PROJECT_CODE") or raw.get("projectCode")),
        region_code=_text(raw.get("REGION_CODE") or raw.get("regionCode")),
        publish_time=format_epoch_ms(raw.get("FABUPX_TIME") or raw.get("RECEIVE_TIME")),
        detail_url=config.detail_url(table_name, notice_id, source_url),
        raw=raw,
    )


def classify_notice(table_name: str, title: str) -> tuple[str, str]:
    if table_name in config.TABLE_NOTICE_TYPES:
        _, notice_type, suffix = config.TABLE_NOTICE_TYPES[table_name]
    else:
        notice_type, suffix = "招标公告", "zbgg"
    title_text = _text(title)
    if any(key in title_text for key in ("中标候选人", "成交候选人")):
        return "中标候选人公示", "hxr"
    if any(key in title_text for key in ("中标结果", "成交结果", "结果公告")):
        return "中标结果公示", "zbjg"
    if any(key in title_text for key in ("变更", "澄清", "更正", "延期", "控制价", "最高投标限价", "最高限价")):
        return "更正结果公示", "gzjg"
    if "计划" in title_text and table_name == "gcjs_tender_plan":
        return "招标计划", "zbjh"
    return notice_type, suffix


def detail_title(value: bytes | str) -> str:
    root = _document(value)
    for xpath in (
        "//*[contains(@class,'article_title')]/text()",
        "//*[contains(@class,'detail_title')]/text()",
        "//*[contains(@class,'title')]/text()",
        "//h1/text()",
        "//h2/text()",
    ):
        for item in root.xpath(xpath):
            text = _text(item)
            if text and "公共资源交易平台" not in text and text != "<!--":
                return text
    text = detail_text(value)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if any(key in line for key in ("公告", "公示", "计划")) and len(line) > 8:
            return line
    return ""


def detail_text(value: bytes | str) -> str:
    root = _document(value)
    for bad in root.xpath("//script|//style|//noscript"):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)
    candidates = []
    for selector in (
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' body_main ')]",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' plan_tbody ')]",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' class2_body ')]",
        "//*[contains(@class,'article')]",
        "//*[contains(@class,'content')]",
        "//*[contains(@class,'detail')]",
        "//*[contains(@class,'main_right')]",
    ):
        candidates.extend(root.xpath(selector))
        if candidates:
            break
    best = max(candidates or [root], key=lambda node: len("".join(node.itertext())))
    lines = []
    for text in best.itertext():
        cleaned = _text(text)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(_dedupe_adjacent(lines)).strip()


def all_text(value: bytes | str) -> str:
    root = _document(value)
    lines = []
    for text in root.itertext():
        cleaned = _text(text)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(_dedupe_adjacent(lines)).strip()


def detail_publish_time(text: str) -> str:
    match = re.search(r"发布(?:日期|时间)\s*[:：]\s*(\d{4}-\d{1,2}-\d{1,2})", text)
    if not match:
        match = re.search(r"发布时间\s*[:：]\s*(\d{4}-\d{1,2}-\d{1,2})", text)
    if match:
        return normalize_date(match.group(1))
    chinese = re.search(r"发布(?:日期|时间)\s*[:：]?\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if chinese:
        return f"{int(chinese.group(1)):04d}-{int(chinese.group(2)):02d}-{int(chinese.group(3)):02d}"
    return ""


def project_name(title: str) -> str:
    text = _text(title)
    return re.sub(
        r"(招标控制价|最高投标限价公示|最高限价公示|招标公告|变更公告|澄清公告|延期公告|中标候选人公示|中标结果公示|结果公告)$",
        "",
        text,
    ).strip()


def find_label(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*[:：]\s*([^\n]+)"
        match = re.search(pattern, text)
        if match:
            return _trim_value(match.group(1))
    return ""


def find_section(text: str, labels: tuple[str, ...], max_chars: int = 600) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*[:：]?\s*(.+?)(?=\n\s*(?:[一二三四五六七八九十]+[、.．]|\d+[、.．]|\d+\.\d+|四、|五、|六、|七、|八、|九、)|$)"
        match = re.search(pattern, text, re.S)
        if match:
            return _trim_value(match.group(1))[:max_chars]
    return ""


def publicity_period(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    match = re.search(
        r"(?:公示期|公示时间)[:：]?"
        r"(\d{4}年\d{1,2}月\d{1,2}日(?:至|-|—|－)"
        r"(?:\d{4}年)?\d{1,2}月\d{1,2}日)",
        compact,
    )
    if match:
        return f"公示期：{match.group(1)}"
    start = re.search(r"公示开始时间[:：]?(\d{4}年\d{1,2}月\d{1,2}日)", compact)
    end = re.search(r"公示结束时间[:：]?(\d{4}年\d{1,2}月\d{1,2}日)", compact)
    if start and end:
        return f"公示期：{start.group(1)}至{end.group(1)}"
    return ""


def find_amount(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        label_pattern = _field_label_pattern(label)
        for pattern in (
            rf"{label_pattern}\s*[:：]?\s*([0-9][0-9,.\s]*(?:万?元)?)",
            rf"{label_pattern}[\s\S]{{0,40}}?([0-9][0-9,.\s]*(?:万?元)?)",
        ):
            match = re.search(pattern, text)
            if match:
                return _normalize_amount(match.group(1))
    return ""


def _field_label_pattern(label: str) -> str:
    return r"\s*".join(re.escape(char) for char in label)


def _normalize_amount(value: str) -> str:
    text = re.sub(r"\s+", "", _text(value))
    return text.strip("，,。；;")


def supervision_department(text: str) -> str:
    value = find_label(text, ("行政监督部门", "监督部门"))
    if value:
        return value
    match = re.search(r"(?:本(?:项目|招标项目)的?)?监督部门(?:为|是)?\s*[:：]?\s*([^。；;\n]+)", text)
    return _trim_value(match.group(1)) if match else ""

def find_party(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        value = find_label(text, (label,))
        if value and len(value) <= 80:
            return value
    contact = re.search(r"(?:招\s*标\s*人|采购人)\s*[:：]\s*([^\n]+)", text)
    return _trim_value(contact.group(1)) if contact else ""


def candidate_names(text: str) -> list[str]:
    names = []
    for pattern in (
        r"中标候选人(?:名称)?\s*[:：]\s*([^\n；;]+)",
        r"第[一二三123]名\s*[:：]\s*([^\n，,；;]+)",
    ):
        for match in re.finditer(pattern, text):
            value = _trim_value(match.group(1))
            if value and value not in names:
                names.append(value)
    for name, _ in _candidate_table_pairs(text):
        if name and name not in names:
            names.append(name)
    return names


def candidate_prices(text: str) -> list[str]:
    prices = []
    for match in re.finditer(
        r"(?:投\s*标\s*报\s*价|报价)\s*[:：]\s*([0-9][0-9,.\s]*\s*万?元?)",
        text,
    ):
        value = _normalize_amount(match.group(1))
        if value and value not in prices:
            prices.append(value)
    for _, price in _candidate_table_pairs(text):
        if price and price not in prices:
            prices.append(price)
    return prices


def _candidate_table_pairs(text: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    pairs: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if not _rank_line(line):
            continue
        name_index = _next_company_line(lines, index + 1)
        if name_index is None:
            continue
        name, price_start = _company_block(lines, name_index)
        price = _price_after_company(lines, price_start)
        if name and (name, price) not in pairs:
            pairs.append((name, price))
    return pairs


def _rank_line(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[123]|第[一二三]名)", _text(value)))


def _next_company_line(lines: list[str], start: int) -> int | None:
    for index in range(start, min(start + 4, len(lines))):
        line = _text(lines[index])
        if _looks_like_company(line):
            return index
        if _rank_line(line):
            return None
    return None


def _company_block(lines: list[str], start: int) -> tuple[str, int]:
    parts: list[str] = []
    index = start
    while index < min(start + 4, len(lines)):
        line = _text(lines[index])
        if not _looks_like_company(line):
            break
        parts.append(_trim_value(line))
        index += 1
    return "；".join(parts), index


def _price_after_company(lines: list[str], start: int) -> str:
    tokens: list[str] = []
    for line in lines[start:min(start + 12, len(lines))]:
        cleaned = _text(line)
        if _rank_line(cleaned) or _looks_like_company(cleaned):
            break
        if any(marker in cleaned for marker in ("质量", "工期", "服务期", "供货期")):
            break
        if re.fullmatch(r"[0-9,.]+|[.]", cleaned):
            tokens.append(cleaned)
            continue
        break
    value = _normalize_amount("".join(tokens))
    if value and not value.endswith(("元", "万元")):
        value = f"{value}元"
    return value


def _looks_like_company(value: str) -> bool:
    text = _text(value)
    if len(text) < 4 or len(text) > 80:
        return False
    if any(key in text for key in ("中标候选人名称", "投标报价", "排序", "序号")):
        return False
    return bool(
        re.search(r"(?:公司|集团|事务所|联合体|中心|院|厂|合作社)(?:$|[（(）、，,])", text)
    )


def award_name(text: str) -> str:
    value = find_label(text, ("中标人", "成交供应商", "成交人"))
    if value and _looks_like_company(value):
        return value
    match = re.search(
        r"确定\s*([\s\S]{2,120}?)\s*为(?:该\s*)?(?:(?:项目|标段)\s*)?的?\s*中标人",
        text,
    )
    if match:
        for line in [item.strip() for item in match.group(1).splitlines() if item.strip()]:
            if _looks_like_company(line):
                return _trim_value(line)
    return value


def award_price(text: str) -> str:
    return find_amount(text, ("中标价", "中标价格", "成交金额", "中标金额", "投标报价"))


def source_nature(title: str) -> str:
    for key in ("变更", "澄清", "更正", "延期", "控制价", "招标计划", "中标候选人", "中标结果"):
        if key in title:
            return key
    return "公告"


def format_epoch_ms(value: Any) -> str:
    number = _int(value)
    if number <= 0:
        return ""
    if number > 10_000_000_000:
        number = number / 1000
    return datetime.fromtimestamp(number, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_date(value: str) -> str:
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if not match:
        return _text(value)
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def first_nonempty(*values: str) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _document(value: bytes | str):
    if isinstance(value, bytes):
        return lxml_html.fromstring(value)
    return lxml_html.fromstring(str(value))


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _trim_value(value: str) -> str:
    text = _text(value)
    text = re.split(
        r"\s+(?:"
        r"[一二三四五六七八九十]+[、.．]|\d+[、.．]|\d+\.\d+|"
        r"招标项目所在地|项目所在地|建设地点|项目地点|项目规模|总投资|"
        r"本次招标金额|招标金额|资金来源|招标人|采购人|招标代理机构"
        r")\s*[:：]?",
        text,
        1,
    )[0]
    return text.strip()


def _int(value: Any) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def _dedupe_adjacent(lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        if not result or result[-1] != line:
            result.append(line)
    return result
