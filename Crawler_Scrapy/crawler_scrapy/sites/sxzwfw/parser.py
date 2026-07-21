"""山西省公共资源交易平台 HTML 纯数据解析器。

页面正文经常由 PDF 转换器生成：同一视觉行被拆成多个 ``span``。本解析器按
``.stl_01`` 视觉行拼接可见文字，并删除隐藏签章模板，避免标签被错误换行或角色
边界被隐藏文本污染。模块不发送请求、不写文件，也不调用 AI。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from html import unescape
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

from lxml import etree, html as lxml_html

from crawler_scrapy.schemas.notice_fields import (
    canonicalize_notice_data,
    create_empty_notice_data,
)
from crawler_scrapy.sites.huaxin.parser import (
    award_details,
    candidate_details,
    candidate_name_price_pairs,
)
from crawler_scrapy.sites.sxzwfw import config


BLOCK_TAGS = frozenset(
    {"p", "div", "li", "tr", "table", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
)
FILE_SUFFIX_RE = re.compile(
    r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|ofd)(?:$|[?#])",
    re.I,
)


@dataclass
class ParsedNotice:
    subtype: str
    notice_type: str
    title: str
    publish_time: str
    source_nature: str
    raw_text: str
    data: dict[str, Any]
    attachments: list[dict[str, Any]]
    cms_attachment: dict[str, Any] | None


def _string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _space(value: Any) -> str:
    text = unescape(_string(value)).replace("\xa0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_document(value: bytes | str):
    if isinstance(value, bytes):
        # 该站页面和保存样本均为 UTF-8；显式解码可兼容缺少 meta charset 的详情片段，
        # 避免 lxml 按 ISO-8859-1 猜测后把中文标题解析成乱码。
        return lxml_html.fromstring(value.decode("utf-8", errors="replace"))
    return lxml_html.fromstring(str(value or "<html></html>"))


def _remove_invisible(root) -> None:
    hidden_xpath = (
        ".//script|.//style|.//noscript|.//object|.//embed|"
        ".//*[contains(translate(@style,'ABCDEFGHIJKLMNOPQRSTUVWXYZ ',"
        "'abcdefghijklmnopqrstuvwxyz'),'display:none')]|"
        ".//*[contains(translate(@style,'ABCDEFGHIJKLMNOPQRSTUVWXYZ ',"
        "'abcdefghijklmnopqrstuvwxyz'),'visibility:hidden')]"
    )
    for node in list(root.xpath(hidden_xpath)):
        if node.getparent() is not None:
            node.drop_tree()


def _plain_block_text(root) -> str:
    chunks: list[str] = []

    def walk(node) -> None:
        if node.text:
            chunks.append(node.text)
        for child in node:
            tag = child.tag.lower() if isinstance(child.tag, str) else ""
            if tag == "br":
                chunks.append("\n")
            else:
                walk(child)
            if tag in BLOCK_TAGS:
                chunks.append("\n")
            if child.tail:
                chunks.append(child.tail)

    walk(root)
    lines = [_space(line) for line in "".join(chunks).splitlines()]
    return "\n".join(line for line in lines if line)


def visible_content_text(html_value: bytes | str) -> str:
    """按页面视觉行重建正文，忽略隐藏模板和图层对象。"""

    try:
        document = _parse_document(html_value)
    except (etree.ParserError, ValueError):
        return ""
    roots = document.cssselect(".cs_xq_content")
    content = roots[0] if roots else document
    _remove_invisible(content)

    visual_lines = content.cssselect(".stl_01")
    if visual_lines:
        lines: list[str] = []
        for line in visual_lines:
            value = _space("".join(line.itertext()))
            # PDF 转换模板末尾的签名/盖章占位会把大段文字设为隐藏，仅留下残缺的
            # “招标人或其招标代理机……责人”。它不是公告正文，也不能参与角色提取。
            if value and not (
                value.startswith("招标人或其招标代理机")
                and ("（签名）" in value or "（盖章）" in value)
            ):
                lines.append(value)
        return "\n".join(lines)
    return _plain_block_text(content)


def _first_text(document, selector: str) -> str:
    nodes = document.cssselect(selector)
    return _space("".join(nodes[0].itertext())) if nodes else ""


def _flexible_label(label: str) -> str:
    return r"\s*".join(re.escape(char) for char in re.sub(r"\s+", "", label))


def _label_value(text: str, labels: Iterable[str]) -> str:
    for line in text.splitlines():
        for label in labels:
            match = re.search(
                rf"(?:^|\s){_flexible_label(label)}\s*[：:]\s*(.+)$",
                line,
            )
            if match:
                return _space(match.group(1)).strip("；;。")
    return ""


def _label_paragraph(
    text: str,
    labels: Iterable[str],
    *,
    prefer_longest: bool = False,
) -> str:
    """提取被 PDF 视觉换行拆开的标签值，并在下一字段/段落处停止。

    这里只连接同一自然句的物理换行，不改写 ``raw_text``。这样既保留原始快照
    的可核验性，又避免“质量要求”“获取方式”等字段只保存第一行。
    """

    lines = [_space(line) for line in text.splitlines() if _space(line)]
    values: list[str] = []
    field_boundary = re.compile(
        r"^(?:[一二三四五六七八九十百]+[、.．]|"
        r"\d+(?:\.\d+)+\s+|\d+[、.．]\s*)"
    )
    generic_label = re.compile(
        r"^[^：:\n]{0,20}(?:时间|方式|方法|名称|编号|金额|规模|范围|期限|"
        r"要求|标准|地点|地址|联系人|电话|邮箱|来源|内容|类型|行业分类|"
        r"招标人|采购人|代理机构)\s*[：:]"
    )
    for index, line in enumerate(lines):
        for label in labels:
            match = re.search(
                rf"(?:^|\s){_flexible_label(label)}\s*[：:]\s*(.+)$",
                line,
            )
            if not match:
                continue
            first_value = _space(match.group(1)).strip("；;。")
            # 页面模板有时输出“获取方式：4.2 获取方式：……”，去掉同名的重复
            # 编号标签，避免把排版编号误存为业务内容。
            first_value = re.sub(
                rf"^\d+(?:\.\d+)*\s*{_flexible_label(label)}\s*[：:]\s*",
                "",
                first_value,
            )
            parts = [first_value]
            cursor = index + 1
            while cursor < len(lines):
                following = lines[cursor]
                if field_boundary.match(following) or generic_label.match(following):
                    break
                parts.append(following)
                cursor += 1
            value = _space(" ".join(part for part in parts if part)).strip("；;")
            if value:
                values.append(value)
    if not values:
        return ""
    return max(values, key=len) if prefer_longest else values[0]


def _time_interval(
    text: str,
    *,
    whole_labels: Iterable[str],
    start_labels: Iterable[str],
    end_labels: Iterable[str],
) -> str:
    whole = _label_value(text, whole_labels)
    start = _label_value(text, start_labels)
    end = _label_value(text, end_labels)
    if start and end:
        return f"{start} 至 {end}"
    return whole or start or end


def _section(text: str, starts: Iterable[str], stops: Iterable[str]) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    begin = -1
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if any(start in compact for start in starts):
            begin = index
            break
    if begin < 0:
        return ""
    result = [lines[begin]]
    for line in lines[begin + 1 :]:
        compact = re.sub(r"\s+", "", line)
        if any(stop in compact for stop in stops):
            break
        result.append(line)
    return "\n".join(result)


def _join_distinct(*values: Any) -> str:
    result: list[str] = []
    for value in values:
        normalized = _space(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return "|".join(result)


def _source_nature(section: str, title: str) -> str:
    rules = (
        ("控制价变更", "控制价变更"),
        ("最高投标限价", "控制价"),
        ("招标控制价", "控制价"),
        ("更正", "更正"),
        ("答疑", "答疑"),
        ("变更", "变更"),
        ("澄清", "澄清"),
        ("延期", "延期"),
        ("终止", "终止"),
        ("废标", "废标"),
        ("撤销", "撤销"),
        ("重新招标", "重新"),
        ("二次招标", "再次"),
    )
    default_nature = "更正" if section == "bg" else "正常"
    nature = next((label for keyword, label in rules if keyword in title), default_nature)
    channel_id, channel_name = config.SECTION_CHANNELS.get(section, ("", section))
    return f"{nature}（{channel_name},channelId={channel_id}）"


def _clean_project_name(title: str) -> str:
    value = _space(title)
    suffixes = (
        "更正中标结果公示", "撤销中标结果公示", "中标结果公示",
        "更正中标候选人公示", "撤销中标候选人公示", "中标候选人公示",
        "定标候选人公示", "资格预审公告", "资审公告", "招标计划变更公告",
        "招标计划", "招标公告撤销公告", "招标撤销公告", "重新招标公告",
        "招标公告", "更正公告", "变更公告", "澄清公告", "延期公告",
        "终止公告", "废标公告", "撤销公告", "招标控制价变更", "招标控制价",
        "最高投标限价", "最高限价", "合同公告", "履约公告",
        "评标结果公示", "成交结果公告", "中标公告", "结果公告",
    )
    previous = None
    while value and value != previous:
        previous = value
        for suffix in suffixes:
            if value.endswith(suffix):
                value = value[: -len(suffix)].strip(" -—_（）()")
                break
    return value or _space(title)


def _detect_type(section: str, title: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", "", title)
    correction = any(word in compact for word in ("更正", "变更", "撤销", "澄清"))
    if "合同" in compact or "履约" in compact:
        return "htly", "合同与履约"
    if section == "zbjh" or "招标计划" in compact:
        return "zbjh", "招标计划"
    if "定标候选人" in compact:
        return "dbhxr", "定标候选人公示"
    if "中标候选人" in compact:
        return "hxr", "中标候选人公示"
    if "中标结果" in compact or re.search(r"中标人(?:信息)?", compact):
        return ("gzjg", "更正结果公示") if correction else ("zbjg", "中标结果公示")
    if "资格预审" in compact or "资审公告" in compact:
        return "zbys", "资格预审公告"
    # 标题偶有省略“中标候选人/中标结果”等固定后缀，栏目 channelId 是比标题
    # 关键词更稳定的分类依据；先完成特殊类型识别，再使用栏目兜底。
    if section == "hxr":
        return "hxr", "中标候选人公示"
    if section == "gs":
        return ("gzjg", "更正结果公示") if correction else ("zbjg", "中标结果公示")
    # 更正/其他栏目没有独立的通用 Schema。无法识别原大类时归入招标公告，并通过
    # “源站公告性质”和 notice_subtype 保留变更、终止、废标等原始语义。
    return "zbgg", "招标公告"


def _normalize_contact_line(line: str) -> str:
    patterns = (
        (r"招\s*标\s*代\s*理(?:\s*机\s*构)?", "招标代理机构"),
        (r"代\s*理\s*机\s*构", "代理机构"),
        (r"招\s*标\s*人(?:\s*[（(][^）)]*[）)])?", "招标人"),
        (r"采\s*购\s*人", "采购人"),
        (r"建\s*设\s*单\s*位", "招标人"),
        (r"项\s*目\s*单\s*位", "招标人"),
        (r"详\s*细\s*地\s*址", "详细地址"),
        (r"联\s*系\s*地\s*址", "详细地址"),
        (r"地\s*址", "地址"),
        (r"联\s*系\s*人", "联系人"),
        (r"联\s*系\s*人\s*电\s*话", "联系电话"),
        (r"联\s*系\s*电\s*话", "联系电话"),
        (r"电\s*话", "电话"),
        (r"联\s*系\s*方\s*式", "联系方式"),
    )
    for pattern, label in patterns:
        match = re.match(rf"^\s*{pattern}\s*[：:]\s*(.*)$", line)
        if match:
            return f"{label}：{_space(match.group(1))}"
    return _space(line)


def _contact_section(text: str) -> list[str]:
    lines = [_normalize_contact_line(line) for line in text.splitlines() if _space(line)]
    headings = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(
            r"(?:[一二三四五六七八九十百\d]+[、.．])?联系方式",
            re.sub(r"[\s：:]+", "", line),
        )
    ]
    return lines[headings[-1] + 1 :] if headings else lines


def _contact_block(lines: list[str], party: str) -> list[str]:
    start = re.compile(r"^(?:招标代理机构|代理机构)：") if party == "agent" else re.compile(r"^(?:招标人|采购人)：")
    begin = next((i for i, line in enumerate(lines) if start.match(line)), -1)
    if begin < 0:
        return []
    result: list[str] = []
    for line in lines[begin:]:
        if result and party == "bidder" and re.match(r"^(?:招标代理机构|代理机构)：", line):
            break
        if result and "招标人或其招标代理机构" in line:
            break
        result.append(line)
    return result


def _block_value(block: list[str], labels: Iterable[str]) -> str:
    boundary = re.compile(
        r"^(?:招标人|采购人|招标代理机构|代理机构|详细地址|地址|联系人|"
        r"联系电话|电话|联系方式|电子邮件|电子邮箱|邮箱|开户行|开户银行|账号|"
        r"监督部门)\s*[：:]"
    )
    for index, line in enumerate(block):
        for label in labels:
            match = re.match(rf"^{re.escape(label)}：\s*(.*)$", line)
            if match:
                values = [_space(match.group(1)).strip("；;。")]
                for following in block[index + 1 :]:
                    if boundary.match(following) or "招标人或其招标代理机构" in following:
                        break
                    values.append(following)
                return _space(" ".join(value for value in values if value)).strip("；;。")
    return ""


def _contacts(text: str) -> dict[str, str]:
    lines = _contact_section(text)
    bidder = _contact_block(lines, "bidder")
    agent = _contact_block(lines, "agent")
    return {
        "bidder_name": _block_value(bidder, ("招标人", "采购人")),
        "bidder_address": _block_value(bidder, ("详细地址", "地址")),
        "bidder_contact": _block_value(bidder, ("联系人",)),
        "bidder_phone": _block_value(bidder, ("联系电话", "电话", "联系方式")),
        "agent_name": _block_value(agent, ("招标代理机构", "代理机构")),
        "agent_address": _block_value(agent, ("详细地址", "地址")),
        "agent_contact": _block_value(agent, ("联系人",)),
        "agent_phone": _block_value(agent, ("联系电话", "电话", "联系方式")),
    }


def _project_numbers(text: str) -> str:
    values: list[str] = []
    for pattern in (
        r"(?:招标项目编号|项目编号|招标编号)\s*[：:]\s*([^，,。；;\n（）()]+)",
        r"\bE\d{16,22}\b",
    ):
        for match in re.finditer(pattern, text):
            value = _space(match.group(1) if match.lastindex else match.group(0))
            if value and value not in values:
                values.append(value)
    return "|".join(values)


def _time_range(text: str, labels: Iterable[str]) -> str:
    value = _label_value(text, labels)
    if value:
        return value
    return ""


def _approval(text: str) -> tuple[str, str]:
    match = re.search(r"已由\s*([^，。；;\n]+?)\s*以\s*([^，。；;\n]+?号)\s*文件批准", text)
    return (match.group(1).strip(), match.group(2).strip()) if match else ("", "")


def _fill_common(data: dict[str, Any], parsed: ParsedNotice, info_source: str) -> None:
    text = parsed.raw_text
    values = {
        "项目性质": _label_value(text, ("项目性质",)),
        "源站公告性质": parsed.source_nature,
        "项目名称": _clean_project_name(parsed.title),
        "所属行业": _label_value(text, ("所属行业", "行业类别")),
        # “公开招标”是招标方式，不等于“组织形式”；源站未明确给出时保持为空。
        "组织形式": _label_value(text, ("组织形式",)),
        "发布日期": parsed.publish_time,
        "发布网站": _join_distinct(config.PLATFORM_NAME, info_source),
    }
    for field, value in values.items():
        if field in data:
            data[field] = value


def _fill_contacts(data: dict[str, Any], text: str) -> None:
    contact = _contacts(text)
    for field in ("招标人/采购人名称", "招标人/采购人", "招标人名称"):
        if field in data:
            data[field] = contact["bidder_name"]
    mapping = {
        "招标人地址": "bidder_address",
        "招标人联系人": "bidder_contact",
        "招标人联系方式": "bidder_phone",
        "招标代理机构": "agent_name",
        "招标代理机构地址": "agent_address",
        "招标代理机构联系人": "agent_contact",
        "招标代理机构联系方式": "agent_phone",
    }
    for field, key in mapping.items():
        if field in data:
            data[field] = contact[key]


def _parse_table(document) -> dict[str, str]:
    result: dict[str, str] = {}
    tables = document.cssselect("table.bid_msgTable")
    if not tables:
        return result
    for row in tables[0].cssselect("tr"):
        cells = [_space("".join(cell.itertext())) for cell in row.cssselect("th,td")]
        for index in range(0, len(cells) - 1, 2):
            label = re.sub(r"[（(][^）)]*[）)]", "", cells[index]).strip("：: ")
            if label and cells[index + 1]:
                result[label] = cells[index + 1]
    return result


def _direct_attachments(document, detail_url: str) -> list[dict[str, Any]]:
    content = document.cssselect(".cs_xq_content")
    root = content[0] if content else document
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in root.cssselect("a[href]"):
        href = _string(anchor.get("href"))
        name = _space("".join(anchor.itertext()))
        if not href or href.lower().startswith(("javascript:", "mailto:")):
            continue
        url = urljoin(detail_url, href)
        if not (FILE_SUFFIX_RE.search(url) or "/attachment.jspx" in url):
            continue
        if url in seen:
            continue
        seen.add(url)
        source_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        result.append(
            {
                "source_file_id": source_id,
                "file_name": name or urlsplit(url).path.rsplit("/", 1)[-1] or None,
                "file_url": url,
                "storage_path": None,
                "file_hash": None,
                "file_size_bytes": None,
                "file_type": "application/pdf" if ".pdf" in url.lower() else None,
                "parse_status": "URL_RESOLVED",
            }
        )
    return result


def _cms_attachment(html_text: str, document, fallback_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    pattern = re.compile(
        r"Cms\.attachment\(\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]?(\d+)['\"]?\s*,\s*(\d+)\s*,\s*['\"]([^'\"]+)['\"]\s*\)"
    )
    match = pattern.search(html_text)
    if not match:
        return None, []
    base, content_id, count_text, prefix = match.groups()
    content_id = content_id or fallback_id
    count = int(count_text)
    names: list[str] = []
    placeholders: list[dict[str, Any]] = []
    for index in range(count):
        nodes = document.xpath(f"//*[@id={prefix + str(index)!r}]")
        name = _space("".join(nodes[0].itertext())) if nodes else f"附件{index + 1}"
        names.append(name)
        placeholders.append(
            {
                "source_file_id": f"{content_id}_{index}",
                "file_name": name,
                "file_url": None,
                "storage_path": None,
                "file_hash": None,
                "file_size_bytes": None,
                "file_type": None,
                "parse_status": "PENDING",
            }
        )
    return {
        "base": urljoin(config.WEB_BASE_URL, base or "/"),
        "content_id": content_id,
        "count": count,
        "names": names,
    }, placeholders


class SxzwfwParser:
    """把列表元数据和详情 HTML 转换为框架八类公告字段。"""

    parser_version = "sxzwfw-v1"

    @classmethod
    def parse_list_records(cls, html_value: bytes | str) -> list[dict[str, str]]:
        try:
            document = _parse_document(html_value)
        except (etree.ParserError, ValueError):
            return []
        result: list[dict[str, str]] = []
        for anchor in document.cssselect("a.cs_two_c_2"):
            href = _string(anchor.get("href"))
            if not href:
                continue
            detail_url = urljoin(config.WEB_BASE_URL, href)
            detail_url = re.sub(r"^http://prec\.sxzwfw\.gov\.cn(?::80)?", config.WEB_BASE_URL, detail_url)
            title_nodes = anchor.cssselect(".cs_bz_cont")
            date_nodes = anchor.cssselect(".cs_bz_cont_1_time")
            title = _space("".join(title_nodes[0].itertext())) if title_nodes else _space("".join(anchor.itertext()))
            publish_time = _space("".join(date_nodes[0].itertext())) if date_nodes else ""
            full_line = _space("".join(anchor.itertext()))
            location_match = re.search(r"交易场所\s*[：:]\s*([^\s]+)", full_line)
            id_match = re.search(r"/(\d+)\.jhtml(?:$|[?#])", detail_url)
            result.append(
                {
                    "notice_id": id_match.group(1) if id_match else "",
                    "title": title,
                    "publish_time": publish_time,
                    "location": location_match.group(1) if location_match else "",
                    "detail_url": detail_url,
                }
            )
        return result

    @staticmethod
    def list_total(html_value: bytes | str) -> int:
        text = html_value.decode("utf-8", errors="replace") if isinstance(html_value, bytes) else str(html_value)
        for pattern in (r"layui-laypage-count[^>]*>\s*共\s*(\d+)\s*条", r"\bcount\s*:\s*(\d+)"):
            if match := re.search(pattern, text):
                return int(match.group(1))
        return 0

    @classmethod
    def parse(
        cls,
        section: str,
        html_value: bytes | str,
        list_record: Mapping[str, Any],
        detail_url: str,
    ) -> ParsedNotice:
        document = _parse_document(html_value)
        raw_text = visible_content_text(html_value)
        title = _first_text(document, ".cs_title_P1") or _string(list_record.get("title"))
        header = _first_text(document, ".cs_title_P3")
        publish_match = re.search(r"发布日期\s*[：:]\s*([0-9]{4}[-/.年][0-9]{1,2}[-/.月][0-9]{1,2}(?:日)?(?:\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?)", header)
        publish_time = publish_match.group(1) if publish_match else _string(list_record.get("publish_time"))
        source_match = re.search(r"信息来源\s*[：:]\s*(.+)$", header)
        info_source = _space(source_match.group(1)) if source_match else ""
        subtype, notice_type = _detect_type(section, title)
        source_nature = _source_nature(section, title)
        parsed = ParsedNotice(
            subtype=subtype,
            notice_type=notice_type,
            title=title,
            publish_time=publish_time,
            source_nature=source_nature,
            raw_text=raw_text,
            data={},
            attachments=[],
            cms_attachment=None,
        )
        data = create_empty_notice_data(notice_type)
        _fill_common(data, parsed, info_source)
        text = raw_text
        contacts = _contacts(text)

        if subtype == "zbjh":
            table = _parse_table(document)
            mapping = {
                "招标方式": ("招标方式",),
                "项目名称": ("项目名称",),
                "项目类型": ("项目类型",),
                "项目总投资": ("项目总投资", "估算总投资"),
                "招标内容": ("招标内容", "招标范围"),
                "招标人名称": ("招标人", "招标人名称"),
                "行政监督部门": ("行政监督部门", "监督部门"),
                "建设地点": ("建设地点", "项目地点"),
                "建设内容及规模": ("建设内容及规模", "建设规模"),
                "招标公告（资格预审公告）预计发布时间": ("预计发布时间", "招标公告预计发布时间"),
            }
            for field, labels in mapping.items():
                if field not in data:
                    continue
                data[field] = next((table[label] for label in labels if table.get(label)), "") or _label_value(text, labels)
        elif subtype in {"zbgg", "zbys"}:
            prequalification = subtype == "zbys"
            data["开标时间"] = _label_value(text, ("开标时间", "开启时间", "递交截止时间"))
            data["项目编号/招标编号"] = _project_numbers(text)
            data["项目类型/行业分类"] = _label_value(text, ("项目类型", "行业分类"))
            data["项目总投资/估算金额"] = _label_paragraph(text, ("项目总投资", "估算金额", "项目估算"))
            data["招标金额"] = _label_paragraph(text, ("招标金额", "最高投标限价", "最高限价", "招标控制价"))
            funding = _label_value(text, ("资金来源", "项目资金来源"))
            if not funding:
                match = re.search(r"(?:项目)?资金来源(?:为|是|[：:])\s*([^，。；;\n]+)", text)
                funding = match.group(1).strip() if match else ""
            data["资金来源"] = funding
            body_location = _label_value(text, ("招标项目所在地区", "项目所在地区", "项目地点", "建设地点"))
            data["项目地点"] = _join_distinct(body_location, list_record.get("location"))
            if "项目规模" in data:
                data["项目规模"] = _label_paragraph(
                    text,
                    ("项目规模", "建设规模", "工程规模"),
                    prefer_longest=True,
                )
            if "工期/服务期/供货日期" in data:
                data["工期/服务期/供货日期"] = _label_paragraph(text, ("计划工期", "工期", "服务期限", "服务期", "供货期", "交货期"))
            if "质量要求" in data:
                data["质量要求"] = _label_paragraph(text, ("质量要求", "质量标准"))
            scope_field = "项目概况与招标范围" if prequalification else "招标内容与范围"
            data[scope_field] = _section(text, ("项目概况和招标范围", "项目概况与招标范围"), ("投标人资格要求", "申请人资格要求", "招标文件的获取", "资格预审文件的获取"))
            data["申请人资格要求/投标人资格要求"] = _section(text, ("投标人资格要求", "申请人资格要求"), ("招标文件的获取", "资格预审文件的获取", "投标文件的递交"))
            data["预审文件获取时间"] = _time_range(text, ("获取时间", "招标文件获取时间", "资格预审文件获取时间"))
            data["获取方式"] = _label_paragraph(text, ("获取方式", "获取方法"), prefer_longest=True)
            data["递交截止时间"] = _label_value(text, ("递交截止时间", "投标截止时间"))
            data["递交方法"] = _label_paragraph(text, ("递交方法", "递交方式"))
            data["开启时间"] = _label_value(text, ("开启时间", "开标时间"))
            data["开启方式"] = _label_value(text, ("开启方式", "开标方式"))
            data["开启地点"] = _label_value(text, ("开启地点", "开标地点", "开标地址"))
            data["评审办法"] = _label_value(text, ("评审办法", "评标办法"))
            data["投标保证金方式"] = _section(text, ("提交投标保证金的形式", "投标保证金的形式"), ("提出异议", "其他公示内容", "监督部门", "联系方式"))
            _fill_contacts(data, text)
        elif subtype in {"hxr", "dbhxr"}:
            project_number = _project_numbers(text)
            publicity = _time_interval(
                text,
                whole_labels=("公示时间", "公示期"),
                start_labels=("公示开始时间",),
                end_labels=("公示结束时间",),
            )
            names, prices = candidate_name_price_pairs(text)
            details = candidate_details(names, prices)
            if subtype == "hxr":
                data["开标时间"] = _label_value(text, ("开标时间", "开标日期"))
                data["公示时间"] = publicity
                data["招标编号/项目编号"] = project_number
                data["中标候选人明细"] = details
                data["中标候选人名称"] = [
                    f"{item['标段']}：{item['候选人名称']}" if item["标段"] else item["候选人名称"]
                    for item in details
                ]
                data["中标候选人报价"] = [item["候选人报价"] for item in details]
            else:
                data["开标时间"] = _label_value(text, ("开标时间", "开标日期"))
                data["公示时间"] = publicity
                data["招标编号/项目编号"] = project_number
                data["定标候选人名称"] = [item["候选人名称"] for item in details]
                data["定标候选人报价"] = [item["候选人报价"] for item in details]
            _fill_contacts(data, text)
        elif subtype == "zbjg":
            details = award_details({}, text)
            data["中标结果明细"] = details
            data["中标人名称"] = [item["中标人名称"] for item in details]
            data["中标价"] = [item["中标价"] for item in details]
            data["招标方式"] = _label_value(text, ("招标方式",)) or ("公开招标" if "公开招标" in text else "")
            data["工期"] = _label_value(text, ("工期", "计划工期", "服务期"))
            data["项目经理"] = _label_value(text, ("项目经理", "项目负责人"))
            data["项目经理证书名称"] = _label_value(text, ("项目经理证书名称", "证书名称"))
            data["项目经理证书编号"] = _label_value(text, ("项目经理证书编号", "证书编号"))
            data["依据文件"], data["依据文号"] = _approval(text)
            _fill_contacts(data, text)
        elif subtype == "gzjg":
            data["公共类型"] = source_nature
            data["公告内容"] = text
            data["开标时间"] = _label_value(text, ("开标时间", "开启时间"))
            data["标书发售时间"] = _time_range(text, ("获取时间", "招标文件获取时间"))
            data["依据文件"], data["依据文号"] = _approval(text)
            _fill_contacts(data, text)
            supervision = _section(text, ("监督部门", "监督单位"), ("联系方式",))
            data["监督部门地址"] = _label_value(supervision, ("地址", "联系地址"))
            data["监督部门联系人"] = _label_value(supervision, ("联系人",))
            data["监督部门联系方式"] = _label_value(supervision, ("电话", "联系电话", "联系方式"))
        elif subtype == "htly":
            data["项目编号"] = _project_numbers(text)
            data["合同名称"] = _label_value(text, ("合同名称",)) or _clean_project_name(title)
            data["招标人名称"] = contacts["bidder_name"] or _label_value(text, ("招标人", "采购人"))
            winner = _label_value(text, ("中标人", "承包人", "供应商"))
            data["中标人名称"] = [winner] if winner else []
            data["合同金额"] = _label_value(text, ("合同金额", "签约合同价"))
            data["合同期限"] = _label_value(text, ("合同期限", "履约期限", "工期"))
            data["合同签署时间"] = _label_value(text, ("合同签署时间", "签订时间"))
            data["合同主要内容"] = _section(text, ("合同主要内容",), ("联系方式",)) or text

        direct = _direct_attachments(document, detail_url)
        id_match = re.search(r"/(\d+)\.jhtml(?:$|[?#])", detail_url)
        fallback_id = id_match.group(1) if id_match else _string(list_record.get("notice_id"))
        cms_info, placeholders = _cms_attachment(
            html_value.decode("utf-8", errors="replace") if isinstance(html_value, bytes) else str(html_value),
            document,
            fallback_id,
        )
        attachments = direct + [item for item in placeholders if item["source_file_id"] not in {value["source_file_id"] for value in direct}]
        data["附件"] = attachments
        parsed.data = canonicalize_notice_data(notice_type, data)
        parsed.attachments = parsed.data["附件"]
        parsed.cms_attachment = cms_info
        return parsed


def pages_for_total(total: int, page_size: int = config.PAGE_SIZE) -> int:
    return math.ceil(total / page_size) if total > 0 else 0
