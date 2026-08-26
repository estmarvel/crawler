"""润世和公开接口、正文 HTML 和 PDF 文字层字段解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit

from crawler_scrapy.sites.bitbid.parser import BitbidParser, clean_html
from crawler_scrapy.sites.runshihua import config
from crawler_scrapy.sites.sxbid.parser import extract_pdf_text


@dataclass
class ParsedNotice:
    notice_type: str
    title: str
    publish_time: str
    raw_html: str
    raw_text: str
    data: dict[str, Any]
    attachments: list[dict[str, Any]]
    validation_warnings: list[str]


def _value(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _nested(source: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = source.get(key)
    return value if isinstance(value, Mapping) else {}


def _range(start: str, end: str) -> str:
    return f"{start} 至 {end}" if start and end else start or end


def _project_type(record: Mapping[str, Any]) -> str:
    return {"A": "工程", "B": "货物", "C": "服务"}.get(
        _value(record, "remark").upper(), ""
    )


def _strip_title(value: str) -> str:
    cleaned = re.sub(
        r"(?:资格预审变更公告|资格预审公告|采购变更公告|采购公告|招标变更公告|"
        r"招标公告|中标候选人公示更正|中标候选人公示|中标结果公示更正|"
        r"中标结果公示|控制价变更公告|控制价公告|撤销公告|补充公告|延期公告)$",
        "",
        str(value or "").strip(),
    ).strip()
    return cleaned.rstrip("。．. ")


def _content_html(detail: Mapping[str, Any]) -> str:
    return _value(
        detail,
        "gcjsPublicityContent",
        "noticeContent",
        "publicityContent",
        "alterationContent",
    )


def _meaningful_project_name(value: object) -> str:
    text = _strip_title(str(value or ""))
    if not text or re.fullmatch(r"(?:不分标段|第?[一二三四五六七八九十\d]+标段)", text):
        return ""
    return text


def _structured_text(detail: Mapping[str, Any]) -> str:
    """接口没有 HTML 时，用原始结构字段生成可检索正文，不伪造缺失值。"""

    fields = (
        ("公告名称", "noticeName"),
        ("项目名称", "tenderingName"),
        ("招标项目编号", "tenderingCode"),
        ("招标编号", "noticeNumber"),
        ("项目地点", "projectAddress"),
        ("资金来源", "fundSource"),
        ("招标方式", "tenderingNode"),
        ("项目规模", "projectScale"),
        ("文件获取时间", "startDate"),
        ("文件获取截止时间", "finishDate"),
        ("获取方式", "getFileMethod"),
        ("递交截止时间", "endDate"),
        ("递交方法", "submitMethod"),
        ("递交地址", "submitAddress"),
        ("开标时间", "fileOpenDate"),
        ("开标方式", "fileOpenMethod"),
        ("其他公告内容", "restsRemark"),
        ("监督部门", "supervisionBranch"),
        ("招标人", "tenderingPerson"),
        ("招标人联系人", "tenderingContacts"),
        ("招标人电话", "tenderingPhone"),
        ("招标代理机构", "tenderingAgency"),
        ("招标代理联系人", "tenderingAgencyContacts"),
        ("招标代理电话", "tenderingAgencyPhone"),
    )
    lines = [
        f"{label}：{value}"
        for label, key in fields
        if (value := _value(detail, key))
    ]
    for label, key in (
        ("招标内容与范围", "sectionNumberContentMap"),
        ("投标人资格要求", "sectionNameRequireMap"),
    ):
        value = _section_map_text(detail.get(key))
        if value:
            lines.append(f"{label}：\n{value}")
    return "\n".join(lines)


def _combined_text(detail: Mapping[str, Any], pdf_text: str) -> tuple[str, str]:
    raw_html = _content_html(detail)
    html_text = clean_html(raw_html)
    pdf = str(pdf_text or "").strip()
    if html_text and pdf and pdf not in html_text:
        return raw_html, f"{html_text}\n\n--- PDF文字层 ---\n{pdf}"
    return raw_html, html_text or pdf or _structured_text(detail)


def _template(detail: Mapping[str, Any]) -> Mapping[str, Any]:
    return _nested(detail, "biddingCandidatePublicityTemplate")


def _identifier(detail: Mapping[str, Any], text: str, *, project: bool) -> str:
    template = _template(detail)
    if project:
        direct = _value(detail, "tenderingCode", "tenderingNumber") or _value(
            template, "tenderingCode"
        )
        labels = ("招标项目编号", "项目编号")
    else:
        direct = _value(detail, "noticeNumber", "candidateNumber") or _value(
            template, "candidateNumber"
        )
        labels = ("招标编号",)
    return direct or BitbidParser._identifier_label(text, *labels)


def _contacts(detail: Mapping[str, Any], text: str, *, award: bool) -> dict[str, str]:
    template = _template(detail)
    parsed = BitbidParser._contact_fields(BitbidParser._contacts(text), award=award)
    owner_name_key = "招标人/采购人" if award else "招标人/采购人名称"
    return {
        owner_name_key: _value(
            detail, "tenderingCompanyName", "tenderingPerson"
        ) or _value(template, "tenderingPerson") or parsed.get(owner_name_key, ""),
        "招标人地址": _value(detail, "tenderingAddress")
        or _value(template, "tenderingAddress")
        or parsed.get("招标人地址", ""),
        "招标人联系人": _value(detail, "tenderingContacts")
        or _value(template, "tenderingContacts")
        or parsed.get("招标人联系人", ""),
        "招标人联系方式": _value(detail, "tenderingPhone")
        or _value(template, "tenderingPhone")
        or parsed.get("招标人联系方式", ""),
        "招标代理机构": _value(
            detail, "tenderingAgency", "tenderingAgencyCompanyName"
        ) or _value(template, "tenderingAgency") or parsed.get("招标代理机构", ""),
        "招标代理机构地址": _value(detail, "tenderingAgencyAddress")
        or _value(template, "tenderingAgencyAddress")
        or parsed.get("招标代理机构地址", ""),
        "招标代理机构联系人": _value(detail, "tenderingAgencyContacts")
        or _value(template, "tenderingAgencyContacts")
        or parsed.get("招标代理机构联系人", ""),
        "招标代理机构联系方式": _value(detail, "tenderingAgencyPhone")
        or _value(template, "tenderingAgencyPhone")
        or parsed.get("招标代理机构联系方式", ""),
    }


def _section_map_text(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    return "\n".join(
        f"{str(key).strip()}：{clean_html(item)}"
        for key, item in value.items()
        if str(item or "").strip()
    )


def _notice_data(
    category: str,
    detail: Mapping[str, Any],
    list_record: Mapping[str, Any],
    text: str,
) -> dict[str, Any]:
    definition = config.CATEGORIES[category]
    project_name = _value(detail, "tenderingName") or _strip_title(
        _value(detail, "noticeName") or _value(list_record, "noticeName")
    )
    project_code = _identifier(detail, text, project=True)
    tender_code = _identifier(detail, text, project=False)
    common = {
        "项目性质": "招标信息",
        "项目名称": project_name,
        "项目编号": project_code,
        "招标编号": tender_code,
        "所属行业": _project_type(list_record),
        "组织形式": _value(detail, "tenderingOrganizeForm"),
        "项目类型/行业分类": _project_type(list_record),
        "资金来源": _value(detail, "fundSource"),
        "项目地点": _value(detail, "projectAddress"),
        "发布日期": _value(detail, "releaseDate")
        or _value(list_record, "returnDate", "createDate"),
        "发布网站": config.PLATFORM_NAME,
    }
    contacts = _contacts(detail, text, award=False)
    if definition["schema"] == "资格预审公告":
        return {
            **common,
            "开标时间": _value(detail, "fileOpenDate", "endDate"),
            "项目编号/招标编号": project_code or tender_code,
            "项目总投资/估算金额": _value(detail, "projectTotal"),
            "招标金额": _value(detail, "tenderAmount"),
            "招标人/采购人名称": contacts["招标人/采购人名称"],
            "项目概况与招标范围": "\n".join(filter(None, (
                _value(detail, "projectScale"),
                _section_map_text(detail.get("sectionNumberContentMap")),
            ))),
            "申请人资格要求/投标人资格要求": _section_map_text(
                detail.get("sectionNameRequireMap")
            ),
            "预审文件获取时间": _range(
                _value(detail, "startDate"), _value(detail, "finishDate")
            ),
            "获取方式": _value(detail, "getFileMethod"),
            "递交截止时间": _value(detail, "endDate"),
            "递交方法": _value(detail, "submitMethod"),
            "开启时间": _value(detail, "fileOpenDate"),
            "开启方式": _value(detail, "fileOpenMethod"),
            "开启地点": _value(detail, "submitAddress"),
            "评审办法": _value(detail, "reviewMethod"),
            "投标保证金方式": _value(detail, "marginForm")
            or BitbidParser._section(
                text,
                ("提交投标保证金的形式", "投标保证金方式"),
                ("提出异议", "其他公告内容", "监督部门"),
            ),
            **contacts,
        }
    if definition["schema"] == "招标公告":
        rests = _value(detail, "restsRemark")
        return {
            **common,
            "开标时间": _value(detail, "fileOpenDate", "endDate"),
            "项目编号/招标编号": project_code or tender_code,
            "项目规模": _value(detail, "projectScale"),
            "工期/服务期/供货日期": BitbidParser._label(
                rests or text, "供货期要求", "工期要求", "服务期要求", "交货期"
            ),
            "质量要求": BitbidParser._label(rests or text, "质量要求"),
            "招标内容与范围": _section_map_text(
                detail.get("sectionNumberContentMap")
            ) or BitbidParser._section(
                text,
                ("项目概况和招标范围", "招标内容与范围"),
                ("投标人资格要求", "招标文件的获取"),
            ),
            "申请人资格要求/投标人资格要求": _section_map_text(
                detail.get("sectionNameRequireMap")
            ),
            "预审文件获取时间": _range(
                _value(detail, "startDate"), _value(detail, "finishDate")
            ),
            "获取方式": _value(detail, "getFileMethod"),
            "递交截止时间": _value(detail, "endDate"),
            "递交方法": _value(detail, "submitMethod"),
            "开启时间": _value(detail, "fileOpenDate"),
            "开启方式": _value(detail, "fileOpenMethod"),
            "开启地点": _value(detail, "submitAddress"),
            "评审办法": _value(detail, "reviewMethod"),
            "投标保证金方式": _value(detail, "marginForm")
            or BitbidParser._section(
                text,
                ("提交投标保证金的形式", "投标保证金方式"),
                ("提出异议", "其他公告内容", "监督部门"),
            ),
            **contacts,
        }
    return _correction_data(category, detail, list_record, text)


def _candidate_data(
    category: str,
    detail: Mapping[str, Any],
    list_record: Mapping[str, Any],
    text: str,
) -> dict[str, Any]:
    template = _template(detail)
    title = _value(detail, "candidateName", "sectionName") or _value(
        list_record, "sectionName"
    )
    project_name = _value(detail, "tenderingName", "sectionName") or _value(
        template, "tenderingName"
    ) or _strip_title(title)
    project_code = _identifier(detail, text, project=True)
    tender_code = _identifier(detail, text, project=False)
    published = _value(detail, "startDate", "createDate") or _value(
        list_record, "startDate", "createDate"
    )
    if config.CATEGORIES[category]["schema"] == "中标候选人公示":
        details = _candidate_details(text)
        return {
            "项目性质": "招标信息",
            "项目名称": project_name,
            "项目编号": project_code,
            "招标编号": tender_code,
            "所属行业": _project_type(list_record),
            "组织形式": _value(detail, "tenderingOrganizeForm"),
            "公示时间": _range(
                _value(detail, "startDate"), _value(detail, "endDate")
            ),
            "招标编号/项目编号": tender_code or project_code,
            "中标候选人名称": [row["候选人名称"] for row in details],
            "中标候选人报价": [row["候选人报价"] for row in details],
            "中标候选人明细": details,
            **_contacts(detail, text, award=True),
            "发布日期": published,
            "发布网站": config.PLATFORM_NAME,
        }
    if config.CATEGORIES[category]["schema"] == "中标结果公示":
        details = _award_details(text)
        return {
            "项目性质": "招标信息",
            "项目名称": project_name,
            "项目编号": project_code,
            "招标编号": tender_code,
            "所属行业": _project_type(list_record),
            "组织形式": _value(detail, "tenderingOrganizeForm"),
            "中标人名称": [row["中标人名称"] for row in details],
            "中标价": [row["中标价"] for row in details],
            "中标结果明细": details,
            **_contacts(detail, text, award=True),
            "发布日期": published,
            "发布网站": config.PLATFORM_NAME,
        }
    return _correction_data(category, detail, list_record, text)


def _candidate_details(text: str) -> list[dict[str, str]]:
    """兼容润世和签章 PDF 中被跨行拆开的候选人名称。

    `pdftotext -layout` 偶尔会把基本情况表中的公司名拆在排名行上下，
    但后续项目负责人/资格响应表仍保留完整公司名。先用通用表格规则；
    数量不足时从后续表恢复有序公司名，再与基本情况表的报价顺序绑定。
    """

    direct = BitbidParser._candidate_details(text)
    basic = BitbidParser._section(
        text,
        ("中标候选人基本情况",),
        ("中标候选人按照", "中标候选人响应", "提出异议"),
    ) or text
    amounts = [
        f"{match.group(1)}{match.group(2) or ''}".replace(" ", "")
        for match in re.finditer(
            r"(?m)^\s*\d+\s+([\d,.，]+)\s*[（(]?\s*(亿元|万元|元|%|％)?\s*[)）]?",
            basic,
        )
    ]
    if direct and (not amounts or len(direct) >= len(amounts)):
        return direct

    later = BitbidParser._section(
        text,
        ("中标候选人按照",),
        ("中标候选人响应", "提出异议", "其他公示内容"),
    ) or BitbidParser._section(
        text,
        ("中标候选人响应",),
        ("提出异议", "其他公示内容", "监督部门"),
    )
    names: list[str] = []
    for match in re.finditer(
        r"(?m)^\s*\d+\s+(.{2,100}?(?:公司|厂|院|中心|集团|研究所))(?=\s{2,}|\t)",
        later,
    ):
        name = re.sub(r"\s+", "", match.group(1)).strip()
        if name and name not in names:
            names.append(name)
    if names and len(names) == len(amounts):
        return [
            {"标段": "", "候选人名称": name, "候选人报价": amount}
            for name, amount in zip(names, amounts)
        ]
    return direct


def _award_details(text: str) -> list[dict[str, str]]:
    details = BitbidParser._award_details(text)
    if not details:
        return details
    fallback = BitbidParser._label(text, "拟中标价格", "中标金额")
    if fallback and len(details) == 1 and not details[0].get("中标价"):
        details[0]["中标价"] = fallback
    return details


def _correction_data(
    category: str,
    detail: Mapping[str, Any],
    list_record: Mapping[str, Any],
    text: str,
) -> dict[str, Any]:
    title = _value(detail, "noticeName", "candidateName", "sectionName") or _value(
        list_record, "noticeName", "sectionName"
    )
    # 部分控制价详情把 tenderingName 退化为“不分标段招标公告”，而列表仍
    # 保存真实项目名；按有效程度选取，避免把标段占位词写进 project。
    project_name = next(filter(None, (
        _meaningful_project_name(list_record.get("tenderingName")),
        _meaningful_project_name(detail.get("tenderProjectName")),
        _meaningful_project_name(detail.get("tenderingName")),
        _meaningful_project_name(_template(detail).get("tenderingName")),
        _meaningful_project_name(title),
    )), _strip_title(title))
    project_code = _identifier(detail, text, project=True)
    tender_code = _identifier(detail, text, project=False)
    contacts = _contacts(detail, text, award=False)
    return {
        "公共类型": config.CATEGORIES[category]["label"],
        "项目名称": project_name,
        "项目编号": project_code,
        "招标编号": tender_code,
        "所属行业": _project_type(list_record),
        "组织形式": _value(detail, "tenderingOrganizeForm"),
        "开标时间": _value(detail, "examineDate", "fileOpenDate", "endDate"),
        "标书发售时间": _range(
            _value(detail, "examineStartDate", "startDate"),
            _value(detail, "examineFileSellEndDate", "finishDate"),
        ),
        "公告内容": _value(detail, "noticeContent", "alterationContent") or text,
        **contacts,
        "监督部门联系方式": _value(detail, "supervisionPhone"),
        # releaseDate 在控制价/变更接口中常指原招标公告日期，当前公告应优先
        # 使用自身 createDate/startDate 或列表 returnDate。
        "发布日期": _value(detail, "createDate", "startDate")
        or _value(list_record, "createDate", "startDate", "returnDate")
        or _value(detail, "releaseDate"),
        "发布网站": config.PLATFORM_NAME,
    }


def _attachment(detail: Mapping[str, Any], title: str) -> list[dict[str, Any]]:
    value = _value(detail, "noticePdf", "candidateUrl", "pdfUrl")
    url = config.absolute_pdf_url(value)
    if not url:
        return []
    source_id = PurePosixPath(urlsplit(url).path).stem or None
    return [{
        "source_file_id": source_id,
        "file_name": f"{title or source_id or '公告正文'}.pdf",
        "file_url": url,
        "file_type": "application/pdf",
        "parse_status": "PENDING",
    }]


class RunshihuaParser(BitbidParser):
    parser_version = "runshihua-v2-verified-api-html-pdf"

    @classmethod
    def parse(
        cls,
        category: str,
        detail: Mapping[str, Any],
        *,
        list_record: Mapping[str, Any] | None = None,
        pdf_text: str = "",
    ) -> ParsedNotice:
        if category not in config.CATEGORIES:
            raise ValueError(f"不支持的润世和公告类别：{category}")
        record = list_record or {}
        raw_html, text = _combined_text(detail, pdf_text)
        definition = config.CATEGORIES[category]
        title = _value(detail, "noticeName", "candidateName", "sectionName") or _value(
            record, "noticeName", "sectionName"
        )
        family = str(definition["family"])
        if family == "notice":
            data = _notice_data(category, detail, record, text)
        elif family == "candidate":
            data = _candidate_data(category, detail, record, text)
        else:
            data = _correction_data(category, detail, record, text)
        project_code = str(data.get("项目编号") or "").strip()
        tender_code = str(data.get("招标编号") or "").strip()
        warnings: list[str] = []
        if not project_code:
            warnings.append("PROJECT_CODE_MISSING")
        if not tender_code:
            warnings.append("TENDER_CODE_MISSING")
        if not text:
            warnings.append("BODY_TEXT_MISSING")
        published = str(data.get("发布日期") or "").strip()
        return ParsedNotice(
            notice_type=str(definition["schema"]),
            title=title,
            publish_time=published,
            raw_html=raw_html,
            raw_text=text,
            data=data,
            attachments=_attachment(detail, title),
            validation_warnings=warnings,
        )


__all__ = ["ParsedNotice", "RunshihuaParser", "extract_pdf_text"]
