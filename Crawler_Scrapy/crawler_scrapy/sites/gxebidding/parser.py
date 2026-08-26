"""国信 e 采列表、PDF 容器和 PDF 文字层解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from lxml import html as lxml_html

from crawler_scrapy.sites.bitbid.parser import BitbidParser, valid_identifier
from crawler_scrapy.sites.gxebidding import config
from crawler_scrapy.sites.sxbid.parser import extract_pdf_text


@dataclass(frozen=True)
class ListRecord:
    cms_id: str
    path_family: str
    title: str
    publish_time: str
    deadline: str
    tender_method: str
    detail_url: str


@dataclass(frozen=True)
class DetailDocument:
    pdf_url: str
    file_id: str
    file_type: str


@dataclass
class ParsedNotice:
    notice_type: str
    title: str
    publish_time: str
    raw_text: str
    data: dict[str, Any]
    attachments: list[dict[str, Any]]
    validation_warnings: list[str]


def _document(value: bytes | str):
    # 该站部分页面没有在响应头声明 charset，lxml 会回退为 latin-1；页面
    # 实际固定为 UTF-8，因此先显式解码，避免标题和“共N页”变成乱码。
    source = (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value or "")
    )
    return lxml_html.fromstring(source or "<html></html>")


def _space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def normalize_pdf_text(value: str) -> str:
    text = str(value or "").replace("\x0c", "\n")
    for compact in (
        "招标人", "采购人", "招标代理机构", "采购代理机构", "地址",
        "联系地址", "联系人", "联系电话", "电话", "招标项目编号",
        "采购项目编号", "投资项目统一代码", "项目代码", "项目编号", "招标编号",
    ):
        text = re.sub(r"\s*".join(map(re.escape, compact)), compact, text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def parse_list_records(value: bytes | str) -> list[ListRecord]:
    root = _document(value)
    records: list[ListRecord] = []
    seen: set[tuple[str, str]] = set()
    for anchor in root.xpath("//ul[contains(@class,'newslist')]//li/a[@href]"):
        href = str(anchor.get("href") or "").strip()
        matched = re.search(
            r"/sdny_([^/]+)/\d{4}-\d{2}-\d{2}/(\d+)\.html(?:[?#]|$)",
            href,
            re.I,
        )
        if not matched:
            continue
        path_name, cms_id = matched.groups()
        path_family = {
            "bulletin": "bulletin",
            "changebulletin": "change",
            "winningperson": "candidate",
            "resultbulletin": "result",
            "failbulletin": "fail",
        }.get(path_name.lower(), path_name.lower())
        identity = (path_family, cms_id)
        if identity in seen:
            continue
        seen.add(identity)
        title = _space(anchor.get("title")) or _space(
            " ".join(anchor.xpath(".//h5//text()"))
        )
        publish_time = _space(
            " ".join(anchor.xpath(".//*[contains(@class,'newsDate')]//text()"))
        )
        labels: dict[str, str] = {}
        for dd in anchor.xpath(".//dl[contains(@class,'newsinfo')]//dd"):
            raw = _space(dd.text_content())
            if "：" in raw:
                label, content = raw.split("：", 1)
                labels[label.strip()] = content.strip()
        records.append(ListRecord(
            cms_id=cms_id,
            path_family=path_family,
            title=title,
            publish_time=publish_time,
            deadline=labels.get("报名截止时间", ""),
            tender_method=labels.get("招标方式", ""),
            detail_url=href,
        ))
    return records


def parse_page_info(value: bytes | str) -> tuple[int, int]:
    text = _space(_document(value).text_content())
    total = re.search(r"共\s*(\d+)\s*页", text)
    current = re.search(r"当前页是第\s*(\d+)\s*页", text)
    return (
        int(total.group(1)) if total else 0,
        int(current.group(1)) if current else 0,
    )


def parse_detail_document(value: bytes | str) -> DetailDocument:
    root = _document(value)
    sources = root.xpath("//iframe[@id='pdfContainer']/@src") or root.xpath(
        "//iframe[contains(@src,'openFileById')]/@src"
    )
    for source in sources:
        decoded = unquote(unquote(str(source)))
        matched = re.search(
            r"openFileById\?fileType=(\d+)&id=([0-9a-fA-F-]+)", decoded
        )
        if matched:
            file_type, file_id = matched.groups()
            return DetailDocument(
                pdf_url=config.pdf_url(file_type, file_id),
                file_id=file_id,
                file_type=file_type,
            )
    return DetailDocument(pdf_url="", file_id="", file_type="")


def _clean_project_title(title: str) -> str:
    text = _space(title).strip()
    text = re.sub(
        r"(?:(?:二次|重新|再次)?招标公告|(?:二次|重新|再次)?采购公告|"
        r"(?:二次|重新|再次)?询比采购公告|(?:二次|重新|再次)?谈判采购公告|"
        r"(?:二次|重新|再次)?询价采购公告|中标候选人公示|"
        r"(?:重新|二次|再次)?(?:询比|询价|谈判|磋商)?采购成交候选人公示|"
        r"成交候选人公示|中标候选人公示更正|成交候选人公示更正|"
        r"中标结果公示更正|成交结果公示更正|中标结果公示|中标结果公告|"
        r"成交结果公示|成交结果公告|变更公告|更正公告|终止公告|废标公告|"
        r"流标公告|招标控制价(?:公告)?|最高投标限价(?:公告)?)$",
        "",
        text,
    ).strip()
    # 源站候选/结果标题常为“[项目]（第一标段） 中标结果公示”。只使用
    # str.strip 会留下项目末尾的单独右方括号，因此必须成对移除包裹符号，
    # 同时保留其后的标段信息。
    text = re.sub(r"^[\[【]\s*(.*?)[\]】](\s*[（(].*?[）)])?$", r"\1\2", text)
    text = re.sub(r"(?:二次|重新|再次)$", "", text).strip()
    return text.strip("[]【】 ")


def _identifier(text: str, *labels: str) -> str:
    value = BitbidParser._identifier_label(text, *labels)
    return value if valid_identifier(value) else ""


def _identifiers(text: str) -> tuple[str, str]:
    """区分源站模板里的项目主键和本次采购/招标编号。

    少数 PDF 同时写“项目编号：代理编号”和“招标项目编号：平台项目代码”。
    前者不能覆盖后者；当没有显式招标/采购编号时，应将不同的普通项目编号
    作为招标编号兜底，保持数据库的项目编号优先关联语义。
    """

    explicit_project = _identifier(
        text,
        "招标项目编号",
        "采购项目编号",
        "投资项目统一代码",
        "项目代码",
    )
    generic_project = _identifier(text, "项目编号")
    tender_number = _identifier(text, "招标编号", "采购编号", "代理编号")
    if (
        not tender_number
        and explicit_project
        and generic_project
        and generic_project != explicit_project
    ):
        tender_number = generic_project
    return explicit_project or generic_project, tender_number


def _contacts(text: str, *, award: bool) -> dict[str, str]:
    contacts = BitbidParser._contacts(text)
    agency = contacts.get("agency", {})
    if agency and not agency.get("contact"):
        agency["contact"] = BitbidParser._label(
            text[text.find("招标代理机构") :], "项目经理", "项目负责人"
        )
    return BitbidParser._contact_fields(contacts, award=award)


def _raw_table_candidate_details(text: str) -> list[dict[str, str]]:
    """从 ``pdftotext -raw`` 的逐单元格文本恢复跨行候选人表格。"""

    basic = BitbidParser._section(
        text,
        ("中标候选人基本情况", "成交候选人基本情况"),
        ("中标候选人按照", "成交候选人按照", "候选人按照"),
    )
    lines = [line.strip() for line in basic.splitlines() if line.strip()]
    ranks = [index for index, line in enumerate(lines) if re.fullmatch(r"\d{1,2}", line)]
    rows: list[dict[str, str]] = []
    for position, start in enumerate(ranks):
        end = ranks[position + 1] if position + 1 < len(ranks) else len(lines)
        block = lines[start + 1 : end]
        amount_index = -1
        amount = ""
        for index, line in enumerate(block):
            matched = re.match(
                r"^[￥¥]?\s*([\d,.，]+(?:\.\d+)?)\s*"
                r"(亿元|万元|元(?:[/／][^\s）)]{1,12})?|%|％)?(?:\s|$)",
                line,
            )
            if matched:
                amount_index = index
                amount = f"{matched.group(1)}{matched.group(2) or ''}"
                break
        if amount_index <= 0:
            continue
        name = re.sub(r"\s+", "", "".join(block[:amount_index])).strip("，,；;：:")
        if not re.search(
            r"(?:有限责任公司|股份有限公司|集团有限公司|有限公司|公司|集团|"
            r"研究院|设计院|研究所|中心|厂|院|联合体)(?:[）)])?$",
            name,
        ):
            continue
        rows.append({"标段": "", "候选人名称": name, "候选人报价": amount})
    return rows


def _candidate_details(text: str, table_text: str = "") -> list[dict[str, str]]:
    prose_rows: list[dict[str, str]] = []
    prose_pattern = re.compile(
        r"第[一二三四五六七八九十\d]+(?:中标|成交)候选人\s*[：:]\s*"
        r"([^，,；;\n]{2,180}?(?:公司|集团|厂|院|中心|联合体))\s*[，,；;]\s*"
        r"(?:含税价|投标报价|响应报价|报价)\s*([\d,.，]+(?:\.\d+)?\s*(?:亿元|万元|元|%|％)?)"
    )
    for matched in prose_pattern.finditer(table_text or text):
        prose_rows.append({
            "标段": "",
            "候选人名称": _space(matched.group(1)),
            "候选人报价": _space(matched.group(2)),
        })
    if prose_rows:
        return prose_rows

    basic = BitbidParser._section(
        text,
        ("中标候选人基本情况", "成交候选人基本情况"),
        ("中标候选人按照", "成交候选人按照", "候选人按照"),
    )
    qualification = BitbidParser._section(
        text,
        ("中标候选人响应招标文件要求的资格能力条件", "成交候选人响应采购文件要求的资格能力条件", "候选人响应招标文件要求的资格能力条件"),
        ("提出异议", "其他公示内容", "监督部门", "联系方式"),
    )
    names: dict[int, str] = {}
    company_suffix = r"(?:有限责任公司|股份有限公司|集团有限公司|有限公司|公司|集团|研究院|设计院|研究所|中心|厂|院)"
    for line in qualification.splitlines():
        matched = re.match(
            rf"^\s*(\d{{1,2}})\s+(.{{2,180}}?{company_suffix})\s+(?:响应|满足|符合)\s*$",
            _space(line),
        )
        if matched:
            names[int(matched.group(1))] = matched.group(2).strip()

    amounts: dict[int, str] = {}
    unit_match = re.search(
        r"(?:报价|价格|市场价|下浮率)[\s\S]{0,100}?[（(]\s*"
        r"(亿元|万元|元(?:[/／][^\s）)]{1,12})?|%|％)\s*[）)]",
        basic[:800],
    )
    if not unit_match:
        unit_match = re.search(r"[（(]\s*(%|％)\s*[）)]", basic[:800])
    unit = unit_match.group(1) if unit_match else (
        "%" if "下浮率" in basic[:800] else ""
    )
    for line in basic.splitlines():
        compact = _space(line)
        matched = re.match(r"^(\d{1,2})\s+(.+)$", compact)
        if not matched:
            continue
        rank = int(matched.group(1))
        tail = matched.group(2)
        numeric = re.search(r"(?<![A-Za-z])([\d,]+(?:\.\d+)?)(?![A-Za-z])", tail)
        if numeric:
            amounts.setdefault(rank, f"{numeric.group(1)}{unit}")
            prefix = tail[: numeric.start()].strip()
            if prefix and re.search(company_suffix + r"$", prefix):
                names.setdefault(rank, prefix)

    if not names:
        raw_rows = _raw_table_candidate_details(table_text) if table_text else []
        if raw_rows:
            return raw_rows
        fallback = BitbidParser._candidate_details(text)
        return fallback
    return [
        {
            "标段": "",
            "候选人名称": names[rank],
            "候选人报价": amounts.get(rank, ""),
        }
        for rank in sorted(names)
    ]


def _award_details(text: str, table_text: str = "") -> list[dict[str, str]]:
    name_pattern = re.compile(
        r"(?:中标单位|中标人(?:名称)?|成交供应商|成交单位|成交人(?:名称)?)"
        r"\s*[：:]\s*"
    )
    price_pattern = re.compile(
        r"(?:中标金额|中标价格|中标价|成交金额|成交价格|成交价)"
        r"(?:\s*[（(][^）)\n]{0,40}[）)])?\s*[：:]\s*"
    )
    normalized_sources = []
    for source in (text, table_text):
        if not source:
            continue
        source = re.sub(r"中\s*标\s*人", "中标人", source)
        source = re.sub(r"成\s*交\s*人", "成交人", source)
        source = re.sub(r"中\s*标\s*(?:价格|价)", "中标价格", source)
        source = re.sub(r"成\s*交\s*(?:价格|价)", "成交价格", source)
        normalized_sources.append(source)
    candidates: list[list[dict[str, str]]] = []
    for source in normalized_sources:
        source_rows: list[dict[str, str]] = []
        for matched in name_pattern.finditer(source):
            block = source[matched.end() :]
            section_end = re.search(r"(?m)^\s*(?:二|2)[、.]", block)
            block = block[: section_end.start()] if section_end else block[:1000]
            price_match = price_pattern.search(block)
            if price_match:
                raw_name = block[: price_match.start()]
                price_tail = block[price_match.end() :]
                price = next(
                    (line.strip() for line in price_tail.splitlines() if line.strip()),
                    "",
                )
            else:
                raw_name = next(
                    (line.strip() for line in block.splitlines() if line.strip()),
                    "",
                )
                price = ""
            name = re.sub(r"\s+", "", raw_name).strip(" ，,。；;")
            name = re.sub(r"^(?:联合体)?牵头人\s*[：:]\s*", "", name)
            name = re.split(
                r"[（(]?\s*联合体成员\s*[：:]", name, maxsplit=1
            )[0].strip("（( ")
            price = price.strip(" ，,。；;")
            if name and not any(row["中标人名称"] == name for row in source_rows):
                source_rows.append({"标段": "", "中标人名称": name, "中标价": price})
        if source_rows:
            candidates.append(source_rows)
    rows = max(
        candidates,
        key=lambda values: (sum(len(row["中标人名称"]) for row in values), len(values)),
        default=[],
    )
    if rows:
        if len(rows) == 1 and not rows[0]["中标价"]:
            rows[0]["中标价"] = BitbidParser._label(
                text, "中标金额", "中标价格", "中标价", "成交金额", "成交价格"
            )
        return rows
    return BitbidParser._award_details(text)


def _consortium_members(text: str, table_text: str = "") -> list[str]:
    for source in (table_text, text):
        if not source:
            continue
        matched = re.search(
            r"联合体成员\s*[：:]\s*(.{2,400}?)"
            r"(?=[）)]?\s*\n\s*(?:中\s*标\s*(?:金额|价格|价)|成\s*交\s*(?:金额|价格|价)|二[、.]|2[、.]))",
            source,
            re.S,
        )
        if not matched:
            continue
        value = re.sub(r"\s+", "", matched.group(1)).strip("，,；;、）)")
        members = [
            part.strip()
            for part in re.split(r"[、,，;；]", value)
            if part.strip()
        ]
        if members:
            return members
    return BitbidParser._list_label(text, "联合体成员")


class GxebiddingParser:
    parser_version = "gxebidding-v2-validated-html-pdf"

    @classmethod
    def parse(
        cls,
        channel: str,
        category: str,
        list_record: Mapping[str, Any],
        detail: DetailDocument,
        *,
        pdf_text: str = "",
        table_text: str = "",
    ) -> ParsedNotice:
        if channel not in config.CHANNELS or category not in config.CATEGORIES:
            raise ValueError(f"不支持的国信e采来源：{channel}/{category}")
        text = normalize_pdf_text(pdf_text)
        table = normalize_pdf_text(table_text)
        title = _space(list_record.get("title"))
        publish_time = _space(list_record.get("publish_time"))
        project_number, tender_number = _identifiers(text)
        project_name = _clean_project_title(title)
        common = {
            "项目名称": project_name,
            "项目编号": project_number,
            "招标编号": tender_number,
            "发布日期": publish_time,
            "发布网站": config.PLATFORM_NAME,
        }
        nature = str(config.CHANNELS[channel]["project_nature"])
        source_label = config.source_label(channel, category)

        if category == "tender":
            contacts = _contacts(text, award=False)
            data = {
                "项目性质": nature,
                "源站公告性质": source_label,
                **common,
                "所属行业": BitbidParser._label(text, "所属行业"),
                "组织形式": BitbidParser._label(text, "组织形式", "招标组织形式"),
                "开标时间": BitbidParser._label(text, "开标时间", "开启时间"),
                "项目编号/招标编号": project_number or tender_number,
                "项目类型/行业分类": BitbidParser._label(text, "项目类型", "行业分类"),
                "项目总投资/估算金额": BitbidParser._label(text, "项目总投资", "估算金额", "投资估算"),
                "招标金额": BitbidParser._label(text, "招标金额", "预算金额", "最高投标限价", "招标控制价"),
                "资金来源": BitbidParser._funding_source(text),
                "项目地点": BitbidParser._label(text, "招标项目所在地区", "项目地点", "建设地点", "实施地点", "交货地点"),
                "招标人/采购人名称": contacts.get("招标人/采购人名称", ""),
                "项目规模": BitbidParser._section(text, ("项目概况", "项目规模"), ("招标范围", "投标人资格要求", "供应商资格要求")),
                "工期/服务期/供货日期": BitbidParser._label(text, "交付期限", "计划工期", "工期", "服务期限", "服务期", "交货期限", "供货期"),
                "质量要求": BitbidParser._label(text, "质量要求", "质量标准"),
                "招标内容与范围": BitbidParser._section(text, ("招标范围", "采购范围", "采购内容"), ("投标人资格要求", "供应商资格要求", "招标文件的获取", "采购文件的获取")),
                "申请人资格要求/投标人资格要求": BitbidParser._section(text, ("投标人资格要求", "供应商资格要求", "申请人资格要求"), ("招标文件的获取", "采购文件的获取", "投标文件的递交", "响应文件的递交")),
                "预审文件获取时间": BitbidParser._label(text, "获取时间", "文件发售时间"),
                "获取方式": BitbidParser._label(text, "获取方法", "获取方式"),
                "递交截止时间": BitbidParser._label(text, "递交截止时间", "投标截止时间", "响应文件递交截止时间"),
                "递交方法": BitbidParser._label(text, "递交方法", "递交方式"),
                "开启时间": BitbidParser._label(text, "开标时间", "开启时间"),
                "开启方式": BitbidParser._label(text, "开标方式", "开启方式"),
                "开启地点": BitbidParser._label(text, "开标地点", "开启地点"),
                "评审办法": BitbidParser._label(text, "评标办法", "评审办法"),
                "投标保证金方式": BitbidParser._section(text, ("提交投标保证金的形式", "投标保证金的形式", "响应保证金的递交"), ("提出异议", "其他公告内容", "监督部门", "联系方式")),
                **contacts,
            }
        elif category == "candidate":
            details = _candidate_details(text, table)
            data = {
                "项目性质": nature,
                "源站公告性质": source_label,
                **common,
                "所属行业": BitbidParser._label(text, "所属行业"),
                "组织形式": BitbidParser._label(text, "组织形式", "招标组织形式"),
                "开标时间": BitbidParser._label(text, "开标时间"),
                "公示时间": BitbidParser._publicity_time(text),
                "招标编号/项目编号": tender_number or project_number,
                "中标候选人名称": [row["候选人名称"] for row in details],
                "中标候选人报价": [row["候选人报价"] for row in details],
                "中标候选人明细": details,
                **_contacts(text, award=True),
            }
        elif category == "award":
            details = _award_details(text, table)
            data = {
                "项目性质": nature,
                "源站公告性质": source_label,
                **common,
                "所属行业": BitbidParser._label(text, "所属行业"),
                "组织形式": BitbidParser._label(text, "组织形式", "招标组织形式"),
                "招标方式": BitbidParser._label(text, "招标方式", "采购方式"),
                "中标人名称": [row["中标人名称"] for row in details],
                "联合体成员": _consortium_members(text, table),
                "中标价": [row["中标价"] for row in details],
                "中标结果明细": details,
                "工期": BitbidParser._label(text, "工期", "服务期", "交货期", "供货期"),
                "项目经理": BitbidParser._label(text, "项目经理", "项目负责人"),
                "项目经理证书名称": BitbidParser._label(text, "证书名称"),
                "项目经理证书编号": BitbidParser._label(text, "证书编号"),
                **_contacts(text, award=True),
                "依据文件": BitbidParser._label(text, "依据文件"),
                "依据文号": BitbidParser._label(text, "依据文号"),
            }
        else:
            public_type = (
                "终止公告" if category == "termination" else
                "招标控制价" if "控制价" in title else
                "二次公告" if "二次" in title or "重新" in title else
                "变更公告"
            )
            contacts = _contacts(text, award=False)
            data = {
                "公共类型": public_type,
                **common,
                "所属行业": BitbidParser._label(text, "所属行业"),
                "组织形式": BitbidParser._label(text, "组织形式", "招标组织形式"),
                "开标时间": BitbidParser._label(text, "开标时间", "开启时间"),
                "标书发售时间": BitbidParser._label(text, "获取时间", "文件发售时间"),
                "公告内容": text,
                **{key: value for key, value in contacts.items() if key != "招标人/采购人名称"},
                "监督部门地址": BitbidParser._label(text, "监督部门地址"),
                "监督部门联系人": BitbidParser._label(text, "监督部门联系人"),
                "监督部门联系方式": BitbidParser._label(text, "监督部门联系方式", "监督电话"),
                "依据文件": BitbidParser._label(text, "依据文件"),
                "依据文号": BitbidParser._label(text, "依据文号"),
            }

        attachments: list[dict[str, Any]] = []
        if detail.pdf_url:
            attachments.append({
                "source_file_id": detail.file_id,
                "file_name": f"{detail.file_id}.pdf",
                "file_url": detail.pdf_url,
                "mime_type": "application/pdf",
                "source": "public_notice_pdf",
            })
        warnings: list[str] = []
        expected_file_type = str(config.CATEGORIES[category]["file_type"])
        if detail.file_type and detail.file_type != expected_file_type:
            warnings.append(
                f"PDF_FILE_TYPE_MISMATCH:expected={expected_file_type},actual={detail.file_type}"
            )
        if not detail.pdf_url:
            warnings.append("DETAIL_WITHOUT_PUBLIC_PDF")
        elif not text:
            warnings.append("PDF_TEXT_UNAVAILABLE")
        return ParsedNotice(
            notice_type=str(config.CATEGORIES[category]["schema"]),
            title=title,
            publish_time=publish_time,
            raw_text=text,
            data=data,
            attachments=attachments,
            validation_warnings=warnings,
        )


__all__ = [
    "DetailDocument",
    "GxebiddingParser",
    "ListRecord",
    "extract_pdf_text",
    "normalize_pdf_text",
    "parse_detail_document",
    "parse_list_records",
    "parse_page_info",
]
