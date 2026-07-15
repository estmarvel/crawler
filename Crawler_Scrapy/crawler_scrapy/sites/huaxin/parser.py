"""华新阳光采购平台的纯数据解析器。

本模块不发送请求、不写文件，也不调用 AI。它迁移自旧版
``sjq/Crawler/huaxin/crawler.py`` 中已经由真实数据验证过的分类和字段规则，
由 Scrapy Spider 将列表字段与详情 JSON 合并后调用。
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any, Iterable, Mapping

from crawler_scrapy.schemas.notice_fields import (
    canonicalize_notice_data,
    coerce_decimal_amount,
    create_empty_notice_data,
)
from crawler_scrapy.sites.huaxin.config import PLATFORM_NAME, WEB_BASE_URL


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


def _extract_label(text: str, labels: Iterable[str]) -> str:
    """按“标签：值”提取，并在新章节或下一个标签处停止。"""

    lines = [line.strip() for line in clean_html_keep_lines(text).splitlines() if line.strip()]
    labels = tuple(labels)
    for index, line in enumerate(lines):
        for label in labels:
            match = re.search(
                rf"(?:^|\s)(?:\d+(?:\.\d+)*\s*)?{re.escape(label)}\s*[：:]\s*(.*)$",
                line,
            )
            if not match:
                continue
            chunks = [match.group(1).strip()] if match.group(1).strip() else []
            for following in lines[index + 1 :]:
                if re.match(r"^(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)[、.．]\s*", following):
                    break
                if any(re.match(rf"^{re.escape(other)}\s*[：:]", following) for other in labels):
                    break
                if re.match(
                    r"^(?:项目|招标|投标|开标|开启|递交|获取|联系人|联系方式|地址|电话|监督部门).{0,12}[：:]",
                    following,
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
        for line in clean_html_keep_lines(text).splitlines()
        if line.strip()
    ]
    heading = re.compile(
        r"^(?:(?:\d+(?:\.\d+)*)|[一二三四五六七八九十]+)?"
        r"[、.．]?\s*联系方式\s*$"
    )
    indices = [index for index, line in enumerate(lines) if heading.match(line)]
    return "\n".join(lines[indices[-1] + 1 :]) if indices else "\n".join(lines)


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
        "phone": ("招标代理机构联系方式", "招标人联系方式", "联系方式", "电话", "电 话"),
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
    source = "\n".join(
        filter(None, (_string(detail.get("diyProjectNo")), _string(detail.get("purDiyCode")), text))
    )
    for pattern in (r"E\d{19}", r"[A-Z]{2,10}-[A-Z]{2}\d{8}"):
        for match in re.finditer(pattern, re.sub(r"\s+", "", source)):
            if match.group(0) not in values:
                values.append(match.group(0))
    if not values:
        fallback = _string(detail.get("diyProjectNo") or detail.get("purDiyCode"))
        if fallback:
            values.append(fallback)
    return "；".join(values)


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
    section_pattern = re.compile(r"^(\d{3}[^：:\n]*?标段)\s*[：:]?\s*$")
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
        blocks.append(("", lines))

    name_pattern = re.compile(
        r"(?:(?:推荐\s*)?第?[一二三123]?名?\s*中标候选人(?:名称)?|第[一二三123]中标候选人(?:名称)?)\s*[：:]\s*(.+)$"
    )
    price_pattern = re.compile(
        r"(.{0,40}?(?:投标总报价|投标报价|响应报价|投标价格|报价))\s*[：:]\s*(.+)$"
    )
    names: list[str] = []
    prices: list[str] = []
    for section_name, block_lines in blocks:
        candidates: list[tuple[str, list[str]]] = []
        active_index = -1
        for line in block_lines:
            if re.search(r"中标候选人(?:基本情况|按照|响应|资格能力|公示)", line):
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
        for candidate_name, quote_lines in candidates:
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
            if re.match(r"^\d{3}.*标段$", possible_section.strip()):
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

        data = create_empty_notice_data(notice_type)
        extractor = getattr(cls, f"_extract_{subtype}")
        extractor(data, detail)
        attachments = _attachments(detail)
        data["附件"] = attachments
        data = canonicalize_notice_data(notice_type, data)
        return subtype, notice_type, data, data["附件"]

    @staticmethod
    def detail_url(subtype: str, detail: Mapping[str, Any]) -> str:
        identifier = _string(
            detail.get("_route_planid")
            or detail.get("annId")
            or detail.get("id")
            or detail.get("planId")
        )
        if not identifier:
            return ""
        if subtype == "zbjh":
            return f"{WEB_BASE_URL}/#/biddingplan?planid={identifier}"
        return f"{WEB_BASE_URL}/#/biddingdetails?annId={identifier}"

    @staticmethod
    def raw_html(detail: Mapping[str, Any]) -> str:
        return _string(detail.get("annContent") or detail.get("annContent2"))

    @staticmethod
    def raw_text(detail: Mapping[str, Any]) -> str:
        """返回可写入 raw_notice.raw_text / project_notice.content 的清洗正文。"""

        return _combine_text(detail)

    @staticmethod
    def _common(data: dict[str, Any], detail: Mapping[str, Any]) -> tuple[str, str]:
        text = _combine_text(detail)
        contact = clean_html_keep_lines(
            detail.get("bidContactInformation")
            or detail.get("contactInformation")
            or detail.get("annContent")
            or detail.get("annContent2")
        )
        if "项目性质" in data:
            data["项目性质"] = _string(
                detail.get("projectNatureName")
                or detail.get("projectNature")
                or detail.get("projectPropertyName")
                or detail.get("projectProperty")
            ) or _extract_label(text, ("项目性质",))
        if "项目名称" in data:
            data["项目名称"] = _project_name(
                detail.get("annTitle")
                or detail.get("annLastTitle")
                or detail.get("purName")
                or detail.get("projectName")
            )
        if "所属行业" in data:
            data["所属行业"] = _string(detail.get("industryName"))
        if "组织形式" in data:
            data["组织形式"] = _string(detail.get("annNum"))
        if "发布日期" in data:
            data["发布日期"] = _string(detail.get("releaseTime") or detail.get("createTime"))
        if "发布网站" in data:
            data["发布网站"] = _string(detail.get("mediaName")) or PLATFORM_NAME
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
        data["招标金额"] = _extract_label(text, ("招标金额", "最高限价", "最高投标限价"))
        funding_source = _extract_label(text, ("资金来源", "项目资金来源"))
        if not funding_source:
            match = re.search(
                r"项目资金来源(?:为|是|[：:])\s*([^，。；;\n]+)", text
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
            data["工期/服务期/供货日期"] = _extract_label(text, ("工期", "服务期", "供货期", "计划工期", "交货期"))
        if "质量要求" in data:
            data["质量要求"] = _extract_label(text, ("质量要求", "质量标准"))
        overview = "\n".join(filter(None, (clean_html(detail.get("projectOverview")), clean_html(detail.get("bidOverview")))))
        if not overview:
            overview = _extract_label(text, ("项目概况与招标范围", "招标内容与范围", "招标范围", "招标内容"))
        overview_field = "项目概况与招标范围" if prequalification else "招标内容与范围"
        data[overview_field] = overview
        qualification = "\n".join(filter(None, (clean_html(detail.get("bidQualification")), clean_html(detail.get("consortiumQualification")))))
        if not qualification:
            qualification = _extract_section(text, ("投标人资格要求", "申请人资格要求", "资格要求"), ("招标文件的获取", "资格预审文件的获取", "文件的获取"))
        data["申请人资格要求/投标人资格要求"] = qualification
        data["预审文件获取时间"] = _format_range(detail.get("acquisitionStart"), detail.get("acquisitionEnd"))
        data["获取方式"] = _string(detail.get("acquisitionWay")) or _extract_label(text, ("获取方法", "获取方式", "发售方式"))
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
        cls._fill_contacts(data, detail, contact)

    @classmethod
    def _extract_gzjg(cls, data: dict[str, Any], detail: Mapping[str, Any]) -> None:
        # 更正结果复用结果公示页面结构，只写当前 Schema 中存在的字段。
        text, contact = cls._common(data, detail)
        nature = _code(detail.get("annNature"))
        data["公共类型"] = {"4": "更正中标结果", "5": "撤销中标结果"}.get(nature, "")
        data["公告内容"] = clean_html(detail.get("otherContent") or detail.get("otherAnnContent") or detail.get("annContent"))
        cls._fill_contacts(data, detail, contact)
