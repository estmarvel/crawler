"""华新阳光采购平台的纯数据解析器。

本模块不发送请求、不写文件，也不调用 AI。它迁移自旧版
``sjq/Crawler/huaxin/crawler.py`` 中已经由真实数据验证过的分类和字段规则，
由 Scrapy Spider 将列表字段与详情 JSON 合并后调用。
"""

from __future__ import annotations

import re
import unicodedata
from html import unescape
from typing import Any, Iterable, Mapping

from crawler_scrapy.schemas.notice_fields import (
    canonicalize_notice_data,
    coerce_decimal_amount,
    create_empty_notice_data,
)
from crawler_scrapy.sites.huaxin.config import PLATFORM_NAME, WEB_BASE_URL


BIDDING_NATURE_LABELS: dict[str, dict[str, str]] = {
    "1": {
        "1": "正常",
        "2": "再次",
        "3": "重新",
        "4": "变更",
        "5": "终止",
        "6": "延期",
        "7": "控制价",
        "8": "补充",
        "9": "控制价变更",
        "10": "暂停",
        "11": "暂停恢复",
    },
    "2": {
        "1": "中标候选人公示",
        "4": "更正中标候选人公示",
        "5": "撤销中标候选人公示",
    },
    "3": {
        "1": "中标结果",
        "4": "更正中标结果",
        "5": "撤销中标结果",
    },
}


def _string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _code(value: Any) -> str:
    return _string(value)


def clean_html_keep_lines(value: Any) -> str:
    """删除 HTML 标签并保留段落边界，供标签和章节规则使用。"""

    if not value:
        return ""
    text = unescape(str(value))
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", text)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(
        r"(?i)</?\s*(?:p|div|li|tr|h[1-6]|table|section)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def clean_html(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_html_keep_lines(value)).strip()


def _combine_text(detail: Mapping[str, Any]) -> str:
    # 实际接口中的 annContent2 通常只是 annContent 外加页面包装。如果两者同时
    # 合并，规则和 AI 会收到两遍正文，长公告还会因此提前触发截断。
    primary_content = detail.get("annContent") or detail.get("annContent2")
    keys = (
        "bidCondition",
        "projectOverview",
        "bidOverview",
        "bidQualification",
        "consortiumQualification",
        "acquisitionWay",
        "acquisitionOther",
        "submitWay",
        "submitAddress",
        "openAddress",
        "evaluationMethod",
        "ensureForm",
        "objectionWay",
        "reviewSituation",
        "otherAnnContent",
        "otherContent",
        "terminationReason",
        "supervisionUnitName",
        "bidContactInformation",
        "contactInformation",
    )
    parts = [clean_html_keep_lines(primary_content)] if primary_content else []
    parts.extend(
        clean_html_keep_lines(detail.get(key))
        for key in keys
        if detail.get(key)
    )
    # 部分结构化 HTML 字段也会原样包含在 annContent 中，按清洗后的整段去重。
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = part.strip()
        if not normalized or normalized in seen:
            continue
        # annContent经常已经包含reviewSituation/contactInformation等结构化片段。
        # 子段已完整存在于主正文时不再追加；源站包装页偶尔只是在标段号中
        # 多一个空格，因此同时做去空白后的包含判断。
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", normalized))
        if any(
            normalized in existing
            or compact in re.sub(
                r"\s+", "", unicodedata.normalize("NFKC", existing)
            )
            for existing in result
        ):
            continue
        seen.add(normalized)
        result.append(normalized)
    return "\n".join(result)


def _format_range(start: Any, end: Any) -> str:
    start_value, end_value = _string(start), _string(end)
    if start_value and end_value:
        return f"{start_value} 至 {end_value}"
    return start_value or end_value


def _join_distinct(*values: Any, separator: str = "|") -> str:
    """按输入顺序连接非空且不重复的来源值。"""

    result: list[str] = []
    for value in values:
        normalized = _string(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return separator.join(result)


def _source_notice_nature(detail: Mapping[str, Any]) -> str:
    """按华新前端 JS 映射 annNature，并同时保留源站代码。"""

    nature = _code(detail.get("annNature"))
    if not nature:
        return ""
    classification = _code(detail.get("annClassification"))
    label = BIDDING_NATURE_LABELS.get(classification, {}).get(nature, "未知")
    return f"{label}（annNature={nature}）"


def _flexible_label(label: str) -> str:
    """把中文标签转换为允许字符间空格的正则，例如“联 系 人”。"""

    compact = re.sub(r"\s+", "", label)
    return r"\s*".join(re.escape(char) for char in compact)


def _extract_label(text: str, labels: Iterable[str]) -> str:
    """按“标签：值”提取，并在新章节或下一个标签处停止。"""

    lines = [line.strip() for line in clean_html_keep_lines(text).splitlines() if line.strip()]
    labels = tuple(labels)
    for index, line in enumerate(lines):
        for label in labels:
            match = re.search(
                rf"(?:^|[\s；;。])(?:\d+(?:\.\d+)*(?:[、.．])?\s*)?"
                rf"{_flexible_label(label)}\s*(?:为|是)?\s*[：:]\s*(.*)$",
                line,
            )
            if not match:
                continue
            inline_value = match.group(1).strip()
            # 同一段经常连续写“合同履行期限：...；交货地点：...；质量要求：...”。
            # 当前字段只保留下一个中文标签前的内容，避免把后续字段一并吞入。
            if inline_value:
                inline_value = re.split(
                    r"[；;。]\s*(?=[\u4e00-\u9fffA-Za-z/（）()]{1,24}\s*[：:])",
                    inline_value,
                    maxsplit=1,
                )[0].strip()
            chunks = [inline_value] if inline_value else []
            for following in lines[index + 1 :]:
                if re.match(r"^(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)[、.．]\s*", following):
                    break
                if any(
                    re.match(rf"^{_flexible_label(other)}\s*[：:]", following)
                    for other in labels
                ):
                    break
                compact_following = re.sub(r"\s+", "", following)
                if re.match(
                    r"^(?:(?:\d+(?:\.\d+)*[、.．]?)|[一二三四五六七八九十]+[、.．])?"
                    r"[\u4e00-\u9fffA-Za-z/（）()]{1,24}[：:]",
                    compact_following,
                ):
                    break
                chunks.append(following)
            return re.sub(r"\s+", " ", " ".join(chunks)).strip()
    return ""


def _extract_section(text: str, starts: Iterable[str], stops: Iterable[str]) -> str:
    lines = [line.strip() for line in clean_html_keep_lines(text).splitlines() if line.strip()]
    start_index = -1
    matched_label = ""
    for index, line in enumerate(lines):
        for label in starts:
            if label in line:
                start_index, matched_label = index, label
                break
        if start_index >= 0:
            break
    if start_index < 0:
        return ""

    chunks: list[str] = []
    match = re.search(rf"{re.escape(matched_label)}\s*[：:]\s*(.*)$", lines[start_index])
    if match and match.group(1).strip():
        chunks.append(match.group(1).strip())
    for line in lines[start_index + 1 :]:
        if any(stop in line for stop in stops):
            break
        chunks.append(line)
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()


def _extract_labeled_section(
    text: str,
    labels: Iterable[str],
    stops: Iterable[str],
) -> str:
    """只从独立标签行开始提取多行值，避免在普通正文中命中标签子串。"""

    lines = [line.strip() for line in clean_html_keep_lines(text).splitlines() if line.strip()]
    labels = tuple(labels)
    for index, line in enumerate(lines):
        for label in labels:
            match = re.match(
                rf"^(?:\d+(?:\.\d+)*(?:[、.．])?\s*)?"
                rf"{_flexible_label(label)}\s*(?:为|是)?\s*[：:]\s*(.*)$",
                line,
            )
            if not match:
                continue
            chunks = [match.group(1).strip()] if match.group(1).strip() else []
            for following in lines[index + 1 :]:
                if any(stop in following for stop in stops):
                    break
                chunks.append(following)
            return re.sub(r"\s+", " ", " ".join(chunks)).strip()
    return ""


def _project_name(value: Any) -> str:
    """从公告标题中去掉公告类型和重新招标次数等展示后缀。"""

    title = _string(value)
    suffix = re.compile(
        r"\s*(?:"
        r"资格预审公告|招标计划|"
        r"(?:(?:第?[一二三四五六七八九十百\d]+次)?(?:重新)?招标)?"
        r"(?:公告|中标候选人公示|定标候选人公示|中标结果公示|更正结果公示)"
        r")\s*$"
    )
    previous = None
    while title and title != previous:
        previous = title
        title = suffix.sub("", title).strip(" -—_")
    return title


def _contact_section(text: str) -> str:
    """优先截取文末明确的“联系方式”章节，避免误取异议联系人电话。"""

    lines = [
        line.strip()
        for line in _normalize_contact_text(clean_html_keep_lines(text)).splitlines()
        if line.strip()
    ]
    heading = re.compile(
        r"^(?:(?:\d+(?:\.\d+)*)|[一二三四五六七八九十]+)?"
        r"[、.．]?\s*联系方式\s*$"
    )
    indices = [index for index, line in enumerate(lines) if heading.match(line)]
    return "\n".join(lines[indices[-1] + 1 :]) if indices else "\n".join(lines)


def _normalize_contact_text(text: str) -> str:
    """只规范联系方式行首标签，不改动机构名、地址和联系人实际内容。"""

    label_patterns = (
        (r"招\s*标\s*代\s*理\s*机\s*构", "招标代理机构"),
        (r"招\s*标\s*代\s*理", "招标代理机构"),
        (r"代\s*理\s*机\s*构", "代理机构"),
        (r"招\s*标\s*人", "招标人"),
        (r"采\s*购\s*人", "采购人"),
        (r"详\s*细\s*地\s*址", "详细地址"),
        (r"地\s*址", "地址"),
        (r"联\s*系\s*人", "联系人"),
        (r"联\s*系\s*电\s*话", "联系电话"),
        (r"联\s*络\s*电\s*话", "联系电话"),
        (r"电\s*话", "电话"),
        (r"联\s*系\s*方\s*式", "联系方式"),
        (r"电\s*子\s*邮\s*(?:件|箱)", "电子邮件"),
    )
    result: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        for pattern, canonical in label_patterns:
            match = re.match(rf"^{pattern}\s*[：:]\s*(.*)$", line)
            if match:
                line = f"{canonical}：{match.group(1).strip()}"
                break
        result.append(line)
    return "\n".join(result)


def _contact_block(text: str, party: str) -> str:
    lines = [
        line.strip()
        for line in _contact_section(text).splitlines()
        if line.strip()
    ]
    start_pattern = (
        re.compile(r"^(?:招标代理机构|代理机构)\s*[：:]")
        if party == "agent"
        else re.compile(r"^(?:招标人|采购人)\s*[：:]")
    )
    stops = (
        ("邮箱", "电子邮件", "开户行", "账号", "公示开始时间")
        if party == "agent"
        else ("招标代理机构：", "招标代理机构:", "代理机构：", "代理机构:")
    )
    begin = next(
        (index for index, line in enumerate(lines) if start_pattern.match(line)),
        -1,
    )
    if begin < 0:
        return ""
    chunks: list[str] = []
    for line in lines[begin:]:
        if chunks and any(stop in line for stop in stops):
            break
        chunks.append(line)
    return "\n".join(chunks)


def _contact_value(text: str, party: str, field: str) -> str:
    block = _contact_block(text, party)
    labels = {
        "name": ("招标代理机构", "代理机构") if party == "agent" else ("招标人", "采购人"),
        "address": ("招标代理机构地址", "招标人地址", "详细地址", "地址", "地 址"),
        "contact": ("招标代理机构联系人", "招标人联系人", "联系人", "联 系 人"),
        "phone": (
            "招标代理机构联系方式", "招标人联系方式", "联系方式",
            "联系电话", "联络电话", "电话", "电 话",
        ),
    }[field]
    if field == "name":
        value = ""
        for line in block.splitlines():
            for label in labels:
                match = re.match(rf"^{re.escape(label)}\s*[：:]\s*(.*)$", line)
                if match:
                    value = match.group(1).strip()
                    break
            if value:
                break
    else:
        value = _extract_label(block, labels)
    if field == "phone":
        value = re.split(r"(?:发布日期|公示开始时间|招标编号|一[、.．])", value, maxsplit=1)[0]
    return value.strip(" ：:，,；;")


def _project_numbers(detail: Mapping[str, Any], text: str) -> str:
    values: list[str] = []
    sources = tuple(
        filter(
            None,
            (
                _string(detail.get("diyProjectNo")),
                _string(detail.get("purDiyCode")),
                text,
            ),
        )
    )
    for pattern in (
        r"E\d{19}",
        # 代理招标编号的字母段中经常带数字，例如ZDF03-HZ260502、
        # HXCT01-2026G001；要求至少含一个连字符，避免把邮箱或普通单词当编号。
        r"[A-Z][A-Z0-9]{1,15}(?:-[A-Z0-9]{2,20})+",
    ):
        for source in sources:
            # 允许HTML span造成的水平空格，但保留换行，否则下一行
            # “2.3 招标内容”的2会被粘到编号末尾。
            normalized_source = re.sub(r"[^\S\r\n]+", "", source)
            for match in re.finditer(pattern, normalized_source):
                if match.group(0) not in values:
                    values.append(match.group(0))
    if not values:
        fallback = _string(detail.get("diyProjectNo") or detail.get("purDiyCode"))
        if fallback:
            values.append(fallback)
    return "；".join(values)


def _clean_identifier_value(value: Any, labels: tuple[str, ...] = ()) -> str:
    """清洗标签后面的单个编号，并在正文开始处可靠停止。

    TWS 公告常把编号和“资金来源/招标人”等正文放在同一段，甚至出现
    ``招标项目编号：招标项目编号：E...``。不能简单读取整行，也不能直接
    遇到右括号就截断，因为代理编号自身可能包含 ``（2024）``。
    """

    result = re.sub(r"[\t\r\n]+", " ", _string(value)).strip()
    if not result:
        return ""

    if labels:
        label_pattern = "|".join(
            re.escape(label) for label in sorted(labels, key=len, reverse=True)
        )
        repeated_label = re.compile(
            rf"^\s*(?:{label_pattern})\s*[：:]\s*",
        )
        while repeated_label.match(result):
            result = repeated_label.sub("", result, count=1).strip()

    # 在未配对的右括号或顶层标点处停止；配对括号属于编号本身，例如
    # ``SXZS招（2024）07-15``，必须保留。
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

    # 少数模板没有括号或标点分隔，直接紧跟说明性正文。
    prose_stops = (
        "项目资金来源", "资金来源", "招标人为", "采购人为", "已由",
        "本项目已", "经评标委员会", "经评审", "现将", "现对",
    )
    stop_indexes = [result.find(marker) for marker in prose_stops if marker in result]
    if stop_indexes:
        result = result[:min(stop_indexes)]

    # 编号中的排版空格没有业务意义；末尾的冒号、顿号等也不属于编号。
    result = re.sub(r"\s+", "", result)
    result = result.strip("：:，,。；;、")
    for opening, ending in (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】")):
        if result.startswith(opening) and result.endswith(ending):
            result = result[1:-1].strip("：:，,。；;、")
            break
    return result


def _labelled_identifier(text: str, labels: tuple[str, ...]) -> str:
    """只接受有明确语义标签的编号，防止项目号和招标号相互污染。"""

    for label in sorted(labels, key=len, reverse=True):
        match = re.search(
            rf"(?:^|\n)\s*[（(]?(?:\d+(?:\.\d+)*[、.．]?\s*)?"
            rf"{re.escape(label)}\s*[：:]\s*([^\n]+)",
            text,
        )
        if not match:
            match = re.search(
                rf"{re.escape(label)}\s*[：:]\s*([^，,。；;\n]+)", text
            )
        if match:
            return _clean_identifier_value(match.group(1), labels)
    return ""


def _extract_tender_scope(text: str) -> str:
    """提取完整招标内容与范围，保留后续001/002等标段内容。"""

    stops = (
        "投标人资格要求",
        "申请人资格要求",
        "资格要求",
        "招标文件的获取",
        "资格预审文件的获取",
        "文件的获取",
        "计划工期",
        "服务期限",
        "服务周期",
        "服务期",
        "供货期限",
        "供货期",
        "交货期限",
        "交货期",
        "质量要求",
        "质量标准",
        "招标控制价",
        "招标金额",
        "最高投标限价",
        "最高限价",
        "项目地点",
        "建设地点",
        "工程地点",
    )
    # 先找具体子标题，避免在“项目概况和招标范围”大章节处过早开始。
    scope = _extract_labeled_section(
        text,
        ("招标内容与范围", "招标范围"),
        stops,
    )
    if scope:
        return scope
    scope = _extract_label(text, ("招标内容与范围", "招标范围"))
    if scope:
        return scope
    scope = _extract_section(text, ("招标内容与范围",), stops)
    if scope:
        return scope
    return _extract_section(
        text,
        ("项目概况与招标范围", "项目概况和招标范围", "招标范围"),
        stops,
    )


def _extract_duration(text: str) -> str:
    """兼容同段标签和“交货期：\n第一标段...”等多行期限。"""

    labels = (
        "合同履行期限", "监理服务期限", "合同服务期限",
        "计划工期", "建设工期", "施工工期",
        "服务期限", "服务周期", "服务期", "供货期限", "供货期",
        "交货期限", "交货期", "工期",
    )
    value = _extract_label(text, labels)
    if value:
        return value
    return _extract_labeled_section(
        text,
        labels,
        (
            "交货地点", "供货地点", "服务地点", "项目地点", "建设地点",
            "质量要求", "质量标准", "投标人资格要求", "申请人资格要求",
            "招标文件的获取", "资格预审文件的获取",
        ),
    )


def _extract_quality(text: str) -> str:
    """提取通用及设计、施工、服务等分项质量要求。"""

    labels = (
        "服务质量要求", "设计要求的质量标准", "施工要求的质量标准",
        "采购质量要求", "技术质量要求", "质量要求", "质量标准",
    )
    values = [_extract_label(text, (label,)) for label in labels]
    values = [value for value in values if value]
    if values:
        return _join_distinct(*values, separator="；")
    return _extract_labeled_section(
        text,
        labels,
        (
            "投标人资格要求", "申请人资格要求", "招标文件的获取",
            "资格预审文件的获取", "联系方式",
        ),
    )


def _extract_manager_certificate(text: str) -> tuple[str, str]:
    """提取中标结果中项目经理证书名称与编号，兼容合并标签。"""

    name = _extract_label(text, ("项目经理证书名称", "证书名称"))
    number = _extract_label(text, ("项目经理证书编号", "证书编号"))
    if name or number:
        return name, number

    combined = _extract_label(
        text,
        ("项目经理相关证书及编号", "证书名称及编号", "证书名称和编号"),
    ).rstrip("；;。")
    if not combined:
        return "", ""
    match = re.match(
        r"^(.*?)[、,，]\s*([A-Za-z0-9][A-Za-z0-9./-]{3,})\s*$",
        combined,
    )
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return combined, ""


def _extract_acquisition_way(text: str) -> str:
    """从文件获取章节识别没有显式“获取方式”标签的下载句。"""

    value = _extract_label(text, ("获取方法", "获取方式", "发售方式"))
    if value:
        return value
    lines = [line.strip() for line in clean_html_keep_lines(text).splitlines() if line.strip()]
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if any(
                heading in line
                for heading in ("招标文件的获取", "资格预审文件的获取", "文件的获取")
            )
        ),
        -1,
    )
    if start < 0:
        return ""
    for line in lines[start:]:
        if any(stop in line for stop in ("投标文件的递交", "申请文件的递交", "开标时间")):
            break
        compact = re.sub(r"\s+", "", line)
        if "获取时间" in compact or "售价" in compact:
            continue
        if not re.search(r"(?:下载|在线获取|现场获取|领取).*(?:招标|预审)?文件", compact):
            continue
        return re.sub(
            r"^\s*\d+(?:\.\d+)*(?:[、.．])?\s*",
            "",
            line,
        ).strip(" ：:；;")
    return ""


def _infer_candidate_section(lines: Iterable[str]) -> str:
    """评标表没有标段表头时，仅在前文唯一指明标段时回填。"""

    source = "\n".join(lines)
    pattern = re.compile(
        r"(?<!\d)(?:\d{3}\s*(?:(?:第?[一二三四五六七八九十百\d]+|不分))?标段"
        r"|第[一二三四五六七八九十百\d]+标段|不分标段)"
    )
    matches: list[str] = []
    for match in pattern.finditer(source):
        value = re.sub(r"\s+", "", match.group(0))
        if value not in matches:
            matches.append(value)
    return matches[0] if len(matches) == 1 else ""


def _candidate_name_price_pairs(text: str) -> tuple[list[str], list[str]]:
    """从候选公示评标情况中成对提取候选人和报价，避免报价吞入工期。"""

    raw_lines = [line.strip() for line in clean_html_keep_lines(text).splitlines() if line.strip()]
    start = next((i + 1 for i, line in enumerate(raw_lines) if "评标情况" in line), 0)
    end = next(
        (
            i
            for i in range(start, len(raw_lines))
            if any(label in raw_lines[i] for label in ("提出异议", "其他公示内容", "监督部门", "联系方式"))
        ),
        len(raw_lines),
    )
    lines = raw_lines[start:end] or raw_lines
    section_pattern = re.compile(
        r"^((?:\d{3}[^：:\n]*?标段)|不分标段)\s*[：:]?\s*$"
    )
    indices = [
        (index, match.group(1).strip())
        for index, line in enumerate(lines)
        if (match := section_pattern.search(line))
    ]
    blocks: list[tuple[str, list[str]]] = []
    if indices:
        for position, (index, section_name) in enumerate(indices):
            block_end = indices[position + 1][0] if position + 1 < len(indices) else len(lines)
            blocks.append((section_name, lines[index + 1 : block_end]))
    else:
        blocks.append((_infer_candidate_section(raw_lines[:start]), lines))

    name_pattern = re.compile(
        r"(?:(?:推荐\s*)?第?[一二三123]?名?\s*中标候选人(?:名称)?|第[一二三123]中标候选人(?:名称)?)\s*[：:]\s*(.+)$"
    )
    # 玖邦常把名次、候选人和报价写在同一行，例如：
    # “第1名：甲公司,投标报价：782000元”。名次只用于识别记录，不写入公司名。
    rank_pattern = re.compile(
        r"^第\s*[一二三四五六七八九十百\d]+\s*名\s*[：:]\s*(.+)$"
    )
    inline_price_pattern = re.compile(
        r"(投标总报价|投标报价|响应报价|投标价格|报价)"
        r"(?:\s*[（(][^）)\n]{1,80}[）)])?\s*[：:]\s*([^，,；;]+)"
    )
    price_pattern = re.compile(
        r"(.{0,40}?(?:投标总报价|投标报价|响应报价|投标价格|报价)"
        r"(?:\s*[（(][^）)\n]{1,80}[）)])?)\s*[：:]\s*(.+)$"
    )
    names: list[str] = []
    prices: list[str] = []
    for section_name, block_lines in blocks:
        candidates: list[tuple[str, list[str]]] = []
        # 部分玖邦公告由表格转成纵向纯文本：
        # 排序 / 中标候选人名称 / 投标报价（万元） / 1 / 公司 / 3595.02。
        candidate_header = next(
            (
                index
                for index, line in enumerate(block_lines)
                if re.fullmatch(r"中标候选人(?:名称)?", line)
            ),
            -1,
        )
        price_header = next(
            (
                index
                for index in range(candidate_header + 1, min(len(block_lines), candidate_header + 4))
                if re.search(r"(?:投标)?报价", block_lines[index])
            ),
            -1,
        )
        if candidate_header >= 0 and price_header >= 0:
            unit_match = re.search(r"(亿元|万元|元|%|％)", block_lines[price_header])
            unit = unit_match.group(1) if unit_match else ""
            table_end = next(
                (
                    index
                    for index in range(price_header + 1, len(block_lines))
                    if block_lines[index] == "序号"
                    or re.match(
                        r"^\d+[、.．]\s*中标候选人(?:响应|按照|资格)",
                        block_lines[index],
                    )
                ),
                len(block_lines),
            )
            cursor = price_header + 1
            while cursor < table_end:
                if not re.fullmatch(r"\d+", block_lines[cursor]):
                    cursor += 1
                    continue
                if cursor + 1 >= table_end:
                    break
                candidate_name = block_lines[cursor + 1].strip(" ；;，,。")
                quote_lines: list[str] = []
                next_index = cursor + 2
                if next_index < table_end:
                    price_value = block_lines[next_index].strip(" ；;，,。")
                    numeric_price = re.fullmatch(
                        r"[-+]?\d[\d,，]*(?:\.\d+)?\s*(?:亿元|万元|元|%|％)?",
                        price_value,
                    )
                    # 整数报价同样合法。只有该数字恰好是下一顺位，且后面紧跟公司名时，
                    # 才把它视为“下一行排名”，用于兼容某候选人确实缺少报价的表格。
                    next_rank = (
                        re.fullmatch(r"\d+", price_value)
                        and int(price_value) == int(block_lines[cursor]) + 1
                        and next_index + 1 < table_end
                        and not re.fullmatch(r"\d+", block_lines[next_index + 1])
                    )
                    if numeric_price and not next_rank:
                        quote_lines.append(f"投标报价：{price_value}{unit}")
                        cursor = next_index + 1
                    else:
                        cursor = next_index
                else:
                    cursor = next_index
                if candidate_name:
                    candidates.append((candidate_name, quote_lines))

        active_index = -1
        for line in (() if candidates else block_lines):
            if re.search(r"中标候选人(?:基本情况|按照|响应|资格能力|公示)", line):
                continue
            rank_match = rank_pattern.search(line)
            if rank_match:
                ranked_content = rank_match.group(1).strip()
                inline_price_match = inline_price_pattern.search(ranked_content)
                quote_lines: list[str] = []
                if inline_price_match:
                    candidate_name = ranked_content[: inline_price_match.start()]
                    candidate_name = candidate_name.strip(" ；;，,。").strip()
                    price_value = inline_price_match.group(2).strip().rstrip("。")
                    quote_lines.append(
                        f"{inline_price_match.group(1)}：{price_value}"
                    )
                else:
                    candidate_name = ranked_content.strip(" ；;，,。").strip()
                if candidate_name:
                    candidates.append((candidate_name, quote_lines))
                    active_index = len(candidates) - 1
                continue
            name_match = name_pattern.search(line)
            if name_match:
                candidate_name = re.sub(r"\s+", " ", name_match.group(1)).strip(" ；;，,")
                candidates.append((candidate_name, []))
                active_index = len(candidates) - 1
                continue
            price_match = price_pattern.search(line)
            if price_match and active_index >= 0:
                candidates[active_index][1].append(
                    f"{price_match.group(1).strip()}：{price_match.group(2).strip()}"
                )
        deduplicated: list[tuple[str, list[str]]] = []
        positions: dict[str, int] = {}
        for candidate_name, quote_lines in candidates:
            key = re.sub(r"\s+", "", candidate_name)
            if key in positions:
                position = positions[key]
                if not deduplicated[position][1] and quote_lines:
                    deduplicated[position] = (candidate_name, quote_lines)
                continue
            positions[key] = len(deduplicated)
            deduplicated.append((candidate_name, quote_lines))

        for candidate_name, quote_lines in deduplicated:
            names.append(
                f"{section_name}：{candidate_name}" if section_name else candidate_name
            )
            prices.append(
                f"{section_name}：{'；'.join(quote_lines)}"
                if section_name and quote_lines
                else "；".join(quote_lines)
            )
    return names, prices


def _candidate_details(
    names: Iterable[Any],
    prices: Iterable[Any],
) -> list[dict[str, Any]]:
    """将名称和报价组合成稳定记录；缺少报价时保留空值而不移动位置。"""

    name_values = list(names)
    price_values = list(prices)
    result: list[dict[str, Any]] = []
    for index, raw_name in enumerate(name_values):
        name = _string(raw_name)
        if not name:
            continue
        section = ""
        candidate_name = name
        if "：" in name:
            possible_section, possible_name = name.split("：", 1)
            if re.match(
                r"^(?:\d{3}.*标段|第[一二三四五六七八九十百\d]+标段|不分标段)$",
                possible_section.strip(),
            ):
                section = possible_section.strip()
                candidate_name = possible_name.strip()
        raw_price = price_values[index] if index < len(price_values) else None
        amount = coerce_decimal_amount(raw_price)
        result.append(
            {
                "标段": section,
                "候选人名称": candidate_name,
                "候选人报价": (
                    amount
                    if amount is not None
                    else (_string(raw_price) or None)
                ),
            }
        )
    return result


def _award_price(value: Any, unit_code: Any = "") -> Any:
    """规范中标价；缺失时返回 None，特殊计价方式保留源站原文。"""

    price = _string(value)
    if not price:
        return None
    unit = _code(unit_code)
    if unit == "2":
        return coerce_decimal_amount(f"{price}万元")
    if unit in {"3", "4", "5"}:
        suffix = {"3": "（单价）", "4": "%", "5": "（其他）"}[unit]
        return f"{price}{suffix}"
    amount = coerce_decimal_amount(price)
    return amount if amount is not None else price


def _award_text_details(text: str) -> list[dict[str, Any]]:
    """从正文逐条提取中标结果，缺少价格时仍保留对应中标人。"""

    lines = [
        line.strip()
        for line in clean_html_keep_lines(text).splitlines()
        if line.strip()
    ]
    result: list[dict[str, Any]] = []
    section = ""
    section_pattern = re.compile(r"^(\d{3}[^：:\n]*?标段)\s*[：:]?\s*$")
    name_pattern = re.compile(r"^(?:中标人名称|中标人)\s*[：:]\s*(.+)$")
    price_pattern = re.compile(r"^(?:中标价格|中标价|中标金额)\s*[：:]\s*(.+)$")
    for line in lines:
        if result and any(
            label in line
            for label in ("其他公示内容", "监督部门", "联系方式")
        ):
            break
        if match := section_pattern.match(line):
            section = match.group(1).strip()
            continue
        if match := name_pattern.match(line):
            name = match.group(1).strip(" ；;，,")
            if name:
                result.append(
                    {"标段": section, "中标人名称": name, "中标价": None}
                )
            continue
        if match := price_pattern.match(line):
            if result and result[-1]["中标价"] is None:
                result[-1]["中标价"] = _award_price(match.group(1))
    return result


def _award_details(
    detail: Mapping[str, Any],
    text: str,
) -> list[dict[str, Any]]:
    """优先用 API 行对象构造明细，并利用正文/标段数组补充标段名称。"""

    deals = detail.get("bidAnnDealDOS") or []
    if not isinstance(deals, (list, tuple)) or not deals:
        return _award_text_details(text)

    text_sections: dict[str, str] = {}
    for line in clean_html_keep_lines(text).splitlines():
        match = re.match(r"^(\d{3}[^：:\n]*?标段)\s*[：:]?\s*$", line.strip())
        if match:
            label = match.group(1).strip()
            text_sections.setdefault(label[:3], label)

    section_by_id: dict[str, str] = {}
    for item in detail.get("bidAnnouncementSectionDOS") or []:
        if not isinstance(item, Mapping):
            continue
        code = _string(item.get("sectionCode"))
        name = _string(item.get("sectionName"))
        label = text_sections.get(code)
        if not label:
            label = f"{code}{name}" if code and name else (name or (f"{code}标段" if code else ""))
        for key in ("sectionOnlyId", "sectionId", "id"):
            identifier = _string(item.get(key))
            if identifier and label:
                section_by_id[identifier] = label

    result: list[dict[str, Any]] = []
    text_labels = list(text_sections.values())
    for index, deal in enumerate(deals):
        if not isinstance(deal, Mapping):
            continue
        name = _string(deal.get("dealName"))
        if not name:
            continue
        section = _string(deal.get("sectionName") or deal.get("bidSectionName"))
        if not section:
            for key in ("sectionOnlyId", "sectionId"):
                section = section_by_id.get(_string(deal.get(key)), "")
                if section:
                    break
        if not section and len(text_labels) == len(deals):
            section = text_labels[index]
        result.append(
            {
                "标段": section,
                "中标人名称": name,
                "中标价": _award_price(
                    deal.get("dealPrice"), deal.get("dealPriceUnit")
                ),
            }
        )
    return result


# 可复用的纯文本结果配对接口。山西省公共资源交易平台等 HTML 站点使用同一套
# “标段—名称—报价”规则，避免各站再次实现容易错位的平行数组解析。
candidate_name_price_pairs = _candidate_name_price_pairs
candidate_details = _candidate_details
award_details = _award_details


def _attachments(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    """收集页面附件，以及没有 HTML 正文时的 PDF 详情原件。

    详情页以 ``forms.fileId`` 是否存在决定是否展示“附件”，并通过
    ``fileobtain(false, forms.fileId, 5)`` 查询 bidding 文件服务。标段数组不作为
    页面附件来源。``pdfFile`` 是后台生成/保存的 PDF 版本：有 HTML 正文时不把它
    重复列为附件；只有详情不含 ``annContent/annContent2`` 时，才按 PDF 详情原件
    下载归档。
    """

    candidates: list[tuple[str, Any]] = []
    if detail.get("fileId"):
        candidates.append((_string(detail.get("fileName")), detail.get("fileId")))
    has_html_detail = bool(
        _string(detail.get("annContent")) or _string(detail.get("annContent2"))
    )
    if detail.get("pdfFile") and not has_html_detail:
        pdf_name = _string(detail.get("pdfFileName"))
        if not pdf_name:
            title = _string(
                detail.get("annTitle")
                or detail.get("annLastTitle")
                or detail.get("projectName")
            ) or "公告详情"
            pdf_name = f"{title}.pdf"
        elif not pdf_name.lower().endswith(".pdf"):
            pdf_name = f"{pdf_name}.pdf"
        candidates.append((pdf_name, detail.get("pdfFile")))

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, file_id in candidates:
        normalized_id = _string(file_id)
        if not normalized_id or normalized_id in seen:
            continue
        seen.add(normalized_id)
        result.append(
            {
                "source_file_id": normalized_id,
                # fileName 可能为空，但前端仍显示附件区域；后续文件查询接口可补名称。
                "file_name": name or None,
                "file_url": None,
                "storage_path": None,
                "file_hash": None,
                "file_size_bytes": None,
                "file_type": (
                    "application/pdf" if name.lower().endswith(".pdf") else None
                ),
                "parse_status": "PENDING",
            }
        )
    return result


class HuaxinParser:
    """把华新详情 JSON 转换为框架的八类公告字段。"""

    parser_version = "huaxin-v12-identifiers"
    platform_name = PLATFORM_NAME
    web_base_url = WEB_BASE_URL

    subtype_to_notice_type = {
        "zbjh": "招标计划",
        "zbys": "资格预审公告",
        "zbgg": "招标公告",
        "hxr": "中标候选人公示",
        "zbjg": "中标结果公示",
        "gzjg": "更正结果公示",
    }

    @classmethod
    def detect_subtype(cls, section: str, detail: Mapping[str, Any]) -> str:
        title = _string(detail.get("annTitle") or detail.get("annLastTitle"))
        if section == "zbjh":
            return "zbjh"
        if section == "zbgg_zys":
            if _code(detail.get("announcementType")) == "3" or (
                not detail.get("announcementType") and "资格预审" in title
            ):
                return "zbys"
            return "zbgg"
        if section == "hxr" or _code(detail.get("annClassification")) == "2":
            return "hxr"
        if section == "gs" or _code(detail.get("annClassification")) == "3":
            correction_words = ("更正中标结果", "撤销中标结果", "更正结果", "撤销结果", "更正", "撤销")
            if any(word in title for word in correction_words):
                return "gzjg"
            if "中标结果" in title or "中标人" in title:
                return "zbjg"
            return "gzjg" if _code(detail.get("annNature")) in {"4", "5"} else "zbjg"
        return ""

    @classmethod
    def parse(
        cls,
        section: str,
        detail: Mapping[str, Any],
    ) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
        subtype = cls.detect_subtype(section, detail)
        notice_type = cls.subtype_to_notice_type.get(subtype, "")
        if not notice_type:
            return "", "", {}, []

        data = create_empty_notice_data(
            notice_type, include_parser_diagnostics=True
        )
        extractor = getattr(cls, f"_extract_{subtype}")
        extractor(data, detail)
        attachments = _attachments(detail)
        data = canonicalize_notice_data(
            notice_type, data, include_parser_diagnostics=True
        )
        return subtype, notice_type, data, attachments

    @classmethod
    def detail_url(cls, subtype: str, detail: Mapping[str, Any]) -> str:
        identifier = _string(
            detail.get("_route_planid")
            or detail.get("annId")
            or detail.get("id")
            or detail.get("planId")
        )
        if not identifier:
            return ""
        if subtype == "zbjh":
            return f"{cls.web_base_url}/#/biddingplan?planid={identifier}"
        return f"{cls.web_base_url}/#/biddingdetails?annId={identifier}"

    @staticmethod
    def raw_html(detail: Mapping[str, Any]) -> str:
        return _string(detail.get("annContent") or detail.get("annContent2"))

    @staticmethod
    def raw_text(detail: Mapping[str, Any]) -> str:
        """返回可写入 raw_notice.raw_text / project_notice.content 的清洗正文。"""

        return _combine_text(detail)

    @classmethod
    def _common(cls, data: dict[str, Any], detail: Mapping[str, Any]) -> tuple[str, str]:
        text = _combine_text(detail)
        # 正文通常包含页面最终展示的完整“联系方式”章节；结构化联系方式HTML可能
        # 只含招标人或代理机构的一方，因此正文优先，并把两个HTML片段作为补充。
        contact_parts: list[str] = []
        for value in (
            detail.get("annContent") or detail.get("annContent2"),
            detail.get("bidContactInformation"),
            detail.get("contactInformation"),
        ):
            normalized = clean_html_keep_lines(value)
            if normalized and normalized not in contact_parts:
                contact_parts.append(normalized)
        contact = _normalize_contact_text("\n".join(contact_parts))
        if "项目性质" in data:
            data["项目性质"] = _string(
                detail.get("projectNatureName")
                or detail.get("projectNature")
                or detail.get("projectPropertyName")
                or detail.get("projectProperty")
            ) or _extract_label(text, ("项目性质",))
        if "源站公告性质" in data:
            data["源站公告性质"] = _source_notice_nature(detail)
        if "项目名称" in data:
            data["项目名称"] = _project_name(
                detail.get("annTitle")
                or detail.get("annLastTitle")
                or detail.get("purName")
                or detail.get("projectName")
            )
        if "项目编号" in data:
            project_labels = (
                "招标项目编号", "项目编号", "投资项目统一代码", "项目代码",
            )
            data["项目编号"] = _labelled_identifier(
                text,
                project_labels,
            ) or _clean_identifier_value(detail.get("diyProjectNo"), project_labels)
        if "招标编号" in data:
            tender_labels = ("招标编号", "采购编号", "代理编号")
            data["招标编号"] = _labelled_identifier(
                text,
                tender_labels,
            ) or _clean_identifier_value(detail.get("purDiyCode"), tender_labels)
        if "所属行业" in data:
            data["所属行业"] = _string(detail.get("industryName"))
        if "组织形式" in data:
            data["组织形式"] = _string(detail.get("annNum"))
        if "发布日期" in data:
            data["发布日期"] = _string(detail.get("releaseTime") or detail.get("createTime"))
        if "发布网站" in data:
            data["发布网站"] = _string(detail.get("mediaName")) or cls.platform_name
        return text, contact

    @classmethod
    def _fill_contacts(cls, data: dict[str, Any], detail: Mapping[str, Any], contact: str) -> None:
        # 正文联系方式章节是对外公示的最终值；顶层 API 字段可能只是集团或
        # 项目归属单位，因此仅在正文没有明确值时兜底。
        bidder_name = _join_distinct(
            _contact_value(contact, "bidder", "name"),
            detail.get("bidName"),
        )
        agent_name = _join_distinct(
            _contact_value(contact, "agent", "name"),
            detail.get("companyName"),
        )
        for field in ("招标人/采购人名称", "招标人/采购人"):
            if field in data:
                data[field] = bidder_name
        values = {
            "招标人地址": _contact_value(contact, "bidder", "address"),
            "招标人联系人": _contact_value(contact, "bidder", "contact"),
            "招标人联系方式": _contact_value(contact, "bidder", "phone"),
            "招标代理机构": agent_name,
            "招标代理机构地址": _contact_value(contact, "agent", "address"),
            "招标代理机构联系人": _contact_value(contact, "agent", "contact"),
            "招标代理机构联系方式": _contact_value(contact, "agent", "phone"),
        }
        for field, value in values.items():
            if field in data:
                data[field] = value

    @classmethod
    def _extract_zbjh(cls, data: dict[str, Any], detail: Mapping[str, Any]) -> None:
        cls._common(data, detail)
        project_type_map = {
            "01": "工业", "02": "国土资源", "03": "房屋市政", "04": "交通",
            "05": "水利", "06": "农业", "07": "广播电视", "08": "能源",
            "09": "文物保护", "10": "林业",
        }
        content_map = {"1": "勘察", "2": "设计", "3": "施工", "4": "监理", "5": "主要设备", "6": "重要材料"}
        data["招标方式"] = {"1": "公开招标", "2": "邀请招标"}.get(_code(detail.get("tenderMode")), "")
        data["项目名称"] = _string(detail.get("projectName") or detail.get("planTitle"))
        project_type = _string(detail.get("projectType"))
        data["项目类型"] = project_type_map.get(project_type, project_type)
        investment = _string(detail.get("contributionScale"))
        data["项目总投资"] = (
            coerce_decimal_amount(f"{investment}万元") if investment else None
        )
        content = [content_map.get(item.strip(), item.strip()) for item in _string(detail.get("tenderContent")).split(";") if item.strip()]
        data["招标内容"] = "、".join(content)
        data["招标人名称"] = _string(detail.get("legalPerson"))
        data["行政监督部门"] = _string(detail.get("superviseDeptName"))
        data["建设地点"] = _string(detail.get("projectAddress"))
        data["建设内容及规模"] = _string(detail.get("projectScale"))
        data["招标公告（资格预审公告）预计发布时间"] = _string(detail.get("noticePlanSendTime"))

    @classmethod
    def _extract_tender(cls, data: dict[str, Any], detail: Mapping[str, Any], *, prequalification: bool) -> None:
        text, contact = cls._common(data, detail)
        data["开标时间"] = _string(detail.get("submitDeadline")) or _extract_label(text, ("开标时间", "开启时间", "递交截止时间"))
        data["项目编号/招标编号"] = _project_numbers(detail, text)
        data["项目类型/行业分类"] = _string(detail.get("classificationName") or detail.get("industryName"))
        data["项目总投资/估算金额"] = _extract_label(text, ("项目总投资", "估算金额", "项目估算"))
        data["招标金额"] = _extract_label(
            text,
            (
                "招标金额", "招标控制价", "最高投标限价", "最高限价",
                "采购预算", "预算金额", "控制价",
            ),
        )
        funding_source = _extract_label(text, ("资金来源", "项目资金来源"))
        if not funding_source:
            match = re.search(
                r"(?:项目资金(?:来源)?|资金来源)(?:为|是|[：:])\s*([^，。；;\n]+)",
                text,
            )
            funding_source = match.group(1).strip() if match else ""
        data["资金来源"] = funding_source
        body_location = _extract_label(
            text,
            ("招标项目所在地区", "项目所在地区", "项目地点", "建设地点", "工程地点"),
        )
        api_location = _string(detail.get("administrativeName"))
        data["项目地点"] = _join_distinct(body_location, api_location)
        if "项目规模" in data:
            data["项目规模"] = _extract_label(text, ("项目规模", "建设规模", "工程规模"))
        if "工期/服务期/供货日期" in data:
            data["工期/服务期/供货日期"] = _extract_duration(text)
        if "质量要求" in data:
            data["质量要求"] = _extract_quality(text)
        overview = "\n".join(filter(None, (clean_html(detail.get("projectOverview")), clean_html(detail.get("bidOverview")))))
        if not overview:
            overview = _extract_tender_scope(text)
        overview_field = "项目概况与招标范围" if prequalification else "招标内容与范围"
        data[overview_field] = overview
        qualification = "\n".join(filter(None, (clean_html(detail.get("bidQualification")), clean_html(detail.get("consortiumQualification")))))
        if not qualification:
            qualification = _extract_section(text, ("投标人资格要求", "申请人资格要求", "资格要求"), ("招标文件的获取", "资格预审文件的获取", "文件的获取"))
        data["申请人资格要求/投标人资格要求"] = qualification
        data["预审文件获取时间"] = _format_range(detail.get("acquisitionStart"), detail.get("acquisitionEnd"))
        data["获取方式"] = _string(detail.get("acquisitionWay")) or _extract_acquisition_way(text)
        data["递交截止时间"] = _string(detail.get("submitDeadline"))
        data["递交方法"] = _string(detail.get("submitWay")) or _extract_label(text, ("递交方法", "递交方式"))
        data["开启时间"] = _string(detail.get("submitDeadline"))
        data["开启方式"] = ({"1": "远程开启", "2": "非远程开启"} if prequalification else {"1": "远程开标", "2": "非远程开标"}).get(_code(detail.get("openWay")), "")
        data["开启地点"] = _extract_label(text, ("开标地点", "开标地址", "开启地点"))
        data["评审办法"] = _string(detail.get("evaluationMethod")) or _extract_label(text, ("评审办法", "评标办法"))
        guarantee_method = _string(detail.get("ensureForm")) or _extract_label(
            text, ("投标保证金", "保证金")
        )
        if not guarantee_method:
            guarantee_method = _extract_section(
                text,
                ("提交投标保证金的形式", "投标保证金的形式"),
                ("提出异议", "其他公告内容", "监督部门", "联系方式"),
            )
        data["投标保证金方式"] = guarantee_method
        cls._fill_contacts(data, detail, contact)

    @classmethod
    def _extract_zbys(cls, data: dict[str, Any], detail: Mapping[str, Any]) -> None:
        cls._extract_tender(data, detail, prequalification=True)

    @classmethod
    def _extract_zbgg(cls, data: dict[str, Any], detail: Mapping[str, Any]) -> None:
        cls._extract_tender(data, detail, prequalification=False)

    @classmethod
    def _extract_hxr(cls, data: dict[str, Any], detail: Mapping[str, Any]) -> None:
        text, contact = cls._common(data, detail)
        data["开标时间"] = _extract_label(text, ("开标时间", "开标日期"))
        data["公示时间"] = _format_range(detail.get("publicityStart"), detail.get("publicityEnd")) or _extract_label(text, ("公示时间", "公示开始时间", "公示期"))
        data["招标编号/项目编号"] = _project_numbers(detail, text)
        candidates = detail.get("bidAnnCandidateDOS") or detail.get("candidateDOS") or []
        if candidates:
            names: list[str] = []
            prices: list[Any] = []
            for item in candidates:
                if not isinstance(item, Mapping):
                    continue
                name = _string(
                    item.get("candidateName")
                    or item.get("bidderName")
                    or item.get("name")
                )
                if not name:
                    continue
                section = _string(
                    item.get("sectionName")
                    or item.get("bidSectionName")
                    or item.get("section")
                )
                names.append(f"{section}：{name}" if section else name)
                prices.append(
                    item.get("bidPrice")
                    or item.get("quote")
                    or item.get("price")
                )
        else:
            names, prices = _candidate_name_price_pairs(text)
        details = _candidate_details(names, prices)
        data["中标候选人明细"] = details
        data["中标候选人名称"] = [
            f"{item['标段']}：{item['候选人名称']}"
            if item["标段"]
            else item["候选人名称"]
            for item in details
        ]
        data["中标候选人报价"] = [
            item["候选人报价"] for item in details
        ]
        cls._fill_contacts(data, detail, contact or text)

    @classmethod
    def _extract_zbjg(cls, data: dict[str, Any], detail: Mapping[str, Any]) -> None:
        text, contact = cls._common(data, detail)
        data["招标方式"] = {"1": "公开招标", "2": "邀请招标", "3": "公开预审", "4": "其他"}.get(_code(detail.get("announcementType")), "")
        details = _award_details(detail, text)
        data["中标结果明细"] = details
        data["中标人名称"] = [item["中标人名称"] for item in details]
        data["中标价"] = [item["中标价"] for item in details]
        data["工期"] = _extract_duration(text)
        data["项目经理"] = _extract_label(
            text, ("项目经理", "项目负责人")
        ).rstrip("；;。")
        certificate_name, certificate_number = _extract_manager_certificate(text)
        data["项目经理证书名称"] = certificate_name
        data["项目经理证书编号"] = certificate_number
        cls._fill_contacts(data, detail, contact)

    @classmethod
    def _extract_gzjg(cls, data: dict[str, Any], detail: Mapping[str, Any]) -> None:
        # 更正结果复用结果公示页面结构，只写当前 Schema 中存在的字段。
        text, contact = cls._common(data, detail)
        data["公共类型"] = _source_notice_nature(detail)
        data["公告内容"] = clean_html(detail.get("otherContent") or detail.get("otherAnnContent") or detail.get("annContent"))
        cls._fill_contacts(data, detail, contact)
