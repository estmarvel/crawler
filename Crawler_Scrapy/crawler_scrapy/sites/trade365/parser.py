"""中招联合（山西）列表、详情、字段和公开附件解析。"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.parse import unquote

from lxml import etree, html as lxml_html

from crawler_scrapy.sites.bitbid.parser import clean_html
from crawler_scrapy.sites.qianji.parser import QianjiParser
from crawler_scrapy.sites.trade365 import config


@dataclass(frozen=True)
class ListRecord:
    notice_id: str
    title: str
    publish_time: str
    detail_url: str
    project_type: str


@dataclass
class ParsedNotice:
    category: str
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


def parse_list_records(value: bytes | str) -> list[ListRecord]:
    root = _document(value)
    records: list[ListRecord] = []
    for node in root.cssselect("ul.searchList > li"):
        title_nodes = node.cssselect("span.span_hover")
        if not title_nodes:
            continue
        title_node = title_nodes[0]
        title = _space(title_node.get("title") or title_node.text_content())
        anchors = node.cssselect("a[href$='.jhtml']")
        href = next((str(anchor.get("href") or "") for anchor in anchors if "/index" not in str(anchor.get("href") or "")), "")
        matched = re.search(r"/([^/]+)/([^/?#]+)\.jhtml", href)
        if not title or not matched:
            continue
        date_nodes = node.cssselect(".release_date")
        date_text = _space(date_nodes[0].text_content()) if date_nodes else ""
        date_match = re.search(r"(20\d{2}-\d{1,2}-\d{1,2})", date_text)
        type_nodes = node.cssselect("em")
        project_type = _space(type_nodes[0].text_content()) if type_nodes else ""
        records.append(ListRecord(
            notice_id=matched.group(2),
            title=title,
            publish_time=date_match.group(1) if date_match else "",
            detail_url=config.absolute_url(href),
            project_type=project_type,
        ))
    return records


def parse_page_info(value: bytes | str) -> tuple[int, int, int]:
    root = _document(value)
    text = _space(" ".join(node.text_content() for node in root.cssselect(".pagination_div")))
    matched = re.search(r"共\s*(\d+)\s*条记录\s*(\d+)\s*/\s*(\d+)\s*页", text)
    return tuple(map(int, matched.groups())) if matched else (0, 0, 0)


def classify_category(source_category: str, title: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", "", str(title or ""))
    if any(word in compact for word in ("废标", "流标", "终止", "撤销", "暂停")):
        return "termination", "招标公告"
    if any(word in compact for word in ("中标候选人", "成交候选人")):
        return "candidate", "中标候选人公示"
    if any(word in compact for word in ("中标结果", "成交结果", "结果公告", "中标公告", "成交公告")):
        return "award", "中标结果公示"
    if source_category == "change" and any(
        word in compact for word in ("变更", "延期", "控制价", "澄清", "答疑", "补充")
    ):
        return "change", "招标公告"
    return "tender", "招标公告"


class Trade365Parser(QianjiParser):
    parser_version = "trade365-v3-field-audit"

    @classmethod
    def parse(
        cls,
        source_feed: str,
        value: bytes | str,
        *,
        list_record: Mapping[str, Any] | None = None,
    ) -> ParsedNotice:
        root = _document(value)
        title_nodes = root.cssselect(".app h2")
        title = _space(title_nodes[0].text_content()) if title_nodes else _space((list_record or {}).get("title"))
        page_text = _space(root.text_content())
        time_match = re.search(
            r"发布时间\s*[：:]\s*(20\d{2}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}:\d{2})?)",
            page_text,
        )
        publish_time = time_match.group(1) if time_match else _space((list_record or {}).get("publish_time"))
        content_nodes = root.cssselect("#content")
        content_html = (
            etree.tostring(content_nodes[0], encoding="unicode", method="html")
            if content_nodes else ""
        )
        raw_text = clean_html(content_html)
        source_category, project_type = source_feed.split(".", 1)
        category, notice_type = classify_category(source_category, title)
        project_type_name = config.PROJECT_TYPES[project_type][0]
        contacts = cls._contacts_365(raw_text)

        if category in {"tender", "change", "termination"}:
            data = cls._tender(raw_text, title, publish_time, contacts, project_type_name, category)
        elif category == "candidate":
            data = cls._candidate(content_html, raw_text, title, publish_time, contacts, project_type_name)
        elif category == "award":
            data = cls._award(content_html, raw_text, title, publish_time, contacts, project_type_name)
        else:
            raise ValueError(f"不支持的公告类型：{category}")

        project_number, tender_number = cls._identifiers_365(raw_text)
        data["项目编号"] = project_number
        data["招标编号"] = tender_number
        combined = "；".join(dict.fromkeys(filter(None, (project_number, tender_number))))
        for field in ("项目编号/招标编号", "招标编号/项目编号"):
            if field in data:
                data[field] = combined
        return ParsedNotice(
            category=category,
            notice_type=notice_type,
            title=title,
            publish_time=publish_time,
            raw_text=raw_text,
            data=data,
            attachments=cls._attachments(content_nodes[0] if content_nodes else root),
            validation_warnings=cls._validation_warnings(data),
        )

    @staticmethod
    def _validation_warnings(data: Mapping[str, Any]) -> list[str]:
        warnings: list[str] = []
        value = str(data.get("预审文件获取时间") or "")
        dates = [
            tuple(map(int, match))
            for match in re.findall(r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})", value)
        ]
        if len(dates) >= 2 and dates[-1] < dates[0]:
            warnings.append(
                "SOURCE_DATE_RANGE_REVERSED:预审文件获取时间结束日期早于开始日期"
            )
        return warnings

    @classmethod
    def _project_name_365(cls, text: str, title: str) -> str:
        # 标题比正文中的“项目名称”标签稳定；后者经常被编辑器在括号内
        # 强制换行，按行取值会把“汾酒（45度 汇通天下1）”截断。
        value = _space(title)
        value = re.sub(
            r"(?:二次招标公告|二次招标延期公告|招标公告|招标变更公告|招标延期公告|"
            r"招标控制价(?:二次变更)?|招标终止公告|(?:二次招标)?中标候选人公示(?:更正)?|"
            r"(?:二次招标)?中标结果公示|结果公告)$",
            "",
            value,
        ).strip()
        value = re.sub(
            r"\s*[（(](?:不分标段|[^（）()\n]{0,80}标段[^（）()\n]{0,80})[）)]\s*$",
            "",
            value,
        ).strip()
        value = re.sub(r"(?<=[\u4e00-\u9fff\d])\s+(?=[“”])", "", value)
        return value.rstrip(" .。")

    @classmethod
    def _contacts_365(cls, text: str) -> dict[str, dict[str, str]]:
        """修正该站“电话写联系人”和“项目负责人/项目联系人”模板。"""

        contacts = cls._contacts_qianji(text)

        def last_block(pattern: str, end_pattern: str | None = None) -> str:
            matches = list(re.finditer(pattern, text, flags=re.M))
            if not matches:
                return ""
            start = matches[-1].end()
            block = text[start:]
            if end_pattern:
                end = re.search(end_pattern, block, flags=re.M)
                if end:
                    block = block[:end.start()]
            signature = re.search(r"(?m)^\s*招标人或其招标代理机构", block)
            return block[:signature.start()] if signature else block

        owner_block = last_block(
            r"^\s*(?:招标人|采购人)\s*[：:]\s*[^\n]*",
            r"^\s*(?:招标代理机构|采购代理机构|招标代理|代理机构)\s*[：:]",
        )
        if owner_block:
            labelled_phone = re.search(
                r"(?m)^\s*联系电话\s*[：:]\s*([\d\s、,，/-]{6,})", owner_block
            )
            current_phone = str(contacts.get("owner", {}).get("phone") or "")
            if labelled_phone:
                contacts.setdefault("owner", {})["phone"] = _space(labelled_phone.group(1))
            if not contacts.get("owner", {}).get("contact") and current_phone and not re.search(r"\d{6,}", current_phone):
                contacts.setdefault("owner", {})["contact"] = current_phone

        agency_block = last_block(
            r"^\s*(?:招标代理机构|采购代理机构|招标代理|代理机构)\s*[：:]\s*[^\n]*"
        )
        # 政府采购模板使用“2.采购代理机构信息”，项目联系人位于紧随其后的
        # “3.项目联系方式”中，仍属于代理侧对外联系人。
        section_matches = list(re.finditer(
            r"(?m)^\s*2\s*[、.．]\s*采购代理机构信息\s*$", text
        ))
        if section_matches:
            agency_block = text[section_matches[-1].end():]
            signature = re.search(r"(?m)^\s*招标人或其招标代理机构", agency_block)
            if signature:
                agency_block = agency_block[:signature.start()]
        if agency_block:
            contact_matches = re.findall(
                r"(?m)^\s*(?:联系人|项目联系人|项目负责人)\s*[：:]\s*([^\n]+)",
                agency_block,
            )
            if contact_matches:
                contacts.setdefault("agency", {})["contact"] = contact_matches[-1].strip()
            phone_match = re.search(
                r"(?m)^\s*(?:联系电话|联系方式|电话)\s*[：:]\s*([^\n]+(?:\n\s*[\d][\d\s、,，/-]{5,})*)",
                agency_block,
            )
            if phone_match:
                contacts.setdefault("agency", {})["phone"] = _space(phone_match.group(1))
        return contacts

    @classmethod
    def _identifiers_365(cls, text: str) -> tuple[str, str]:
        """按源站标签保留项目号和招标号，不用一个编号盲目填充两个字段。"""

        project_number = cls._identifier_label(
            text, "招标项目编号", "采购项目编号", "项目代码"
        )
        plain_project_number = cls._identifier_exact_365(text, "项目编号")
        tender_number = cls._identifier_label(text, "招标编号", "采购编号", "代理编号")
        if not project_number:
            project_number = plain_project_number
        elif not tender_number and plain_project_number != project_number:
            # 该站少数依法招标模板同时写“招标项目编号(I...)”和
            # “项目编号(Q9...)”；两个源值都保留，后者作为招标编号回退。
            tender_number = plain_project_number
        return project_number, tender_number

    @staticmethod
    def _identifier_exact_365(text: str, label: str) -> str:
        match = re.search(
            rf"(?<![\u4e00-\u9fff]){re.escape(label)}\s*[：:]\s*"
            rf"([A-Za-z0-9][A-Za-z0-9._/-]{{2,190}})",
            text,
        )
        return match.group(1).strip("：:()（）") if match else ""

    @staticmethod
    def _source_nature(title: str, category: str) -> str:
        if category == "termination":
            return next((word + "公告" for word in ("废标", "流标", "终止", "撤销", "暂停") if word in title), "终止公告")
        if category == "change":
            return next((word + "公告" for word in ("控制价", "延期", "澄清", "答疑", "补充", "变更") if word in title), "变更公告")
        return "招标公告"

    @classmethod
    def _tender(cls, text, title, publish_time, contacts, project_type, category):
        deadline = cls._datetime_value_365(cls._last_exact_label(
            text, "递交截止时间", "投标截止时间", "提交投标文件截止时间",
            "响应文件提交截止时间", "投标文件递交截止时间",
        ))
        open_time = cls._datetime_value_365(cls._last_exact_label(text, "开标时间", "开启时间")) or deadline
        amount = cls._control_price_365(text) if category == "change" and "控制价" in title else cls._last_fuzzy_label(
            text, "最高投标限价", "预算金额", "招标金额"
        )
        total_investment = cls._fuzzy_label(text, "项目总投资", "估算金额", "投资估算")
        if not total_investment:
            investment_match = re.search(
                r"(?:工程)?投资额\s*(?:约|为|是|：|:)?\s*([\d,.，]+\s*(?:亿元|万元|元))",
                text,
            )
            total_investment = investment_match.group(1).strip() if investment_match else ""
        opening_place = cls._last_fuzzy_label(text, "开标地点", "开启地点", "递交地址")
        opening_place = re.split(r"[，,](?=逾期递交|逾期送达|未正常递交)", opening_place)[0].strip()
        return {
            "项目性质": "招标信息",
            "源站公告性质": cls._source_nature(title, category),
            "项目名称": cls._project_name_365(text, title),
            "所属行业": cls._fuzzy_label(text, "所属行业"),
            "组织形式": "委托招标" if contacts.get("agency", {}).get("name") else "",
            "开标时间": open_time,
            "项目编号/招标编号": "",
            "项目类型/行业分类": project_type,
            "项目总投资/估算金额": total_investment,
            "招标金额": amount,
            "资金来源": cls._funding_source_365(text),
            "项目地点": cls._fuzzy_label(text, "招标项目所在地区", "项目所在地", "项目地点", "建设地点", "工程地址", "服务地点", "交货地点"),
            "招标人/采购人名称": contacts.get("owner", {}).get("name", ""),
            "项目规模": cls._fuzzy_label(text, "项目规模", "建设规模及内容", "建设规模"),
            "工期/服务期/供货日期": cls._fuzzy_label(text, "合同履行期限", "计划工期", "监理周期", "服务周期", "供货周期", "工期", "服务期限", "服务期", "交货期", "供货期"),
            "质量要求": cls._fuzzy_label(text, "质量要求", "质量标准"),
            "招标内容与范围": cls._section(text, ("项目概况与招标范围", "项目概况", "招标内容与范围", "招标范围"), ("投标人资格要求", "申请人资格要求", "招标文件的获取")),
            "申请人资格要求/投标人资格要求": cls._section(
                text,
                ("投标人资格要求", "申请人资格要求", "申请人的资格要求"),
                ("招标文件的获取", "投标文件的递交", "获取招标文件"),
            ),
            "预审文件获取时间": cls._last_fuzzy_label(text, "获取时间", "招标文件获取时间", "文件发售时间"),
            "获取方式": cls._last_fuzzy_label(text, "获取方法", "获取方式"),
            "递交截止时间": deadline,
            "递交方法": cls._last_fuzzy_label(text, "递交方法", "递交方式"),
            "开启时间": open_time,
            "开启方式": cls._last_fuzzy_label(text, "开标方式", "开启方式"),
            "开启地点": opening_place,
            "评审办法": cls._fuzzy_label(text, "评标办法", "评审办法"),
            "投标保证金方式": cls._guarantee_method(text),
            **cls._contact_fields_qianji({}, contacts, award=False),
            "发布日期": publish_time,
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _datetime_value_365(cls, value: str) -> str:
        return cls._datetime_value(str(value or "").replace("点", "时"))

    @classmethod
    def _funding_source_365(cls, text: str) -> str:
        value = cls._funding_source(text)
        return re.split(r"[，,；;](?=\s*招标人为)", value, maxsplit=1)[0].strip()

    @classmethod
    def _control_price_365(cls, text: str) -> str:
        """只抽取单一总控制价；多标段多个控制价保留在正文而不误选首项。"""

        patterns = (
            r"招标控制总价\s*(?:为|是)?\s*[：:]?\s*[￥¥]?\s*([\d,.，]+\s*(?:万元|元))",
            r"(?<!招标)控制价\s*(?:为|是)?\s*[：:]?\s*[￥¥]?\s*([\d,.，]+\s*(?:万元|元))",
            r"最高投标限价\s*(?:为|是)?\s*[：:]?\s*[￥¥]?\s*([\d,.，]+\s*(?:万元|元))",
        )
        for pattern in patterns:
            matches = re.findall(pattern, text)
            if len(matches) == 1:
                return matches[0].strip()
        return ""

    @classmethod
    def _candidate(cls, raw_html, text, title, publish_time, contacts, project_type):
        details = cls._candidate_table_details_365(raw_html) or cls._candidate_plain_details_365(text)
        return {
            "项目性质": "招标信息",
            "源站公告性质": "中标候选人公示",
            "项目名称": cls._project_name_365(text, title),
            "所属行业": project_type,
            "组织形式": "委托招标" if contacts.get("agency", {}).get("name") else "",
            "开标时间": cls._datetime_value(cls._last_exact_label(text, "开标时间")),
            "公示时间": cls._publicity_time(text),
            "招标编号/项目编号": "",
            "中标候选人名称": [row["候选人名称"] for row in details],
            "中标候选人报价": [row.get("候选人报价", "") for row in details],
            "中标候选人明细": details,
            **cls._contact_fields_qianji({}, contacts, award=True),
            "发布日期": publish_time,
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _candidate_table_details_365(cls, raw_html: str) -> list[dict[str, str]]:
        """兼容含报价表和依法模板中不提供报价列的候选人表。"""

        result: list[dict[str, str]] = []
        for rows in cls._tables(raw_html):
            headers = rows[0]
            name_index = next((i for i, value in enumerate(headers) if "候选人名称" in value), -1)
            if name_index < 0:
                continue
            price_index = next((
                i for i, value in enumerate(headers)
                if any(key in value for key in ("投标报价", "投标总价", "报价", "价格"))
            ), -1)
            price_header = headers[price_index] if price_index >= 0 else ""
            unit_match = re.search(r"[（(]\s*([^（）()]+)\s*[）)]", price_header)
            price_unit = unit_match.group(1).strip() if unit_match else ""
            for cells in rows[1:]:
                if name_index >= len(cells):
                    continue
                name = cells[name_index].strip()
                if not name or any(row["候选人名称"] == name for row in result):
                    continue
                price = cells[price_index].strip() if 0 <= price_index < len(cells) else ""
                if price and price_unit and not re.search(r"(?:亿元|万元|元|%|％)", price):
                    price = f"{price}{price_unit}"
                result.append({"标段": "", "候选人名称": name, "候选人报价": price})
            if result:
                break
        return result

    @classmethod
    def _candidate_plain_details_365(cls, text: str) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        pattern = re.compile(
            r"(?m)^\s*(?:第[一二三四五六七八九十\d]+)?中标候选人(?:名称)?\s*[：:]\s*([^\n]+)"
        )
        matches = list(pattern.finditer(text))
        for index, match in enumerate(matches):
            name = match.group(1).strip()
            if not name or any(row["候选人名称"] == name for row in result):
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else min(
                len(text), match.end() + 500
            )
            block = text[match.end():end]
            price_match = re.search(r"投标报价\s*[：:]\s*([^\n]+)", block)
            result.append({
                "标段": "",
                "候选人名称": name,
                "候选人报价": price_match.group(1).strip() if price_match else "",
            })
        return result

    @classmethod
    def _award(cls, raw_html, text, title, publish_time, contacts, project_type):
        award_text = re.sub(r"中\s*标\s*人", "中标人", text)
        details = (
            cls._award_table_details(raw_html)
            or cls._award_details(award_text)
            or cls._award_stacked_details(award_text)
        )
        union_members: list[str] = []
        union_match = re.search(
            r"中标人\s*[：:]\s*牵头人\s*[：:]\s*([^\n、]+?)\s*、\s*联合体\s*[：:]\s*([^\n]+)",
            text,
        )
        if union_match:
            leader = union_match.group(1).strip()
            union_members = [
                value.strip()
                for value in re.split(r"[、；;]", union_match.group(2))
                if value.strip()
            ]
            if details:
                details[0]["中标人名称"] = leader
        return {
            "项目性质": "招标信息",
            "源站公告性质": "中标结果公示",
            "项目名称": cls._project_name_365(text, title),
            "所属行业": project_type,
            "组织形式": "委托招标" if contacts.get("agency", {}).get("name") else "",
            "招标方式": cls._fuzzy_label(text, "招标方式", "采购方式"),
            "中标人名称": [row["中标人名称"] for row in details],
            "联合体成员": union_members or cls._list_label(text, "联合体成员"),
            "中标价": [row.get("中标价", "") for row in details],
            "中标结果明细": details,
            "工期": cls._fuzzy_label(text, "合同履行期限", "履约期限", "服务期限", "工期", "服务期", "交货期"),
            # 只接受行首业务标签，避免把页脚“主要负责人（项目负责人）：
            # （签名）”误当作中标项目经理。
            "项目经理": cls._label(text, "项目经理", "项目负责人"),
            "项目经理证书名称": cls._fuzzy_label(text, "证书名称"),
            "项目经理证书编号": cls._fuzzy_label(text, "证书编号"),
            **cls._contact_fields_qianji({}, contacts, award=True),
            "依据文件": cls._fuzzy_label(text, "依据文件"),
            "依据文号": "",
            "发布日期": publish_time,
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _attachments(cls, root) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in root.cssselect("a[href]"):
            href = str(anchor.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "#", "mailto:")):
                continue
            url = config.absolute_url(href)
            path = urlsplit(url).path
            name = _space(anchor.text_content()) or Path(path).name
            if not name or url in seen:
                continue
            decoded_path = unquote(path).rstrip("）。；;,.， ")
            suffix = Path(decoded_path).suffix.lower()
            downloadable_suffixes = {
                ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar",
                ".7z", ".ofd", ".dwg", ".txt", ".jpg", ".jpeg", ".png",
            }
            if suffix not in downloadable_suffixes and not re.search(
                r"(?:^|[/_-])(download|attachment|file)(?:[/_?=&-]|$)", url, re.I
            ):
                continue
            seen.add(url)
            result.append({
                "source_file_id": hashlib.sha256(url.encode()).hexdigest()[:20],
                "file_name": name,
                "file_url": url,
                "file_type": mimetypes.guess_type(name or path)[0] or "application/octet-stream",
                "parse_status": "PENDING",
                "source": "public_detail_link",
            })
        return result
