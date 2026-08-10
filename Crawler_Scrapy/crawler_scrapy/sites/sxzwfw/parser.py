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
    coerce_decimal_amount,
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
                rf"(?:^|\s)(?:(?:\d+(?:\.\d+)*[、.．]?|[（(]\d+[）)])\s*)?"
                rf"{_flexible_label(label)}\s*[：:]\s*(.+)$",
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
        r"\d+(?:\.\d+)+(?:[、.．]\s*|\s+)|\d+[、.．]\s*|"
        r"本次(?:招标|采购)?公告(?:同时)?|本公告|发布公告的媒介|"
        r"招标公告发布媒介)"
    )
    generic_label = re.compile(
        r"^[^：:\n]{0,20}(?:时间|方式|方法|名称|编号|金额|规模|范围|期限|"
        r"要求|标准|地点|地址|联系人|电话|邮箱|来源|内容|类型|行业分类|"
        r"招标人|采购人|代理机构)\s*[：:]"
    )
    for index, line in enumerate(lines):
        for label in labels:
            match = re.search(
                rf"(?:^|\s)(?:(?:\d+(?:\.\d+)*[、.．]?|[（(]\d+[）)])\s*)?"
                rf"{_flexible_label(label)}\s*[：:]\s*(.*)$",
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
                child_item = bool(re.match(r"^[（(]\d+[）)]", following))
                if field_boundary.match(following) or (
                    generic_label.match(following) and not child_item
                ):
                    break
                parts.append(following)
                cursor += 1
            value = _space(" ".join(part for part in parts if part)).strip("；;")
            if value:
                values.append(value)
    if not values:
        return ""
    return max(values, key=len) if prefer_longest else values[0]


def _trim_following_numbered_field(value: str) -> str:
    """去掉同一视觉行中紧随其后的下一个编号字段。"""

    return re.split(
        r"[；;]\s*\d+(?:\.\d+)+(?:[、.．])?\s*[^：:\n]{1,24}[：:]",
        value,
        maxsplit=1,
    )[0].strip("；; ")


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


def _source_nature(section: str, title: str, text: str = "") -> str:
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
    if nature == "正常" and section == "qt" and "异常情况描述" in text:
        nature = "异常"
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
                # “(1标段)”属于项目名称，去公告后缀时不能顺带删除其右括号。
                value = value[: -len(suffix)].strip(" -—_")
                break
    return value or _space(title)


def _detect_type(section: str, title: str, text: str = "") -> tuple[str, str]:
    compact = re.sub(r"\s+", "", title)
    compact_text = re.sub(r"\s+", "", text)
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
    # 公共字段框架没有独立的终止公告 Schema。业务字段沿用招标公告，真实
    # 生命周期通过 zzgg 子类型保留，导出层再把公告编码写成 TERMINATION。
    insufficient_bidders = bool(re.search(
        r"(?:有效)?(?:投标人|投标单位|供应商)(?:数量)?不足[三3]家",
        compact_text,
    ))
    if any(
        word in compact for word in ("终止", "废标", "流标", "招标失败", "撤销")
    ) or (section == "qt" and insufficient_bidders):
        return "zzgg", "招标公告"
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


PROJECT_IDENTIFIER_LABELS = (
    "投资项目统一代码",
    "项目代码",
    "招标项目编号",
    "采购项目编号",
    "项目编号",
)
TENDER_IDENTIFIER_LABELS = ("招标编号", "采购编号", "代理编号")
IDENTIFIER_PROSE_MARKERS = (
    "资金来源", "招标人", "采购人", "已由", "批准", "建设单位", "本项目",
    "经评标委员会", "经评审", "现将", "现对", "进行公开招标",
    "项目总投资", "招标控制总价", "项目建设地址", "项目地址", "建设地点",
    "建设规模", "招标范围", "招标编号",
)


def _valid_identifier(value: str) -> bool:
    if not 4 <= len(value) <= 128:
        return False
    if value.upper() in {"NULL", "NONE", "N/A", "N-A"}:
        return False
    if any(marker in value for marker in IDENTIFIER_PROSE_MARKERS):
        return False
    if re.search(r"[：:&|；;]", value):
        return False
    if not all(
        value.count(opening) == value.count(closing)
        for opening, closing in (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】"))
    ):
        return False
    return bool(re.search(r"[0-9]", value))


def _clean_identifier_value(value: Any, labels: Iterable[str] = ()) -> str:
    """清洗标签后的单个编号，不让同一行后续说明文字进入编号。

    SXZWFW 的 PDF 转 HTML 文本经常把 ``）已由……批准`` 与编号放在同一
    行；编号自身又可能包含 ``（2026）``、``【2026】``，所以不能简单按
    第一个右括号截断。这里保留成对括号，并在未配对括号、顶层标点或常见
    说明性短语前停止。
    """

    result = re.sub(r"[\t\r\n]+", " ", _string(value)).strip()
    if not result:
        return ""

    label_values = tuple(labels)
    if label_values:
        label_pattern = "|".join(
            re.escape(label) for label in sorted(label_values, key=len, reverse=True)
        )
        repeated = re.compile(rf"^\s*(?:{label_pattern})\s*[：:]\s*")
        while repeated.match(result):
            result = repeated.sub("", result, count=1).strip()

    # PDF 转文本后，章节号常与编号无空格粘连，例如
    # ``E...0012.3项目地址``；这里的 ``2.3`` 属于下一字段，而非编号。
    result = re.split(
        r"2\.(?:2|3|4|5)\s*(?=(?:项目|建设|招标|监理|工程))",
        result,
        maxsplit=1,
    )[0]
    result = re.split(
        r"(?:项目总投资(?:为)?|招标控制总价|招标编号|项目建设地址|项目地址|"
        r"建设地点|建设规模(?:及(?:主要)?内容)?|招标范围)\s*[：:]",
        result,
        maxsplit=1,
    )[0]

    closing_for = {"(": ")", "（": "）", "[": "]", "【": "】"}
    closing = set(closing_for.values())
    stack: list[str] = []
    kept: list[str] = []
    for character in result:
        if character in closing_for:
            stack.append(closing_for[character])
            kept.append(character)
            continue
        if character in closing:
            if not stack or stack[-1] != character:
                break
            stack.pop()
            kept.append(character)
            continue
        if not stack and character in "，,。；;":
            break
        kept.append(character)
    result = "".join(kept).strip()

    prose_stops = (
        "项目资金来源", "资金来源", "招标人为", "采购人为", "已由", "是由",
        "由太原市", "由山西省", "批准建设", "批准", "本项目已",
        "经评标委员会", "经评审", "现将", "现对", "进行公开招标",
    )
    stop_indexes = [result.find(marker) for marker in prose_stops if marker in result]
    if stop_indexes:
        result = result[:min(stop_indexes)]

    result = re.sub(r"\s+", "", result).strip("：:，,。；;、")
    result = re.sub(r"^(?:变更为|变更后(?:为)?)[：:]?", "", result)
    for opening, ending in (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】")):
        if result.startswith(opening) and result.endswith(ending):
            result = result[1:-1].strip("：:，,。；;、")
            break
    if not _valid_identifier(result):
        return ""
    return result


def _identifier_values(text: str, labels: Iterable[str]) -> list[str]:
    """按调用方给出的标签优先级返回去重后的可靠编号。"""

    label_values = tuple(labels)
    values: list[str] = []
    for label in label_values:
        label_pattern = (
            rf"(?<!招标)(?<!采购){re.escape(label)}"
            if label == "项目编号"
            else re.escape(label)
        )
        patterns = (
            rf"(?m)^\s*[（(]?(?:\d+(?:\.\d+)*[、.．]?\s*)?"
            rf"{label_pattern}[ \t]*[：:][ \t]*([^\n]+)",
            rf"{label_pattern}[ \t]*[：:][ \t]*([^，,。；;\n]+)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                value = _clean_identifier_value(match.group(1), label_values)
                # PDF 视觉文本可能在编号中间换行，例如 ``202\n6GC010064``。
                # 仅当第一段本身不是有效编号时，拼接紧随其后的字母数字段。
                if not value:
                    following = re.match(
                        r"\s*\n\s*([A-Za-z0-9][A-Za-z0-9._/\\-]{1,100})",
                        text[match.end():],
                    )
                    if following:
                        prefix = _string(match.group(1))
                        continuation = following.group(1)
                        value = _clean_identifier_value(
                            continuation if not re.search(r"\d", prefix) else f"{prefix}{continuation}",
                            label_values,
                        )
                if value and value not in values:
                    values.append(value)
    return values


def _labelled_identifiers(text: str, labels: Iterable[str]) -> str:
    """返回最高优先级的单个编号，供数据库关联字段使用。"""

    values = _identifier_values(text, labels)
    return values[0] if values else ""


def _project_numbers(text: str) -> str:
    """保留正文中的全部业务编号，但不把组合值当成项目主键。"""

    values = [
        *_identifier_values(text, PROJECT_IDENTIFIER_LABELS),
        *_identifier_values(text, TENDER_IDENTIFIER_LABELS),
    ]
    for match in re.finditer(r"\b[EMD][A-Za-z0-9]{16,30}\b", text):
        if match.group(0) not in values:
            values.append(match.group(0))
    return "；".join(dict.fromkeys(values))


def _project_investment(text: str) -> str:
    value = _label_paragraph(
        text,
        ("项目总投资", "估算金额", "项目估算", "估算总投资", "计划投资"),
    )
    if value:
        value = _trim_following_numbered_field(value)
        match = re.match(
            r"^(?:为|约|人民币|：|:|\s)*"
            r"([\d,，]+(?:\.\d+)?\s*(?:亿元|万元|元))",
            value,
        )
        return _space(match.group(1)) if match else value
    match = re.search(
        r"(?:项目总投资|估算总投资|总投资|计划投资|投资额)\s*"
        r"(?:[：:]\s*)?(?:为|约)?\s*(?:总投资\s*)?"
        r"((?:人民币)?\s*[\d,，]+(?:\.\d+)?\s*(?:亿元|万元|元))",
        text,
    )
    return _space(match.group(1)) if match else ""


def _time_range(text: str, labels: Iterable[str]) -> str:
    value = _label_value(text, labels)
    if value:
        return value
    return ""


def _monetary_label_paragraph(text: str, labels: Iterable[str]) -> str:
    """优先保留标签后的总额，避免金额标准化误取括号内最后一个分标段值。"""

    label_values = tuple(labels)
    label_pattern = "|".join(
        re.escape(label) for label in sorted(label_values, key=len, reverse=True)
    )
    direct = re.search(
        rf"(?:{label_pattern})\s*(?:约|为)?\s*[：:]?\s*"
        r"([\d,，]+(?:\.\d+)?\s*(?:亿元|万元|元))",
        text,
    )
    if direct:
        return _space(direct.group(1))
    value = _label_paragraph(text, label_values)
    if not value:
        return ""
    match = re.match(
        r"^(?:为|约|人民币|：|:|\s)*"
        r"([\d,，]+(?:\.\d+)?\s*(?:亿元|万元|元))",
        value,
    )
    return _space(match.group(1)) if match else value


def _datetime_label_value(text: str, labels: Iterable[str]) -> str:
    """把源站常见中文时分格式转成 Schema 可识别的时间文本。"""

    value = _label_value(text, labels)
    if not value:
        return ""
    match = re.match(
        r"^(\d{4})年(\d{1,2})月(\d{1,2})日\s*"
        r"(\d{1,2})[时：:](\d{1,2})分?(?:(\d{1,2})秒)?",
        value,
    )
    if not match:
        return value
    year, month, day, hour, minute, second = match.groups()
    suffix = f":{int(second):02d}" if second is not None else ""
    return (
        f"{int(year):04d}-{int(month):02d}-{int(day):02d} "
        f"{int(hour):02d}:{int(minute):02d}{suffix}"
    )


def _approval(text: str) -> tuple[str, str]:
    match = re.search(r"已由\s*([^，。；;\n]+?)\s*以\s*([^，。；;\n]+?号)\s*文件批准", text)
    return (match.group(1).strip(), match.group(2).strip()) if match else ("", "")


def _fill_common(data: dict[str, Any], parsed: ParsedNotice, info_source: str) -> None:
    text = parsed.raw_text
    values = {
        "项目性质": _label_value(text, ("项目性质",)),
        "源站公告性质": parsed.source_nature,
        "项目名称": _label_value(
            text,
            ("招标项目名称", "采购项目名称", "项目名称"),
        ) or _clean_project_name(parsed.title),
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


def _organization_name_like(value: str) -> bool:
    return bool(re.search(
        r"(?:公司|集团|研究院|设计院|检测中心|试验中心|事务所|联合社|合作社)",
        value,
    ))


def _candidate_table_details(document, text: str) -> list[dict[str, Any]]:
    """从候选人结果表按行提取名称和报价，避免扁平文本丢失列关系。"""

    default_section = ""
    if match := re.search(r"(?m)^(\d{3}[^\n：:]{1,160})\s*[：:]\s*$", text):
        default_section = _space(match.group(1))

    tables: list[tuple[bool, list[list[str]], int, int, int]] = []
    for table in document.xpath(".//table[not(.//table)]"):
        rows = []
        for row in table.xpath(".//tr"):
            cells = [_space("".join(cell.itertext())) for cell in row.xpath("./th|./td")]
            if cells:
                rows.append(cells)
        for header_index, header in enumerate(rows):
            name_index = next(
                (i for i, value in enumerate(header) if "中标候选人" in value),
                -1,
            )
            if name_index < 0:
                continue
            price_index = next(
                (
                    i for i, value in enumerate(header)
                    if any(word in value for word in ("投标报价", "投标总报价", "中标价", "报价"))
                ),
                -1,
            )
            section_index = next(
                (i for i, value in enumerate(header) if value in {"标段", "标包", "包号"}),
                -1,
            )
            tables.append(
                (price_index >= 0, rows[header_index + 1 :], name_index, price_index, section_index)
            )
            break

    # 优先使用同时含名称和报价的“候选人基本情况”表；人员/业绩表只作兜底。
    tables.sort(key=lambda value: value[0], reverse=True)
    for _, rows, name_index, price_index, section_index in tables:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        current_section = default_section
        for row in rows:
            offset = 0
            if (
                section_index == 0
                and name_index > 0
                and name_index < len(row)
                and not _organization_name_like(row[name_index])
                and _organization_name_like(row[name_index - 1])
            ):
                # 部分表格第二行起省略合并后的“标段”单元格，后续列整体左移。
                offset = -1
            actual_name_index = name_index + offset
            actual_price_index = price_index + offset if price_index >= 0 else -1
            if actual_name_index < 0 or actual_name_index >= len(row):
                continue
            if section_index >= 0 and offset == 0 and section_index < len(row):
                section_value = row[section_index]
                if section_value:
                    current_section = section_value
            name = row[actual_name_index].strip()
            if not name or "中标候选人" in name or name in {"排序", "序号", "响应情况"}:
                continue
            price = (
                row[actual_price_index].strip()
                if 0 <= actual_price_index < len(row)
                else ""
            )
            identity = (current_section, name, price)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(
                {
                    "标段": current_section,
                    "候选人名称": name,
                    "候选人报价": price or None,
                }
            )
        if result:
            return result
    return []


def _candidate_visual_details(text: str) -> list[dict[str, Any]]:
    """兜底提取 PDF 视觉定位表中的候选人名称，不猜测未公布的报价。"""

    lines = [_space(line) for line in text.splitlines() if _space(line)]
    starts = [
        index for index, line in enumerate(lines)
        if line in {"中标候选人", "中标候选人名称"}
    ]
    if not starts:
        return []
    start = starts[-1] + 1
    block: list[str] = []
    for line in lines[start:]:
        if re.match(r"^[二三四五六七八九十]+[、.．]", line):
            break
        block.append(line)

    organization = re.compile(
        r"(?:公司|集团|研究院|设计院|检测中心|试验中心|事务所|联合社|合作社)"
        r"(?:[（(]有限公司[）)])?$"
    )
    names: list[str] = []
    pending_leader = ""
    for index, line in enumerate(block):
        if not organization.search(line):
            continue
        role = block[index + 1] if index + 1 < len(block) else ""
        if "联合体牵头人" in role:
            pending_leader = f"{line}{role}"
            continue
        if "联合体成员" in role and pending_leader:
            names.append(f"{pending_leader}{line}{role}")
            pending_leader = ""
            continue
        if pending_leader:
            names.append(pending_leader)
            pending_leader = ""
        if line not in names:
            names.append(line)
    if pending_leader:
        names.append(pending_leader)
    return [
        {"标段": "", "候选人名称": name, "候选人报价": None}
        for name in dict.fromkeys(names)
    ]


def _award_result_details(text: str) -> list[dict[str, Any]]:
    """提取 SXZWFW 结果公示中的“标段—中标人—中标价”。

    源站正文既有标准“中标人：”行，也有由 ``&nbsp;`` 排成“中 标 人”的
    行，还存在“招标人确定某公司为该项目的中标人”的叙述模板。公共解析器
    先处理标准模板；这里仅补齐该站的空格标签和叙述模板，不猜测未公布价格。
    """

    standard = award_details({}, text)
    result: list[dict[str, Any]] = []
    section = ""
    lines = [_space(raw_line) for raw_line in text.splitlines() if _space(raw_line)]
    section_pattern = re.compile(
        r"^(\d{3}[^：:\n]{0,180}?(?:标段|包))\s*[：:]?\s*$"
    )
    ordinal = r"(?:[一二三四五六七八九十百\d]+[、.．]\s*)?"
    price_label = (
        rf"(?:{_flexible_label('中标价格')}|{_flexible_label('中标价')}|"
        rf"{_flexible_label('中标金额')}|{_flexible_label('投标报价')})"
    )
    name_pattern = re.compile(
        rf"^{ordinal}(?:{_flexible_label('中标人名称')}|{_flexible_label('中标人')})"
        rf"\s*[：:]?\s*(.+?)(?={ordinal}{price_label}\s*[：:]?|$)"
    )
    price_pattern = re.compile(
        rf"{ordinal}{price_label}\s*[：:]?\s*(.*)$"
    )
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if result and any(
            label in compact for label in ("其他公示内容", "监督部门", "联系方式")
        ):
            break
        if match := section_pattern.match(line):
            section = _space(match.group(1))
            continue
        if match := name_pattern.match(line):
            name = _space(match.group(1)).strip("；;，,。")
            if name and name not in {"信息", "情况"}:
                result.append({"标段": section, "中标人名称": name, "中标价": None})
        if match := price_pattern.search(line):
            if result and result[-1]["中标价"] is None:
                value = _space(match.group(1)).strip("；;。")
                if not value and index + 1 < len(lines):
                    following = lines[index + 1].strip("；;。")
                    if coerce_decimal_amount(following) is not None or any(
                        marker in following for marker in ("费率", "单价", "%", "％")
                    ):
                        value = following
                amount = coerce_decimal_amount(value)
                result[-1]["中标价"] = amount if amount is not None else (value or None)

    if result:
        return result

    if standard:
        return standard

    # 部分结果公告只在叙述句中公布中标人，随后单独给出投标报价。
    narrative = re.search(
        r"(?:招标人)?确定\s*(.{2,180}?)\s*为(?:该|本)?项目(?:的)?中标人",
        text,
    )
    if not narrative:
        return []
    name = _space(narrative.group(1)).strip("，,；;。")
    price = _label_value(text, ("投标报价", "中标价", "中标价格", "中标金额"))
    amount = coerce_decimal_amount(price)
    return [
        {
            "标段": section,
            "中标人名称": name,
            "中标价": amount if amount is not None else (price or None),
        }
    ]


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

    parser_version = "sxzwfw-v8-field-boundaries"

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
        subtype, notice_type = _detect_type(section, title, raw_text)
        source_nature = _source_nature(section, title, raw_text)
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
        data = create_empty_notice_data(
            notice_type, include_parser_diagnostics=True
        )
        _fill_common(data, parsed, info_source)
        text = raw_text
        if "项目编号" in data:
            data["项目编号"] = _labelled_identifiers(
                text,
                PROJECT_IDENTIFIER_LABELS,
            )
        if "招标编号" in data:
            data["招标编号"] = _labelled_identifiers(
                text,
                TENDER_IDENTIFIER_LABELS,
            )
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
        elif subtype in {"zbgg", "zbys", "zzgg"}:
            prequalification = subtype == "zbys"
            data["开标时间"] = _datetime_label_value(text, ("开标时间", "开启时间"))
            data["项目编号/招标编号"] = _project_numbers(text)
            data["项目类型/行业分类"] = _label_value(text, ("项目类型", "行业分类"))
            data["项目总投资/估算金额"] = _project_investment(text)
            data["招标金额"] = _monetary_label_paragraph(
                text,
                (
                    "最高投标限价总价", "招标控制总价", "财政审定金额",
                    "本次招标金额", "招标金额", "最高投标限价", "最高限价",
                    "招标控制价",
                ),
            )
            funding = _label_value(text, ("资金来源", "项目资金来源", "建设资金来源"))
            if not funding:
                match = re.search(
                    r"(?:项目资金来源|资金来源|建设资金(?:来源)?)"
                    r"(?:为|是|由|[：:])\s*([^。；;\n]+)",
                    text,
                )
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
                data["工期/服务期/供货日期"] = _trim_following_numbered_field(_label_paragraph(
                    text,
                    (
                        "计划工期", "施工工期", "建设工期", "合同履行期限", "合同期限",
                        "服务周期/服务完成期限", "服务期限", "服务周期",
                        "服务期", "供货期限",
                        "服务完成期限", "供货期", "交货期限", "交货期", "工期",
                    ),
                    prefer_longest=True,
                ))
            if "质量要求" in data:
                data["质量要求"] = _trim_following_numbered_field(
                    _label_paragraph(
                        text,
                        ("服务标准/质量要求", "质量要求", "质量标准"),
                    )
                )
            scope_field = "项目概况与招标范围" if prequalification else "招标内容与范围"
            data[scope_field] = _section(
                text,
                (
                    "项目概况和招标范围", "项目概况与招标范围",
                    "招标内容和范围", "招标内容与范围", "招标范围",
                    "采购内容和范围", "采购内容与范围", "采购范围",
                ),
                (
                    "投标人资格要求", "申请人资格要求", "供应商资格要求",
                    "招标文件的获取", "资格预审文件的获取", "采购文件的获取",
                ),
            )
            data["申请人资格要求/投标人资格要求"] = _section(
                text,
                (
                    "投标人资格要求", "投标人资格能力要求",
                    "申请人资格要求", "申请人资格能力要求", "供应商资格要求",
                ),
                (
                    "招标文件的获取", "资格预审文件的获取", "采购文件的获取",
                    "投标文件的递交", "响应文件的递交",
                ),
            )
            data["预审文件获取时间"] = _time_range(
                text,
                (
                    "获取时间", "招标文件获取时间", "资格预审文件获取时间",
                    "电子招标文件获取时间", "电子资格预审文件获取时间",
                    "采购文件获取时间", "文件获取时间",
                ),
            )
            data["获取方式"] = _label_paragraph(
                text,
                (
                    "电子招标文件获取方式", "电子资格预审文件获取方式",
                    "获取方式", "获取方法",
                ),
                prefer_longest=True,
            )
            data["递交截止时间"] = _label_value(text, ("递交截止时间", "投标截止时间"))
            data["递交方法"] = _label_paragraph(text, ("递交方法", "递交方式"))
            data["开启时间"] = _datetime_label_value(text, ("开启时间", "开标时间"))
            data["开启方式"] = _label_paragraph(text, ("开启方式", "开标方式"))
            data["开启地点"] = _label_paragraph(
                text, ("开启地点", "开标地点", "开标地址")
            )
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
            details = _candidate_table_details(document, text)
            if not details:
                names, prices = candidate_name_price_pairs(text)
                details = candidate_details(names, prices)
            if not details:
                details = _candidate_visual_details(text)
            if subtype == "hxr":
                data["开标时间"] = _label_value(text, ("开标时间", "开标日期"))
                data["公示时间"] = publicity
                data["招标编号/项目编号"] = project_number
                data["中标候选人明细"] = details
                # 保持框架既有扁平字段约定：多标段时在名称前保留标段；结构化
                # 的纯名称和标段仍分别存放在“中标候选人明细”中。
                data["中标候选人名称"] = [
                    f"{item['标段']}：{item['候选人名称']}"
                    if item["标段"] else item["候选人名称"]
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
            details = _award_result_details(text)
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
            data["项目编号"] = _labelled_identifiers(
                text, PROJECT_IDENTIFIER_LABELS
            )
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
        parsed.data = canonicalize_notice_data(
            notice_type, data, include_parser_diagnostics=True
        )
        parsed.attachments = attachments
        parsed.cms_attachment = cms_info
        return parsed


def pages_for_total(total: int, page_size: int = config.PAGE_SIZE) -> int:
    return math.ceil(total / page_size) if total > 0 else 0
