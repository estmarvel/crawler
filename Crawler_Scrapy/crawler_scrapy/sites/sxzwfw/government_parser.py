"""山西省公共资源交易平台政府采购公告解析器。

当前只处理 ``channelId=19`` 更正公告和 ``channelId=20`` 中标结果公告。
采购公告 ``channelId=18`` 暂未接入，避免在采购专用字段尚未确定时错误落入现有字段。
模块只解析离线 HTML，不发送请求、不写文件，也不调用 AI。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from crawler_scrapy.schemas.notice_fields import (
    canonicalize_notice_data,
    create_empty_notice_data,
)
from crawler_scrapy.sites.sxzwfw import config
from crawler_scrapy.sites.sxzwfw.parser import (
    ParsedNotice,
    _approval,
    _clean_project_name,
    _cms_attachment,
    _contacts,
    _direct_attachments,
    _first_text,
    _join_distinct,
    _label_value,
    _parse_document,
    _section,
    _space,
    _string,
    _time_range,
    visible_content_text,
)


def _semantic_text(document, class_name: str) -> str:
    nodes = document.cssselect(f".{class_name}")
    return _space("".join(nodes[0].itertext())) if nodes else ""


def _inline_label(text: str, labels: Iterable[str]) -> str:
    """兼容“一、项目编号：...”等带编号前缀的政府采购模板。"""

    for line in text.splitlines():
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}\s*[：:]\s*(.+)$",
                line,
            )
            if match:
                return _space(match.group(1)).strip("；;。")
    return ""


def _source_nature(section: str, title: str) -> str:
    if section == "zc_jg":
        nature = "成交" if "成交" in title else "中标结果"
    else:
        rules = (
            ("废标", "废标"),
            ("终止", "终止"),
            ("撤销", "撤销"),
            ("延期", "延期"),
            ("澄清", "澄清"),
            ("变更", "变更"),
            ("更正", "更正"),
        )
        nature = next((value for keyword, value in rules if keyword in title), "更正")
    channel_id, channel_name = config.SECTION_CHANNELS[section]
    return f"{nature}（{channel_name},channelId={channel_id}）"


def _project_number(document, text: str) -> str:
    return (
        _semantic_text(document, "code-00004")
        or _inline_label(text, ("项目编号", "采购项目编号", "招标编号"))
    )


def _project_name(document, text: str, title: str) -> str:
    return (
        _semantic_text(document, "code-00003")
        or _inline_label(text, ("项目名称", "采购项目名称"))
        or _clean_project_name(title)
    )


def _row_cells(row) -> list[str]:
    # 只取当前 tr 的直接单元格。政府采购详情外层还有包裹整个正文的 table/tr，
    # 如果使用后代选择器会把内层结果行重复识别一次。
    return [
        _space("".join(cell.itertext()))
        for cell in row.xpath("./th[not(.//table)]|./td[not(.//table)]")
    ]


def _direct_class_cells(row, class_name: str):
    expression = (
        "./th[contains(concat(' ', normalize-space(@class), ' '), $class_token)]|"
        "./td[contains(concat(' ', normalize-space(@class), ' '), $class_token)]"
    )
    return row.xpath(expression, class_token=f" {class_name} ")


def _award_rows(document) -> list[dict[str, Any]]:
    """按同一表格行提取供应商和金额，绝不依靠两个独立数组的位置猜测。"""

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in document.cssselect("tr"):
        names = _direct_class_cells(row, "code-winningSupplierName")
        if not names:
            continue
        name = _space("".join(names[0].itertext()))
        if not name:
            continue
        section_nodes = _direct_class_cells(row, "code-sectionNo")
        amount_nodes = _direct_class_cells(row, "code-summaryPrice")
        section = _space("".join(section_nodes[0].itertext())) if section_nodes else ""
        amount = _space("".join(amount_nodes[0].itertext())) if amount_nodes else ""
        identity = (section, name, amount)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
            {
                "标段": section,
                "中标人名称": name,
                "中标价": amount or None,
            }
        )
    if result:
        return result

    # 兼容没有政采云语义 class、但表头仍明确的旧模板。
    for table in document.cssselect("table"):
        rows = table.cssselect("tr")
        header_index = -1
        name_index = -1
        amount_index = -1
        section_index = -1
        for index, row in enumerate(rows):
            cells = _row_cells(row)
            for cell_index, value in enumerate(cells):
                compact = re.sub(r"\s+", "", value)
                if "供应商名称" in compact or "中标人名称" in compact:
                    name_index = cell_index
                if any(label in compact for label in ("中标（成交）金额", "中标成交金额", "中标金额", "成交金额")):
                    amount_index = cell_index
                if compact in {"序号", "标项", "标项名称", "包号", "标包"}:
                    section_index = cell_index
            if name_index >= 0 and amount_index >= 0:
                header_index = index
                break
        if header_index < 0:
            continue
        for row in rows[header_index + 1 :]:
            cells = _row_cells(row)
            if name_index >= len(cells):
                continue
            name = cells[name_index]
            if not name:
                continue
            result.append(
                {
                    "标段": cells[section_index] if 0 <= section_index < len(cells) else "",
                    "中标人名称": name,
                    "中标价": cells[amount_index] if amount_index < len(cells) else None,
                }
            )
        if result:
            break
    return result


def _table_column_value(document, labels: Iterable[str]) -> str:
    wanted = tuple(re.sub(r"\s+", "", value) for value in labels)
    for table in document.cssselect("table"):
        rows = table.cssselect("tr")
        for index, row in enumerate(rows[:-1]):
            headers = _row_cells(row)
            values = _row_cells(rows[index + 1])
            for column, header in enumerate(headers):
                compact = re.sub(r"\s+", "", header)
                if any(label in compact for label in wanted) and column < len(values):
                    return values[column]
    return ""


def _contact_blocks(text: str) -> dict[str, str]:
    """按采购人、代理机构、项目联系方式三个角色边界提取，避免相互污染。"""

    lines = [_space(value) for value in text.splitlines() if _space(value)]

    def block(start_keyword: str, stop_keywords: tuple[str, ...]) -> list[str]:
        start = next(
            (index for index, value in enumerate(lines) if start_keyword in re.sub(r"\s+", "", value)),
            -1,
        )
        if start < 0:
            return []
        result: list[str] = []
        for value in lines[start + 1 :]:
            compact = re.sub(r"\s+", "", value)
            if any(keyword in compact for keyword in stop_keywords):
                break
            result.append(value)
        return result

    purchaser = block("采购人信息", ("采购代理机构信息", "项目联系方式"))
    agent = block("采购代理机构信息", ("项目联系方式",))
    project = block("项目联系方式", ())

    def value(values: list[str], labels: tuple[str, ...]) -> str:
        for line in values:
            compact = re.sub(r"\s+", "", line)
            for label in labels:
                match = re.search(rf"{re.escape(label)}[：:](.+)$", compact)
                if match:
                    return _space(match.group(1)).strip("；;。")
        return ""

    fallback = _contacts(text)
    return {
        "purchaser_name": value(purchaser, ("名称", "采购人")) or fallback["bidder_name"],
        "purchaser_address": value(purchaser, ("地址",)) or fallback["bidder_address"],
        "purchaser_contact": value(purchaser, ("联系人",)) or fallback["bidder_contact"],
        "purchaser_phone": value(purchaser, ("联系方式", "联系电话", "电话")) or fallback["bidder_phone"],
        "agent_name": value(agent, ("名称", "代理机构")) or fallback["agent_name"],
        "agent_address": value(agent, ("地址",)) or fallback["agent_address"],
        "agent_contact": value(agent, ("联系人",)) or value(project, ("项目联系人", "联系人")) or fallback["agent_contact"],
        "agent_phone": value(agent, ("联系方式", "联系电话", "电话")) or value(project, ("电话", "联系方式")) or fallback["agent_phone"],
    }


def _procurement_method(title: str, text: str) -> str:
    # “评审专家（单一来源采购人员）名单”是固定模板说明，不能据此推断采购方式。
    explicit = _inline_label(text, ("采购方式",))
    combined = f"{title}\n{explicit}"
    for value in (
        "公开招标", "邀请招标", "竞争性磋商", "竞争性谈判", "询价",
        "单一来源", "框架协议", "电子卖场",
    ):
        if value in combined:
            return value
    return ""


class SxzwfwGovernmentProcurementParser:
    """把政府采购更正、结果 HTML 转换为框架预设公告字段。"""

    parser_version = "sxzwfw-zfcg-v1"
    extraction_model_name = "sxzwfw-zfcg-rule-parser"
    supported_sections = frozenset(config.GOVERNMENT_SECTION_CHANNELS)

    @classmethod
    def parse(
        cls,
        section: str,
        html_value: bytes | str,
        list_record: Mapping[str, Any],
        detail_url: str,
    ) -> ParsedNotice:
        if section not in cls.supported_sections:
            raise ValueError(f"政府采购解析器不支持栏目：{section}")

        document = _parse_document(html_value)
        raw_text = visible_content_text(html_value)
        title = _first_text(document, ".cs_title_P1") or _string(list_record.get("title"))
        header = _first_text(document, ".cs_title_P3")
        publish_match = re.search(
            r"发布日期\s*[：:]\s*([0-9]{4}[-/.年][0-9]{1,2}[-/.月][0-9]{1,2}(?:日)?"
            r"(?:\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?)",
            header,
        )
        publish_time = publish_match.group(1) if publish_match else _string(list_record.get("publish_time"))
        source_match = re.search(r"信息来源\s*[：:]\s*(.+)$", header)
        info_source = _space(source_match.group(1)) if source_match else ""
        source_nature = _source_nature(section, title)
        notice_type = "更正结果公示" if section == "zc_gz" else "中标结果公示"
        subtype = "gzjg" if section == "zc_gz" else "zbjg"
        data = create_empty_notice_data(notice_type)
        project_name = _project_name(document, raw_text, title)
        contacts = _contact_blocks(raw_text)

        if section == "zc_gz":
            data["公共类型"] = source_nature
            data["项目名称"] = project_name
            data["公告内容"] = raw_text
            data["开标时间"] = _inline_label(
                raw_text,
                ("开标时间", "开启时间", "响应文件提交截止时间"),
            )
            data["标书发售时间"] = _time_range(
                raw_text,
                ("获取采购文件时间", "采购文件获取时间", "获取时间"),
            )
            data["招标人地址"] = contacts["purchaser_address"]
            data["招标人联系人"] = contacts["purchaser_contact"]
            data["招标人联系方式"] = contacts["purchaser_phone"]
            data["招标代理机构"] = contacts["agent_name"]
            data["招标代理机构地址"] = contacts["agent_address"]
            data["招标代理机构联系人"] = contacts["agent_contact"]
            data["招标代理机构联系方式"] = contacts["agent_phone"]
            data["依据文件"], data["依据文号"] = _approval(raw_text)
            supervision = _section(raw_text, ("监督部门", "监督单位"), ("联系方式",))
            data["监督部门地址"] = _label_value(supervision, ("地址", "联系地址"))
            data["监督部门联系人"] = _label_value(supervision, ("联系人",))
            data["监督部门联系方式"] = _label_value(supervision, ("电话", "联系电话", "联系方式"))
        else:
            details = _award_rows(document)
            data["源站公告性质"] = source_nature
            data["项目名称"] = project_name
            data["招标编号/项目编号"] = _project_number(document, raw_text)
            data["招标方式"] = _procurement_method(title, raw_text)
            data["中标结果明细"] = details
            data["中标人名称"] = [value["中标人名称"] for value in details]
            data["中标价"] = [value["中标价"] for value in details]
            data["工期"] = _table_column_value(
                document,
                ("服务时间", "合同履行期限", "交付时间", "供货时间", "工期"),
            )
            data["招标人/采购人"] = contacts["purchaser_name"]
            data["招标人地址"] = contacts["purchaser_address"]
            data["招标人联系人"] = contacts["purchaser_contact"]
            data["招标人联系方式"] = contacts["purchaser_phone"]
            data["招标代理机构"] = contacts["agent_name"]
            data["招标代理机构地址"] = contacts["agent_address"]
            data["招标代理机构联系人"] = contacts["agent_contact"]
            data["招标代理机构联系方式"] = contacts["agent_phone"]

        data["发布日期"] = publish_time
        data["发布网站"] = _join_distinct(config.PLATFORM_NAME, info_source)

        direct = _direct_attachments(document, detail_url)
        id_match = re.search(r"/(\d+)\.jhtml(?:$|[?#])", detail_url)
        fallback_id = id_match.group(1) if id_match else _string(list_record.get("notice_id"))
        cms_info, placeholders = _cms_attachment(
            html_value.decode("utf-8", errors="replace") if isinstance(html_value, bytes) else str(html_value),
            document,
            fallback_id,
        )
        direct_ids = {value["source_file_id"] for value in direct}
        attachments = direct + [
            value for value in placeholders if value["source_file_id"] not in direct_ids
        ]
        data["附件"] = attachments
        normalized = canonicalize_notice_data(notice_type, data)
        return ParsedNotice(
            subtype=subtype,
            notice_type=notice_type,
            title=title,
            publish_time=publish_time,
            source_nature=source_nature,
            raw_text=raw_text,
            data=normalized,
            attachments=normalized["附件"],
            cms_attachment=cms_info,
        )
