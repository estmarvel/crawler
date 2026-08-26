"""山西招投标网列表、详情、PDF 正文和八类字段解析。"""

from __future__ import annotations

import mimetypes
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from lxml import etree, html as lxml_html

from crawler_scrapy.ai.field_contracts import normalize_project_nature
from crawler_scrapy.sites.bitbid.parser import BitbidParser, clean_html
from crawler_scrapy.sites.sxbid import config


@dataclass(frozen=True)
class ListRecord:
    notice_id: str
    path_type: str
    title: str
    publish_time: str
    detail_url: str
    region: str
    project_type: str


@dataclass
class ParsedPage:
    title: str
    publish_time: str
    source_name: str
    raw_text: str
    content_html: str
    headers: dict[str, str]
    body_pdf_url: str
    attachments: list[dict[str, Any]]
    project_chain_id: str


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
    source: bytes | str = value if isinstance(value, bytes) else str(value or "")
    return lxml_html.fromstring(source or "<html></html>")


def _space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _lines(value: Any) -> str:
    return "\n".join(
        line.strip() for line in str(value or "").replace("\x0c", "\n").splitlines()
        if line.strip()
    )


def parse_list_records(value: bytes | str) -> list[ListRecord]:
    root = _document(value)
    records: list[ListRecord] = []
    for row in root.cssselect("table.content_table tbody tr"):
        anchors = row.cssselect("td.text_left a[href*='/f/new/notice/']")
        if not anchors:
            continue
        anchor = anchors[0]
        href = str(anchor.get("href") or "")
        matched = re.search(r"/f/new/notice/(\d+)/([0-9a-fA-F-]+)", href)
        if not matched:
            continue
        cells = row.cssselect("td")
        first_text = _space(cells[0].text_content()) if cells else ""
        region_match = re.match(r"\[([^]]+)]", first_text)
        title = _space(anchor.get("title") or anchor.text_content())
        project_type = _space(cells[1].text_content()) if len(cells) > 1 else ""
        publish_time = _space(cells[2].text_content()) if len(cells) > 2 else ""
        records.append(ListRecord(
            notice_id=matched.group(2),
            path_type=matched.group(1),
            title=title,
            publish_time=publish_time,
            detail_url=config.absolute_url(href),
            region=region_match.group(1) if region_match else "",
            project_type=project_type,
        ))
    return records


def parse_page_info(value: bytes | str) -> tuple[int, int]:
    text = _space(_document(value).text_content())
    matched = re.search(r"共\s*(\d+)\s*页\s*(\d+)\s*条记录", text)
    return (int(matched.group(1)), int(matched.group(2))) if matched else (0, 0)


def _table_fields(root) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in root.cssselect("table tr"):
        cells = row.xpath("./th|./td")
        for index, cell in enumerate(cells[:-1]):
            bold = cell.xpath(".//b")
            if not bold:
                continue
            label = _space(bold[0].text_content()).rstrip("：:")
            if not label:
                continue
            value = _space(cells[index + 1].get("title") or cells[index + 1].text_content())
            if value and label not in result:
                result[label] = value
    return result


def _file_id_from_url(value: str) -> tuple[str, str, bool]:
    decoded = unquote(str(value or ""))
    query = parse_qs(urlsplit(config.absolute_url(decoded)).query)
    return (
        str((query.get("fname") or [""])[0]),
        str((query.get("type") or ["3"])[0]),
        str((query.get("originName") or [""])[0]) == "1",
    )


def parse_detail_page(
    value: bytes | str,
    *,
    list_record: Mapping[str, Any] | None = None,
) -> ParsedPage:
    root = _document(value)
    panel_nodes = root.cssselect(".page_panel.noticeInfoDiv")
    panel = panel_nodes[0] if panel_nodes else root
    title_nodes = panel.cssselect(".page_name")
    title = _space(title_nodes[0].text_content()) if title_nodes else _space((list_record or {}).get("title"))
    message_nodes = panel.cssselect(".page_msg")
    message = _space(message_nodes[0].text_content()) if message_nodes else ""
    published = re.search(r"发布日期\s*[：:]\s*(20\d{2}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}:\d{2})?)", message)
    source = re.search(r"来源\s*[：:]\s*(.+?)(?=\s*浏览次数|$)", message)
    content_nodes = panel.cssselect(".page_content")
    content = content_nodes[0] if content_nodes else panel
    content_html = etree.tostring(content, encoding="unicode", method="html")
    headers = _table_fields(panel)
    raw_text = clean_html(content_html)

    body_pdf_url = ""
    iframe_nodes = content.cssselect("iframe[src*='downloadByFileName']")
    if iframe_nodes:
        # 先解析外层 viewer 查询，再解码 file；若提前 unquote，内层
        # downloadByFileName 的 &fname 会被误当成 viewer 自己的参数。
        iframe_src = str(iframe_nodes[0].get("src") or "")
        file_value = (parse_qs(urlsplit(config.absolute_url(iframe_src)).query).get("file") or [""])[0]
        body_pdf_url = config.absolute_url(file_value)

    attachments: list[dict[str, Any]] = []
    seen: set[str] = set()
    if body_pdf_url:
        file_id, file_type, origin_name = _file_id_from_url(body_pdf_url)
        attachments.append({
            "source_file_id": file_id or None,
            "file_name": f"{title or '公告正文'}.pdf",
            "file_url": config.download_url(file_id, file_type=file_type, origin_name=origin_name) if file_id else body_pdf_url,
            "file_type": "application/pdf",
            "parse_status": "PENDING",
        })
        seen.add(body_pdf_url)

    for box in root.cssselect(".bg_panel.margin_top"):
        headings = box.cssselect(".bid_title")
        if not headings or "附件下载" not in _space(headings[0].text_content()):
            continue
        for anchor in box.cssselect("a[href*='downloadByFileName']"):
            url = config.absolute_url(str(anchor.get("href") or ""))
            if not url or url in seen:
                continue
            file_id, file_type, origin_name = _file_id_from_url(url)
            name = _space(anchor.get("title") or anchor.text_content())
            if not name or name == "下载":
                name = f"附件_{file_id or len(attachments) + 1}"
            attachments.append({
                "source_file_id": file_id or None,
                "file_name": name,
                "file_url": config.download_url(file_id, file_type=file_type, origin_name=origin_name) if file_id else url,
                "file_type": mimetypes.guess_type(name)[0],
                "parse_status": "PENDING",
            })
            seen.add(url)

    page_source = str(value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value)
    chain = re.search(r"/getRelatedContent/\d+/([0-9A-Za-z_-]+)", page_source)
    return ParsedPage(
        title=title,
        publish_time=published.group(1) if published else _space((list_record or {}).get("publish_time")),
        source_name=_space(source.group(1)) if source else "",
        raw_text=raw_text,
        content_html=content_html,
        headers=headers,
        body_pdf_url=body_pdf_url,
        attachments=attachments,
        project_chain_id=chain.group(1) if chain else "",
    )


def extract_pdf_text(
    value: bytes, *, timeout: int = 60, mode: str = "layout"
) -> str:
    """调用系统 pdftotext 读取文字层；不落临时文件。"""

    if not value:
        return ""
    option = "-raw" if mode == "raw" else "-layout"
    try:
        result = subprocess.run(
            ["pdftotext", option, "-", "-"],
            input=value,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return _lines(result.stdout.decode("utf-8", errors="replace"))


class SxbidParser(BitbidParser):
    parser_version = "sxbid-v5-qualification-headings"

    QUALIFICATION_SECTION_TITLES = (
        "投标人资格要求",
        "申请人资格要求",
        "投标人的资格",
        "申请人的资格",
        "资格条件要求",
    )

    @classmethod
    def _label_with_optional_note(cls, text: str, *labels: str) -> str:
        """兼容“递交截止时间（同开标时间）：...”一类带注释标签。"""

        value = cls._label(text, *labels)
        if value:
            return value
        for label in labels:
            matched = re.search(
                rf"(?m)(?:^|\n)\s*[（(]?(?:\d+(?:\.\d+)*[、.]?\s*)?"
                rf"{re.escape(label)}\s*[（(][^）)\n]{{1,30}}[）)]\s*[：:]\s*([^\n]+)",
                text,
            )
            if matched:
                return matched.group(1).strip(" ：:;；")
        return ""

    @classmethod
    def _deadline_sxbid(cls, text: str) -> str:
        value = cls._label_with_optional_note(
            text,
            "投标文件递交截止时间",
            "资格预审申请文件递交截止时间",
            "递交截止时间",
            "投标截止时间",
        )
        if re.fullmatch(r"(?:同)?开标时间", _space(value)):
            value = cls._label_with_optional_note(
                text, "文件开启时间", "开启时间", "开标时间"
            )
        # PDF 文字层可能把下一句拼到同一行。这里只保留明确日期和时间，
        # 避免“申请人应在截止时间前……”进入数据库字段。
        matched = re.search(
            r"20\d{2}\s*(?:[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}"
            r"|年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
            r"(?:\s*\d{1,2}\s*(?::|时)\s*\d{1,2}"
            r"(?:\s*(?::|分)\s*\d{1,2})?\s*(?:分|秒)?)?",
            value,
        )
        return _space(matched.group(0)) if matched else _space(value)

    @classmethod
    def parse(
        cls,
        category: str,
        page: ParsedPage,
        *,
        list_record: Mapping[str, Any] | None = None,
        pdf_text: str = "",
    ) -> ParsedNotice:
        if category not in config.CATEGORIES:
            raise ValueError(f"不支持的山西招投标网栏目：{category}")
        text = _lines("\n".join(filter(None, (page.raw_text, pdf_text))))
        headers = page.headers
        title = page.title or _space((list_record or {}).get("title"))
        publish_time = page.publish_time or _space((list_record or {}).get("publish_time"))
        project_number, tender_number = cls._identifiers(text, headers, page.project_chain_id)
        project_name = cls._project_name_sxbid(headers, title, text)
        contacts = cls._contacts(text)
        common = {
            "项目名称": project_name,
            "项目编号": project_number,
            "招标编号": tender_number,
            "发布日期": publish_time,
            "发布网站": config.PLATFORM_NAME,
        }

        if category == "plan":
            data = {
                # “招标计划”是公告类型，不是项目法定性质。
                "项目性质": normalize_project_nature(headers.get("发布类型", "")),
                "招标方式": headers.get("招标方式", ""),
                **common,
                "项目类型": headers.get("项目类型", "") or _space((list_record or {}).get("project_type")),
                "项目总投资": headers.get("项目总投资", ""),
                "招标内容": headers.get("招标内容", ""),
                "招标人名称": headers.get("招标人名称", ""),
                "行政监督部门": headers.get("行政监督部门", ""),
                # 列表地区只是站内检索分类，不能作为公告明确披露的建设地点。
                "建设地点": headers.get("建设地点", ""),
                "建设内容及规模": headers.get("建设内容及规模", ""),
                "招标公告（资格预审公告）预计发布时间": headers.get("招标公告（资格预审公告）预计发布时间", ""),
            }
        elif category in {"prequalification", "tender"}:
            data = cls._tender_fields(
                category, text, headers, common, contacts, list_record or {}
            )
        elif category == "candidate":
            details = cls._ranked_candidate_details(text, final=False)
            data = {
                "项目性质": "依法必须招标",
                **common,
                "所属行业": headers.get("所属行业", ""),
                "组织形式": headers.get("招标组织形式", ""),
                "开标时间": headers.get("开标时间", "") or cls._label(text, "开标时间"),
                "公示时间": cls._publicity_time(text),
                "招标编号/项目编号": cls._combined(tender_number, project_number),
                "中标候选人名称": [row["候选人名称"] for row in details],
                "中标候选人报价": [row["候选人报价"] for row in details],
                "中标候选人明细": details,
                **cls._contact_fields(contacts, award=True),
            }
        elif category == "final_candidate":
            details = cls._ranked_candidate_details(text, final=True)
            data = {
                "项目性质": "依法必须招标",
                **common,
                "所属行业": headers.get("所属行业", ""),
                "组织形式": headers.get("招标组织形式", ""),
                "开标时间": headers.get("开标时间", "") or cls._label(text, "开标时间"),
                "公示时间": cls._publicity_time(text),
                "招标编号/项目编号": cls._combined(tender_number, project_number),
                "定标候选人名称": [row["候选人名称"] for row in details],
                "定标候选人报价": [row["候选人报价"] for row in details],
                "定标候选人项目经理": cls._label(text, "项目经理", "项目负责人"),
                "定标候选人项目经理相关证书及编号": cls._label(text, "项目经理相关证书及编号", "证书名称及编号"),
                "定标候选人项目副经理": cls._label(text, "项目副经理"),
                "定标候选人项目副经理相关证书及编号": cls._label(text, "项目副经理相关证书及编号"),
                "定标候选人资信情况": cls._section(text, ("资信情况",), ("业绩情况", "联系方式")),
                "定标候选人业绩情况（名称、日期、金额）": cls._section(text, ("业绩情况",), ("联系方式", "监督部门")),
                **cls._contact_fields(contacts, award=True),
                "依据文件": headers.get("依据文件", ""),
                "依据文号": headers.get("依据文号", ""),
            }
        elif category == "award":
            details, consortium = cls._award_details_sxbid(text)
            data = {
                "项目性质": "依法必须招标",
                **common,
                "所属行业": headers.get("所属行业", ""),
                "组织形式": headers.get("招标组织形式", ""),
                "招标方式": cls._label(text, "招标方式"),
                "中标人名称": [row["中标人名称"] for row in details],
                "联合体成员": consortium or cls._list_label(text, "联合体成员"),
                "中标价": [row["中标价"] for row in details],
                "中标结果明细": details,
                "工期": cls._label(text, "工期", "计划工期", "服务期", "特许经营期"),
                "项目经理": cls._label(text, "项目经理", "项目负责人"),
                "项目经理证书名称": cls._label(text, "证书名称"),
                "项目经理证书编号": cls._label(text, "证书编号"),
                **cls._contact_fields(contacts, award=True),
                "依据文件": headers.get("依据文件", ""),
                "依据文号": headers.get("依据文号", ""),
            }
        elif category == "correction":
            data = {
                "公共类型": "更正公告公示",
                **common,
                "所属行业": headers.get("所属行业", ""),
                "组织形式": headers.get("招标组织形式", ""),
                # HTML 表中的时间可能仍是原值，更正公告必须以正文最后一次
                # 出现的“现变更为”开标时间为准。
                "开标时间": cls._last_label(text, "开标时间") or headers.get("开标时间", ""),
                "标书发售时间": headers.get("标书发售时间", "") or cls._label(text, "标书发售时间"),
                "公告内容": text,
                **cls._contact_fields(contacts, award=False),
                "监督部门地址": cls._label(text, "监督部门地址"),
                "监督部门联系人": cls._label(text, "监督部门联系人"),
                "监督部门联系方式": cls._label(text, "监督部门联系方式", "监督电话"),
                "依据文件": headers.get("依据文件", ""),
                "依据文号": headers.get("依据文号", ""),
            }
        elif category == "contract":
            data = {
                **common,
                "合同名称": headers.get("合同名称", title),
                "招标人名称": headers.get("招标人名称", ""),
                "中标人名称": [headers.get("中标人名称", "")] if headers.get("中标人名称") else [],
                "合同金额": cls._with_unit(headers.get("合同金额（万元）", ""), "万元"),
                "合同期限": cls._with_unit(headers.get("合同期限（年）", ""), "年"),
                "合同签署时间": headers.get("合同签署时间", ""),
                "合同主要内容": headers.get("合同主要内容", ""),
            }
        else:  # pragma: no cover - guarded above
            raise AssertionError(category)

        warnings: list[str] = []
        if page.body_pdf_url and not pdf_text:
            warnings.append("PDF_TEXT_UNAVAILABLE:公告正文PDF未取得可解析文字层")
        return ParsedNotice(
            notice_type=config.CATEGORIES[category]["label"],
            title=title,
            publish_time=publish_time,
            raw_text=text,
            data=data,
            attachments=page.attachments,
            validation_warnings=warnings,
        )

    @classmethod
    def _tender_fields(cls, category, text, headers, common, contacts, list_record):
        project_number = common["项目编号"]
        tender_number = common["招标编号"]
        shared = {
            "项目性质": "依法必须招标",
            **common,
            "所属行业": headers.get("所属行业", ""),
            "组织形式": headers.get("招标组织形式", ""),
            "开标时间": headers.get("开标时间", "") or cls._label(text, "开标时间"),
            "项目编号/招标编号": cls._combined(project_number, tender_number),
            "项目类型/行业分类": _space(list_record.get("project_type")),
            "项目总投资/估算金额": cls._label(text, "项目总投资", "估算金额", "投资估算"),
            "招标金额": cls._label(text, "招标金额", "最高投标限价", "招标控制价", "预算金额"),
            "资金来源": cls._funding_source_sxbid(text),
            # 列表“地区”只是检索分类，不冒充正文中的实际履约地点；它仍
            # 保存在 fieldMeta.region 中用于检索和溯源。
            "项目地点": headers.get("实施地", "") or cls._label(
                text, "项目地点", "建设地点", "实施地点", "服务地点", "交货地点"
            ),
            "招标人/采购人名称": contacts.get("owner", {}).get("name", ""),
            "申请人资格要求/投标人资格要求": cls._section(
                text,
                cls.QUALIFICATION_SECTION_TITLES,
                ("资格预审文件的获取", "招标文件的获取", "投标文件的递交", "资格预审申请文件的递交"),
            ),
            "预审文件获取时间": cls._time_range(text, "获取时间") or cls._label(text, "获取时间"),
            "获取方式": cls._paragraph_label(text, "获取方式") or cls._paragraph_label(text, "获取方法"),
            "递交截止时间": cls._deadline_sxbid(text),
            "递交方法": cls._label(text, "递交方法", "递交方式"),
            "开启时间": cls._label(text, "文件开启时间", "开启时间", "开标时间"),
            "开启方式": cls._label(text, "文件开启方式", "开启方式", "开标方式"),
            "开启地点": cls._label(text, "开启地点", "开标地点"),
            "评审办法": cls._paragraph_label(text, "评审办法") or cls._label(text, "评标办法"),
            "投标保证金方式": cls._section(
                text,
                ("提交投标保证金的形式", "投标保证金的形式", "投标保证金的递交"),
                ("提出异议", "其他公告内容", "监督部门", "联系方式"),
            ),
            **cls._contact_fields(contacts, award=False),
        }
        if category == "prequalification":
            shared["项目概况与招标范围"] = cls._section(
                text,
                ("项目概况与招标范围", "项目概况和招标范围"),
                cls.QUALIFICATION_SECTION_TITLES,
            )
        else:
            shared.update({
                "项目规模": cls._section(
                    text,
                    ("项目规模",),
                    ("招标内容与范围", "招标范围"),
                ) or cls._label(text, "项目规模"),
                "工期/服务期/供货日期": cls._label(text, "计划工期", "工期", "服务期", "供货期"),
                "质量要求": cls._label(text, "质量要求", "工程质量要求", "质量标准"),
                "招标内容与范围": cls._section(
                    text,
                    ("招标内容与范围", "招标范围"),
                    cls.QUALIFICATION_SECTION_TITLES,
                ),
            })
        return shared

    @classmethod
    def _identifiers(cls, text: str, headers: Mapping[str, str], chain_id: str) -> tuple[str, str]:
        explicit_project = cls._identifier_sxbid(text, "招标项目编号")
        generic_project = cls._identifier_sxbid(text, "项目编号")
        project_number = (
            headers.get("投资项目统一代码", "")
            or cls._identifier_sxbid(text, "投资项目统一代码", "项目代码")
            or explicit_project
            or headers.get("项目编号", "")
            or generic_project
            # getRelatedContent 路径中的 chain_id 是站内关联键，尚未证明等同
            # 业务项目编号；只保留在 fieldMeta.projectChainId，不写业务字段。
        )
        tender_number = cls._identifier_sxbid(
            text, "招标编号", "采购编号", "代理编号"
        )
        if (
            not tender_number
            and explicit_project
            and generic_project
            and generic_project != explicit_project
        ):
            tender_number = generic_project
        return project_number, tender_number

    @staticmethod
    def _identifier_sxbid(text: str, *labels: str) -> str:
        """保留该站包含空格和中文书名括号的完整业务编号。"""

        for label in labels:
            label_pattern = (
                r"(?<!招标)(?<!采购)项目编号"
                if label == "项目编号"
                else re.escape(label)
            )
            matched = re.search(
                rf"{label_pattern}\s*[：:]\s*([^\n，,。；;]+)", text
            )
            if not matched:
                continue
            value = matched.group(1).strip()
            round_depth = 0
            chinese_depth = 0
            for index, character in enumerate(value):
                if character == "(":
                    round_depth += 1
                elif character == ")":
                    if round_depth == 0:
                        value = value[:index]
                        break
                    round_depth -= 1
                elif character == "（":
                    chinese_depth += 1
                elif character == "）":
                    if chinese_depth == 0:
                        value = value[:index]
                        break
                    chinese_depth -= 1
            value = re.sub(r"\s+", "", value).strip(" ：:;；")
            if value:
                return value
        return ""

    @classmethod
    def _project_name_sxbid(
        cls, headers: Mapping[str, str], title: str, text: str
    ) -> str:
        if headers.get("项目名称"):
            return headers["项目名称"]
        labelled = cls._label(text, "项目名称")
        if labelled and len(labelled) <= 300:
            return labelled
        # 变更类标题常见“项目招标公告变更公告”这种双层后缀。一次替换只会
        # 去掉最外层“变更公告”，因此循环清理，直到不再命中公告类型后缀。
        cleaned = title.strip()
        suffix = (
            r"(?:资格预审公告|招标公告|中标候选人公示|定标候选人公示|"
            r"中标结果公示|(?:第?[一二三四五六七八九十\d]+次)?"
            r"(?:变更|澄清|延期|终止|撤销|更正).*?公告|"
            r"招标控制价(?:公告)?|合同)$"
        )
        while True:
            previous = cleaned
            cleaned = re.sub(suffix, "", cleaned).strip()
            if cleaned == previous:
                break
        cleaned = re.sub(r"(?:重新|二次|再次)$", "", cleaned).strip()
        return re.sub(r"项目项目(?=\s*[（(]\d+标段[）)]$)", "项目", cleaned)

    @classmethod
    def _contacts(cls, text: str) -> dict[str, dict[str, str]]:
        """解析该站 PDF 中经常带排版空格的联系人标签。"""

        result = {"owner": {}, "agency": {}}
        owner_label = r"(?:招\s*标\s*人|采\s*购\s*人|建设单位)"
        agency_label = r"(?:招\s*标\s*代理\s*机构(?:名称)?|采购代理机构|招标代理|代理\s*机构)"
        owner = re.search(
            rf"(?ms)^\s*{owner_label}\s*[：:]\s*(.*?)(?=^\s*{agency_label}\s*[：:]|\Z)",
            text,
        )
        agency = re.search(
            rf"(?ms)^\s*{agency_label}\s*[：:]\s*(.*?)"
            rf"(?=^\s*招标人或(?:其)?招标代理机构|\Z)",
            text,
        )

        all_labels = (
            r"地\s*址|联系地址|项目联系人|联\s*系\s*人|联系人|电\s*话|"
            r"联系电话|联系方式|联络电话|邮\s*编|电\s*子\s*邮\s*件|"
            r"电\s*子\s*邮\s*箱|邮箱|招\s*标\s*方\s*式|开\s*标\s*时\s*间|公告时间"
            r"|招标代理机构项目负责人|招标人或(?:其)?招标代理机构"
        )

        def labelled(block: str, pattern: str) -> str:
            matched = re.search(
                rf"(?ms)(?:^|\n)\s*(?:{pattern})\s*[：:]\s*(.*?)"
                rf"(?=\s*(?:{all_labels})\s*[：:]|\Z)",
                block,
            )
            if not matched:
                return ""
            value = _space(matched.group(1)).strip("。；;")
            return re.sub(
                r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value
            )

        def phone_value(block: str, inline: str = "") -> str:
            raw = labelled(
                block, r"电\s*话|联系电话|联系方式|联络电话"
            ) or inline
            normalized = str(raw or "").replace("—", "-").replace("－", "-")
            values = re.findall(
                r"(?<!\d)(?:\+?86[- ]?)?(?:0\d{2,3}[- ]?\d{7,8}(?:[- ]\d{1,6})?"
                r"|1[3-9]\d{9})(?!\d)",
                normalized,
            )
            return "；".join(dict.fromkeys(_space(value) for value in values))

        for key, matched in (("owner", owner), ("agency", agency)):
            if not matched:
                continue
            block = matched.group(1).strip()
            first = _space(block.splitlines()[0]) if block else ""
            inline_phone = re.search(
                r"[（(]\s*(?:联系电话|电话|联系方式)\s*[：:]\s*([^）)]+)[）)]",
                first,
            )
            first = re.sub(
                r"\s*[（(]\s*(?:联系电话|电话|联系方式)\s*[：:]\s*[^）)]+[）)]\s*$",
                "",
                first,
            ).strip()
            result[key] = {
                "name": first,
                "address": labelled(block, r"地\s*址|联系地址"),
                "contact": labelled(block, r"项目联系人|联\s*系\s*人|联系人"),
                "phone": phone_value(
                    block, _space(inline_phone.group(1)) if inline_phone else ""
                ),
            }
        return result

    @classmethod
    def _publicity_time(cls, text: str) -> str:
        value = super()._publicity_time(text)
        if value:
            return value
        matched = re.search(r"公示期\s*[：:]\s*([^\n]+)", text)
        return _space(matched.group(1)).strip("。；;") if matched else ""

    @classmethod
    def _ranked_candidate_details(
        cls, text: str, *, final: bool
    ) -> list[dict[str, str]]:
        """合并 PDF 表格中的排名、公司和报价。

        源站 PDF 经常把第一张表的公司名折成三行，但第二张负责人表又保留
        完整公司名。这里按排名将两张表合并，避免把负责人或响应表误当报价。
        """

        label = "定标候选人" if final else "中标候选人"
        basic = cls._section(
            text,
            (f"{label}基本情况",),
            (f"{label}按照", f"{label}响应"),
        )
        company = (
            r".{2,180}?(?:联合体|有限责任公司|股份有限公司|集团有限公司|"
            r"有限公司|公司|集团|研究院|设计院|研究所|中心|厂|院)"
        )
        unit_match = re.search(r"(?:投标|中标)?(?:总)?报价[^\n]*[（(](亿元|万元|元|%|％)[）)]", basic)
        if not unit_match:
            unit_match = re.search(
                r"(?:报价|价)\s*[（(](亿元|万元|元|%|％)[）)]",
                "\n".join(basic.splitlines()[:15]),
            )
        unit = unit_match.group(1) if unit_match else ""
        names: dict[int, str] = {}
        amounts: dict[int, str] = {}

        # 定标候选人 PDF 的宽表在 -layout 模式下可能发生列错序；该栏目
        # 使用 -raw 后，排名、公司名和报价按行顺序排列。公司名仍可能被
        # 拆成多行甚至逐字一行，因此在报价行前统一拼回。
        for matched in re.finditer(
            r"(?ms)^\s*((?:[1-9]|1\d|20))\s*\n(.{2,500}?)\n\s*"
            r"([\d,.]+)\s*[（(](亿元|万元|元)[）)]",
            basic,
        ):
            rank = int(matched.group(1))
            name = re.sub(r"\s+", "", matched.group(2))
            if re.search(r"(?:联合体|有限责任公司|股份有限公司|集团有限公司|有限公司|公司|集团|研究院|设计院|研究所|中心|厂|院)$", name):
                names[rank] = name
                amounts[rank] = f"{matched.group(3)}（{matched.group(4)}）"

        lines = basic.splitlines()
        for line_index, line in enumerate(lines):
            matched = re.match(
                rf"^\s*(\d+)\s+({company})\s+([\d,.]+(?:\s*[（(]?(?:亿元|万元|元|%|％)[）)]?)?)",
                line,
            )
            if matched:
                rank = int(matched.group(1))
                parsed_name = _space(matched.group(2))
                names[rank] = cls._complete_inline_wrapped_company(
                    lines, line_index, parsed_name
                ) or parsed_name
                amount = _space(matched.group(3))
                amounts[rank] = amount if re.search(r"亿元|万元|元|%|％", amount) else f"{amount}{unit}"
                continue
            unranked = re.match(
                rf"^\s*({company})\s+([\d,.]+(?:\s*[（(]?(?:亿元|万元|元|%|％)[）)]?)?)",
                line,
            )
            if unranked:
                rank = len(names) + 1
                names[rank] = _space(unranked.group(1))
                amount = _space(unranked.group(2))
                amounts[rank] = amount if re.search(r"亿元|万元|元|%|％", amount) else f"{amount}{unit}"
                continue
            amount_only = re.match(r"^\s*(\d+)\s+([\d,.]+)\s+", line)
            if not amount_only:
                amount_only = re.match(
                    r"^\s*(\d+)\s+\S{1,4}\s+([\d,.]+)\s*[（(](?:亿元|万元|元)[）)]",
                    line,
                )
            if amount_only:
                rank = int(amount_only.group(1))
                amounts[rank] = f"{amount_only.group(2)}{unit}"
                if rank not in names:
                    wrapped = cls._wrapped_company_around_rank(lines, line_index)
                    if wrapped:
                        names[rank] = wrapped

        manager_start = re.search(rf"{label}按照.*?项目负责人情况", text, re.S)
        manager_end = re.search(rf"{label}响应", text)
        manager_block = text[
            manager_start.end() if manager_start else 0:
            manager_end.start() if manager_end else len(text)
        ]
        for line in manager_block.splitlines():
            matched = re.match(rf"^\s*(\d+)\s+({company})\s{{2,}}", line)
            if matched:
                names.setdefault(int(matched.group(1)), _space(matched.group(2)))

        response_block = cls._section(
            text,
            (f"{label}响应招标文件要求的资格能力条件",),
            ("提出异议的渠道和方式", "其他公示内容"),
        )
        for line in response_block.splitlines():
            matched = re.match(rf"^\s*(\d+)\s+({company})(?:\s{{2,}}|$)", line)
            if matched:
                names.setdefault(int(matched.group(1)), _space(matched.group(2)))

        fallback = cls._candidate_details(text)
        known_ranks = set(names) | set(amounts)
        for index, row in enumerate(fallback, 1):
            if known_ranks and index not in known_ranks:
                continue
            names.setdefault(index, row.get("候选人名称", ""))
            if row.get("候选人报价"):
                amounts.setdefault(index, row["候选人报价"])

        if not names:
            for match in re.finditer(
                rf"(?:{label}|候选人)(?:名称)?\s*[：:]\s*([^\n]{{2,100}}?)(?:\s+(?:报价|投标报价)\s*[：:]\s*([^\n]+))?(?=\n|$)",
                text,
            ):
                rank = len(names) + 1
                names[rank] = match.group(1).strip()
                amounts[rank] = (match.group(2) or "").strip()

        return [
            {
                "标段": "",
                "候选人名称": names[rank],
                "候选人报价": amounts.get(rank, ""),
            }
            for rank in sorted(names)
            if names[rank]
        ]

    @staticmethod
    def _wrapped_company_around_rank(lines: list[str], index: int) -> str:
        """恢复被 PDF 表格拆到排名行上下两侧的公司名称。"""

        if index <= 0 or index + 1 >= len(lines):
            return ""
        before = _space(lines[index - 1])
        after = _space(lines[index + 1])
        if not before or not after or re.search(r"候选人名称|投标报价|排序|序号", before):
            return ""
        value = re.sub(r"\s+", "", before + after)
        if not (4 <= len(value) <= 180):
            return ""
        return value if re.search(
            r"(?:联合体|有限责任公司|股份有限公司|集团有限公司|有限公司|公司|集团|研究院|设计院|研究所|中心|厂|院)$",
            value,
        ) else ""

    @staticmethod
    def _complete_inline_wrapped_company(
        lines: list[str], index: int, parsed_name: str
    ) -> str:
        """补全“前半公司名 / 排名+中段 / 后缀”三行式 PDF 单元格。"""

        if index <= 0 or index + 1 >= len(lines):
            return ""
        before = _space(lines[index - 1])
        after = _space(lines[index + 1])
        if (
            not before
            or not after
            or re.search(r"候选人名称|投标报价|排序|序号|质量|工期", before)
            or not re.fullmatch(
                r"(?:有限责任公司|股份有限公司|集团有限公司|有限公司|公司|"
                r"集团|研究院|设计院|研究所|中心|厂|院)",
                re.sub(r"\s+", "", after),
            )
        ):
            return ""
        value = re.sub(r"\s+", "", before + parsed_name + after)
        if not (4 <= len(value) <= 180):
            return ""
        return value if re.search(
            r"(?:联合体|有限责任公司|股份有限公司|集团有限公司|有限公司|公司|"
            r"集团|研究院|设计院|研究所|中心|厂|院)$",
            value,
        ) else ""

    @classmethod
    def _award_details_sxbid(
        cls, text: str
    ) -> tuple[list[dict[str, str]], list[str]]:
        rows = cls._award_details(text)
        if not rows:
            # 部分公共资源 PDF 使用“中标单位：…”或无冒号的
            # “中标人名称 某公司”，而基类只识别“中标人：…”。只接受
            # 明确角色标签且名称以机构后缀结束，避免从叙述段落猜测实体。
            labelled = list(re.finditer(
                r"(?m)^\s*(?:中\s*标\s*(?:单位|人名称)|成交(?:单位|供应商))"
                r"\s*[：:]?\s*([^\n]{2,240}?(?:有限责任公司|股份有限公司|"
                r"集团有限公司|有限公司|公司|集团|研究院|设计院|研究所|中心|厂|院))\s*$",
                text,
            ))
            for index, matched in enumerate(labelled):
                end = labelled[index + 1].start() if index + 1 < len(labelled) else min(
                    len(text), matched.end() + 800
                )
                context = text[matched.end():end]
                name = cls._clean_result_name(matched.group(1))
                price_match = re.search(
                    r"(?:中\s*标\s*(?:金额|价格|价)|成交(?:金额|价格|价)|投标报价)"
                    r"\s*[：:]?\s*([\d,.，]+\s*(?:亿元|万元|元|%|％))",
                    context,
                )
                price = (
                    _space(price_match.group(1))
                    if price_match
                    else cls._label(
                        context,
                        "中标金额", "中标价格", "中标价", "成交金额", "成交价",
                    ) or cls._amount(context)
                )
                rows.append({
                    "标段": cls._label(text, "标段", "标段（包）"),
                    "中标人名称": name,
                    "中标价": price,
                })
        if not rows:
            info = cls._section(
                text,
                ("中标人信息", "中标人情况"),
                ("其他公示内容", "其他公告内容", "监督部门", "联系方式"),
            )
            company = (
                r"[^\n]{2,180}?(?:有限责任公司|股份有限公司|集团有限公司|"
                r"有限公司|公司|集团|研究院|设计院|研究所|中心|厂|院|队)"
            )
            for matched in re.finditer(
                rf"(?m)^\s*(?P<section>[^\s]{{0,30}}?(?:标段|包))?\s*"
                rf"(?P<name>{company})\s{{2,}}"
                r"(?P<price>[\d,.，]+\s*(?:亿元|万元|元|%|％))\s*$",
                info,
            ):
                rows.append({
                    "标段": _space(matched.group("section") or ""),
                    "中标人名称": cls._clean_result_name(matched.group("name")),
                    "中标价": _space(matched.group("price")),
                })
            if not rows:
                lines = info.splitlines()
                for index, line in enumerate(lines):
                    name_match = re.fullmatch(rf"\s*({company})\s*", line)
                    if not name_match:
                        continue
                    context = "\n".join(lines[index + 1:index + 4])
                    price_match = re.search(
                        r"(?:中标价|中标价格|投标报价)\s*[：:]\s*"
                        r"([\d,.，]+\s*(?:亿元|万元|元|%|％))",
                        context,
                    )
                    if price_match:
                        rows.append({
                            "标段": "",
                            "中标人名称": cls._clean_result_name(name_match.group(1)),
                            "中标价": _space(price_match.group(1)),
                        })
                        break
        if not rows:
            leader = cls._label(text, "联合体牵头单位", "联合体牵头人")
            if leader:
                members = cls._list_label(text, "联合体成员单位") \
                    or cls._list_label(text, "联合体成员")
                rows = [{
                    "标段": cls._label(text, "标段", "标段（包）"),
                    "中标人名称": ";".join([leader, *members]),
                    "中标价": cls._label(
                        text, "中标金额", "中标价格", "中标价", "投标报价"
                    ),
                }]
        if not rows:
            selected = re.search(
                r"(?:(?:由\s*)?招标人\s*)?确定\s*(.{2,300}?)\s*为"
                r"(?:(?:本|该)?(?:项目|标段)(?:的)?|.{0,240}?的)?"
                r"\s*中\s*标\s*人",
                text,
                re.S,
            )
            if selected:
                name = _space(selected.group(1)).strip("，,。；;")
                name = re.sub(r"[（(]联合体[）)]$", "", name).strip()
                name = re.sub(
                    r"[（(]\s*联合体(?:牵头人|成员单位|成员)\s*[）)]",
                    "",
                    name,
                )
                parties = [
                    item.strip() for item in re.split(r"[、;；]", name) if item.strip()
                ]
                price = cls._label(
                    text, "中标金额", "中标价格", "中标价", "投标报价"
                )
                rows = [{
                    "标段": cls._label(text, "标段", "标段（包）"),
                    "中标人名称": ";".join(parties),
                    "中标价": price,
                }]
        result: list[dict[str, str]] = []
        consortium: list[str] = []
        for row in rows:
            parties = [
                re.sub(
                    r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])",
                    "",
                    _space(item),
                ).strip(" ，,;；")
                for item in re.split(r"[;；]", row.get("中标人名称", ""))
                if item.strip(" ，,;；")
            ]
            if not parties:
                continue
            consortium.extend(parties[1:])
            price = row.get("中标价", "")
            values = re.findall(
                r"[\d,.]+\s*(?:(?:亿元|万元|元)(?:/[^\s（(，,。；;]+)?|%|％)",
                price,
            )
            result.append({
                "标段": row.get("标段", ""),
                "中标人名称": parties[0],
                "中标价": _space(values[-1] if values else price).strip("（() ）;；"),
            })
        return result, list(dict.fromkeys(consortium))

    @classmethod
    def _funding_source_sxbid(cls, text: str) -> str:
        # pdftotext 会按版面宽度在词语中间断行（例如“剩余资\n金”）。
        # 允许字段跨普通换行，但遇到标点、招标人或下一章节标题即停止。
        matched = re.search(
            r"(?s)(?:项目)?资金来源(?:为|是|由)\s*(.+?)"
            r"(?=[，,]\s*招\s*标\s*人\s*(?:为|是|[：:])|[。；;]|"
            r"\n\s*(?:[一二三四五六七八九十]+[、.．]|\d+(?:[.．]\d+)*[、.．])\s*[^\n]+|\Z)",
            text,
        )
        if not matched:
            return cls._funding_source(text)
        value = _space(matched.group(1))
        return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)

    @staticmethod
    def _time_range(text: str, label: str) -> str:
        matched = re.search(
            rf"{re.escape(label)}\s*[：:]\s*(20\d{{2}}[-年/]\d{{1,2}}[-月/]\d{{1,2}}[^\n]*?至\s*20\d{{2}}[-年/]\d{{1,2}}[-月/]\d{{1,2}}[^\n]*?)(?=\s+(?:获取方式|递交方法|递交方式)\s*[：:]|\n|$)",
            text,
        )
        return _space(matched.group(1)) if matched else ""

    @staticmethod
    def _paragraph_label(text: str, label: str) -> str:
        matched = re.search(
            rf"(?s){re.escape(label)}\s*[：:]\s*(.*?)(?=\n\s*(?:[一二三四五六七八九十]+[、.．]|\d+(?:[.．]\d+)*[、.．])\s*[^\n]+|\Z)",
            text,
        )
        return _lines(matched.group(1)) if matched else ""

    @staticmethod
    def _last_label(text: str, label: str) -> str:
        matches = re.findall(rf"{re.escape(label)}\s*[：:]\s*([^\n]+)", text)
        return _space(matches[-1]).strip("。；;") if matches else ""

    @staticmethod
    def _combined(*values: str) -> str:
        return "；".join(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _with_unit(value: str, unit: str) -> str:
        text = _space(value)
        return f"{text}{unit}" if text and unit not in text else text
