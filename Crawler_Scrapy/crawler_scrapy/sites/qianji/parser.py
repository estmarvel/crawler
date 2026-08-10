"""千极链 Base64 HTML、结构化字段及附件的站点专用解析。"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Mapping

from crawler_scrapy.sites.bitbid.parser import BitbidParser, clean_html
from crawler_scrapy.sites.qianji import config


class QianjiParser(BitbidParser):
    @staticmethod
    def decode_html(detail: Mapping[str, Any]) -> str:
        value = str(detail.get("content") or "").strip()
        if value:
            try:
                return base64.b64decode(value, validate=True).decode("utf-8", errors="replace")
            except (binascii.Error, ValueError, UnicodeError):
                if "<" in value and ">" in value:
                    return value
        return str(detail.get("contentText") or "")

    @classmethod
    def parse(
        cls, feed: str, detail: Mapping[str, Any], *, pdf_text: str = ""
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]], str, str]:
        category, project_type = feed.split(".", 1)
        project_type_name = config.FEEDS[feed][1]
        raw_html = cls.decode_html(detail)
        html_text = clean_html(raw_html)
        text = cls._merge_text(html_text, pdf_text)

        if category == "plan":
            notice_type = "招标计划"
            data = cls._plan_qianji(detail, text)
        elif category in {"tender", "change"}:
            notice_type = "招标公告"
            data = cls._tender_qianji(detail, text, project_type_name, change=category == "change")
        elif category == "candidate":
            notice_type = "中标候选人公示"
            data = cls._candidate_qianji(detail, text, raw_html, project_type_name)
        elif category == "award":
            notice_type = "中标结果公示"
            data = cls._award_qianji(detail, text, raw_html, project_type_name)
        else:
            raise ValueError(f"不支持的千极链栏目：{feed}")
        project_number = cls._identifier_label(
            text,
            "招标项目编号",
            "采购项目编号",
            "投资项目统一代码",
            "项目代码",
            "项目编号",
        )
        tender_number = cls._identifier_label(
            text,
            "招标编号",
            "采购编号",
            "代理编号",
        )
        # 详情接口字段名明确为 projectCode。即使正文同时存在“招标编号”，
        # 也应把 projectCode 保存为独立项目编号，不能因招标编号非空而丢失。
        detail_code = cls._value(detail, "projectCode")
        project_number = project_number or detail_code
        data["项目编号"] = project_number
        data["招标编号"] = tender_number
        combined = "；".join(dict.fromkeys(filter(None, (project_number, tender_number))))
        for field in ("项目编号/招标编号", "招标编号/项目编号"):
            if field in data:
                data[field] = combined
        return notice_type, data, cls.attachments(detail), raw_html, text

    @classmethod
    def _plan_qianji(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        return {
            "项目性质": cls._label(text, "发布类型") or cls._value(d, "bidSituation") or "招标信息",
            "招标方式": cls._label(text, "招标方式") or cls._value(d, "bidTypeName"),
            "项目名称": cls._label(text, "项目名称") or cls._project_name(d, text),
            "项目类型": cls._label(text, "项目类型"),
            "项目总投资": cls._fuzzy_label(text, "项目总投资", "投资估算"),
            "招标内容": cls._label(text, "招标内容"),
            "招标人名称": cls._label(text, "招标人名称", "招标人") or cls._value(d, "zbUnitName"),
            "行政监督部门": cls._label(text, "行政监督部门"),
            "建设地点": cls._label(text, "建设地点"),
            "建设内容及规模": cls._label(text, "建设内容及规模") or cls._section(text, ("建设内容及规模",), ("招标内容", "招标方式")),
            "招标公告（资格预审公告）预计发布时间": cls._label(text, "招标公告（资格预审公告）预计发布时间", "预计发布时间") or cls._value(d, "noticeEndTime"),
            "发布日期": cls._value(d, "noticeStartTime", "createTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _tender_qianji(
        cls, d: Mapping[str, Any], text: str, project_type: str, *, change: bool
    ) -> dict[str, Any]:
        contacts = cls._contacts_qianji(text)
        source_nature = cls._source_nature_qianji(
            cls._value(d, "title"), change=change
        )
        return {
            "项目性质": cls._value(d, "bidSituation") or "招标信息",
            "源站公告性质": source_nature,
            "项目名称": cls._project_name(d, text),
            "所属行业": cls._label(text, "所属行业"),
            "组织形式": cls._label(text, "组织形式") or ("委托招标" if d.get("dlUnitName") else ""),
            "开标时间": cls._datetime_value(cls._last_exact_label(text, "开标时间", "开启时间")),
            "项目编号/招标编号": cls._value(d, "projectCode") or cls._number(text),
            "项目类型/行业分类": project_type,
            "项目总投资/估算金额": cls._label(text, "项目总投资", "估算金额", "投资估算"),
            "招标金额": cls._label(
                text,
                "招标控制价总价",
                "招标控制价",
                "招标金额",
                "最高投标限价",
                "预算金额",
            ),
            "资金来源": cls._funding_source(text),
            "项目地点": cls._fuzzy_label(text, "招标项目所在地", "项目所在地", "项目地点", "建设地点", "工程地址", "服务地点", "交货地点"),
            "招标人/采购人名称": cls._value(d, "zbUnitName") or contacts["owner"].get("name", ""),
            "项目规模": cls._fuzzy_label(text, "项目规模", "建设规模及内容", "建设规模"),
            "工期/服务期/供货日期": cls._fuzzy_label(
                text,
                "合同履行期限",
                "计划工期",
                "监理周期",
                "服务周期",
                "供货周期",
                "工期",
                "服务期限",
                "服务期",
                "交货期",
                "供货期",
            ),
            "质量要求": cls._fuzzy_label(text, "质量要求", "质量标准"),
            "招标内容与范围": cls._section(text, ("项目概况与招标范围", "招标内容与范围", "招标范围"), ("投标人资格要求", "申请人资格要求", "招标文件的获取")),
            "申请人资格要求/投标人资格要求": cls._section(text, ("投标人资格要求", "申请人资格要求"), ("招标文件的获取", "投标文件的递交")),
            "预审文件获取时间": cls._last_fuzzy_label(text, "获取时间", "招标文件获取时间", "文件发售时间"),
            "获取方式": cls._last_fuzzy_label(text, "获取方式", "获取方法"),
            "递交截止时间": cls._datetime_value(cls._last_exact_label(text, "递交截止时间", "投标截止时间")),
            "递交方法": cls._last_fuzzy_label(text, "递交方法", "递交方式") or cls._online_submission(text),
            "开启时间": cls._datetime_value(cls._last_exact_label(text, "开标时间", "开启时间")),
            "开启方式": cls._last_fuzzy_label(text, "开标方式", "开启方式") or cls._online_opening(text),
            "开启地点": cls._last_fuzzy_label(text, "开标地点", "开启地点") or cls._online_place_qianji(text),
            "评审办法": cls._label(text, "评标办法", "评审办法"),
            "投标保证金方式": cls._guarantee_method(text),
            **cls._contact_fields_qianji(d, contacts, award=False),
            "发布日期": cls._value(d, "noticeStartTime", "createTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _candidate_qianji(cls, d: Mapping[str, Any], text: str, raw_html: str, project_type: str) -> dict[str, Any]:
        contacts = cls._contacts_qianji(text)
        details = cls._candidate_table_details(raw_html) or cls._candidate_details(text)
        return {
            "项目性质": cls._value(d, "bidSituation") or "招标信息",
            "源站公告性质": "中标候选人公示",
            "项目名称": cls._project_name(d, text),
            "所属行业": project_type,
            "组织形式": "委托招标" if d.get("dlUnitName") else "",
            "开标时间": cls._label(text, "开标时间"),
            "公示时间": cls._publicity_time(text) or cls._range(cls._value(d, "noticeStartTime"), cls._value(d, "noticeEndTime")),
            "招标编号/项目编号": cls._value(d, "projectCode") or cls._number(text),
            "中标候选人名称": [x["候选人名称"] for x in details],
            "中标候选人报价": [x["候选人报价"] for x in details],
            "中标候选人明细": details,
            **cls._contact_fields_qianji(d, contacts, award=True),
            "发布日期": cls._value(d, "noticeStartTime", "createTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _award_qianji(cls, d: Mapping[str, Any], text: str, raw_html: str, project_type: str) -> dict[str, Any]:
        contacts = cls._contacts_qianji(text)
        # 部分采购结果模板把标签排成“中 标 人”，先消除标签内空格再
        # 使用公共结果解析器，避免把正文中正常的公司名称做模糊猜测。
        award_text = re.sub(r"中\s*标\s*人", "中标人", text)
        details = (
            cls._award_table_details(raw_html)
            or cls._award_details(award_text)
            or cls._award_stacked_details(award_text)
        )
        return {
            "项目性质": cls._value(d, "bidSituation") or "招标信息",
            "源站公告性质": "结果公告",
            "项目名称": cls._project_name(d, text),
            "所属行业": project_type,
            "组织形式": "委托招标" if d.get("dlUnitName") else "",
            "招标方式": cls._value(d, "bidTypeName") or cls._label(text, "招标方式"),
            "中标人名称": [x["中标人名称"] for x in details],
            "联合体成员": cls._list_label(text, "联合体成员"),
            "中标价": [x["中标价"] for x in details],
            "中标结果明细": details,
            "工期": cls._label(
                text,
                "合同履行期限",
                "履约期限",
                "服务期限",
                "工期",
                "服务期",
                "交货期",
            ),
            "项目经理": cls._label(text, "项目经理", "项目负责人"),
            "项目经理证书名称": cls._label(text, "证书名称"),
            "项目经理证书编号": cls._label(text, "证书编号"),
            **cls._contact_fields_qianji(d, contacts, award=True),
            "依据文件": cls._label(text, "依据文件"),
            "依据文号": cls._value(d, "projectCode") or cls._number(text),
            "发布日期": cls._value(d, "noticeStartTime", "createTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @staticmethod
    def _online_place_qianji(text: str) -> str:
        return config.PLATFORM_NAME if "千极" in text and re.search(r"在线|线上|电子", text) else ""

    @staticmethod
    def _source_nature_qianji(title: str, *, change: bool) -> str:
        """保留千极链细分公告性质，数据库公告类型仍由导出器统一编码。"""

        value = str(title or "")
        if "控制价" in value:
            return "招标控制价公告"
        if any(word in value for word in ("暂停", "终止", "撤销")):
            return "招标暂停/终止公告"
        if any(word in value for word in ("流标", "废标")):
            return "流标/废标公告"
        if "延期" in value:
            return "招标延期公告"
        return "招标变更公告" if change else "招标公告"

    @classmethod
    def _fuzzy_label(cls, text: str, *labels: str) -> str:
        value = cls._label(text, *labels)
        if value:
            return value
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}[^：:\n]{{0,25}}[：:]\s*([^\n；;]+)", text
            )
            if match:
                return match.group(1).strip(" ：:;；")
            # 千极链招标计划使用两列表格，标签和值会被HTML清洗成相邻两行。
            match = re.search(
                rf"(?m)^{re.escape(label)}[^\n]{{0,25}}[：:]?\s*\n\s*([^\n]+)", text
            )
            if match:
                return match.group(1).strip(" ：:;；")
        return ""

    @classmethod
    def _last_fuzzy_label(cls, text: str, *labels: str) -> str:
        """取最后一次出现的标签值，适配变更公告的“原内容/变更为”。"""

        candidates: list[tuple[int, str]] = []
        for label in labels:
            pattern = rf"{re.escape(label)}[^：:\n]{{0,25}}[：:]\s*([^\n；;]+)"
            candidates.extend((m.start(), m.group(1).strip(" ：:;；")) for m in re.finditer(pattern, text))
        return max(candidates, default=(-1, ""), key=lambda item: item[0])[1]

    @staticmethod
    def _last_exact_label(text: str, *labels: str) -> str:
        candidates: list[tuple[int, str]] = []
        for label in labels:
            pattern = rf"{re.escape(label)}\s*[：:]\s*([^\n；;]+)"
            candidates.extend((m.start(), m.group(1).strip()) for m in re.finditer(pattern, text))
        return max(candidates, default=(-1, ""), key=lambda item: item[0])[1]

    @staticmethod
    def _datetime_value(value: str) -> str:
        text = str(value or "").replace("：", ":").strip()
        match = re.search(
            r"(20\d{2})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*"
            r"(\d{1,2})\s*日?\s*(上午|下午|晚上)?\s*"
            r"(\d{1,2})\s*[:时]\s*(\d{1,2})"
            r"(?:\s*分|\s*:\s*(\d{1,2}))?",
            text,
        )
        if not match:
            return ""
        year, month, day, period, hour, minute, second = match.groups()
        hour_value = int(hour)
        if period in {"下午", "晚上"} and hour_value < 12:
            hour_value += 12
        return f"{year}-{int(month):02d}-{int(day):02d} {hour_value:02d}:{int(minute):02d}:{int(second or 0):02d}"

    @staticmethod
    def _funding_source(text: str) -> str:
        match = re.search(r"资金来源(?:为|[：:])\s*([^。；;\n]+)", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _online_submission(text: str) -> str:
        if re.search(r"(?s)(?:投标文件|电子版投标文件).{0,160}(?:在线递交|网上递交|上传|电子交易平台)", text):
            return "通过千极数采电子交易平台在线递交"
        return ""

    @staticmethod
    def _online_opening(text: str) -> str:
        if re.search(r"开标(?:地点|方式).{0,100}线上开标", text):
            return "线上开标"
        return ""

    @classmethod
    def _guarantee_method(cls, text: str) -> str:
        direct = cls._last_fuzzy_label(text, "投标保证金方式", "保证金递交方式")
        if direct:
            return direct
        match = re.search(
            r"(?s)提交投标保证金的形式\s*\n?\s*(.*?)(?=\n\s*(?:八|九|十|\d+)[、.]|\Z)",
            text,
        )
        return " ".join(match.group(1).split()).strip() if match else ""

    @classmethod
    def _contacts_qianji(cls, text: str) -> dict[str, dict[str, str]]:
        """按行切分联系方式，避免监督电话或另一方信息串入代理机构。"""

        result: dict[str, dict[str, str]] = {"owner": {}, "agency": {}}
        party_pattern = re.compile(
            r"(?m)^\s*(招标人|采购人|招标代理机构|采购代理机构|"
            r"采购代理|招标代理|代理机构)\s*[：:]\s*([^\n]*)"
        )
        matches = list(party_pattern.finditer(text))
        candidates: dict[str, list[dict[str, str]]] = {"owner": [], "agency": []}

        def spaced_label(label: str) -> str:
            return r"\s*".join(map(re.escape, label))

        def line_value(block: str, *labels: str) -> str:
            found: list[str] = []
            for field_label in labels:
                found.extend(
                    value.strip()
                    for value in re.findall(
                        rf"(?m)^\s*{spaced_label(field_label)}\s*[：:]\s*([^\n]+)",
                        block,
                    )
                    if value.strip()
                )
            return found[-1] if found else ""

        for index, match in enumerate(matches):
            label, name = match.group(1), match.group(2).strip()
            # “招标人或其招标代理机构”签章行不会满足行首精确标签；下一方
            # 联系方式或签章区均作为当前块的边界。
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            block = text[match.end():end]
            signature = re.search(r"(?m)^\s*招标人或其招标代理机构", block)
            if signature:
                block = block[:signature.start()]

            party = "owner" if label in {"招标人", "采购人"} else "agency"
            candidates[party].append({
                "name": name,
                "address": line_value(block, "地址", "联系地址"),
                "contact": line_value(block, "联系人"),
                "phone": line_value(block, "联系电话", "联系方式", "电话"),
            })

        # 采购结果常用“1、招标人信息 / 名 称：...”分节，不会出现
        # “招标人：...”这一行。按节标题确定角色，仍只读取本节内明确标签。
        section_pattern = re.compile(
            r"(?m)^\s*\d+\s*[、.．]\s*"
            r"(招标人|采购人|招标代理机构|采购代理机构)信息\s*$"
        )
        sections = list(section_pattern.finditer(text))
        for index, match in enumerate(sections):
            label = match.group(1)
            end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
            block = text[match.end():end]
            party = "owner" if label in {"招标人", "采购人"} else "agency"
            name_labels = (
                ("名称", "招标人", "采购人")
                if party == "owner"
                else ("名称", "招标代理机构", "采购代理机构", "采购代理")
            )
            candidates[party].append({
                "name": line_value(block, *name_labels),
                "address": line_value(block, "地址", "联系地址"),
                "contact": line_value(block, "联系人"),
                "phone": line_value(block, "联系电话", "联系方式", "电话"),
            })

        for party, values in candidates.items():
            if values:
                result[party] = max(
                    values,
                    key=lambda item: sum(bool(item.get(field)) for field in item),
                )
        return result

    @staticmethod
    def _tables(raw_html: str) -> list[list[list[str]]]:
        if not raw_html:
            return []
        try:
            from lxml import html as lxml_html
            root = lxml_html.fromstring(raw_html)
        except Exception:
            return []
        tables = []
        for table in root.xpath("//table"):
            rows = []
            for tr in table.xpath(".//tr"):
                cells = [" ".join(cell.text_content().split()) for cell in tr.xpath("./th|./td")]
                if cells:
                    rows.append(cells)
            if rows:
                tables.append(rows)
        return tables

    @classmethod
    def _candidate_table_details(cls, raw_html: str) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for rows in cls._tables(raw_html):
            headers = rows[0]
            name_index = next((i for i, x in enumerate(headers) if "候选人名称" in x), -1)
            price_index = next((i for i, x in enumerate(headers) if any(k in x for k in ("投标报价", "投标总价", "报价", "价格"))), -1)
            if name_index < 0 or price_index < 0:
                continue
            for cells in rows[1:]:
                if max(name_index, price_index) >= len(cells):
                    continue
                name, price = cells[name_index].strip(), cells[price_index].strip()
                if name and not any(x["候选人名称"] == name for x in result):
                    result.append({"标段": "", "候选人名称": name, "候选人报价": price})
            if result:
                break
        return result

    @classmethod
    def _award_table_details(cls, raw_html: str) -> list[dict[str, str]]:
        tables = cls._tables(raw_html)
        name = price = ""
        for rows in tables:
            for cells in rows:
                for index, cell in enumerate(cells):
                    normalized = cell.replace(" ", "")
                    if normalized.rstrip("：:") == "中标人" and index + 1 < len(cells):
                        name = cells[index + 1].strip()
                    if ("中标价格" in normalized or normalized.rstrip("：:") == "中标价") and index + 1 < len(cells):
                        price = cells[index + 1].strip()
                    if "总合计" in normalized and len(cells) > 1:
                        price = cells[-1].strip()
        return [{"标段": "", "中标人名称": name, "中标价": price}] if name else []

    @classmethod
    def _award_stacked_details(cls, text: str) -> list[dict[str, str]]:
        """解析表格清洗后形成“标签换行值”的结果模板。"""

        name_match = re.search(r"(?m)^\s*中标人\s*\n\s*([^\n]{2,100})", text)
        if not name_match:
            return []
        name = name_match.group(1).strip()
        price_match = re.search(
            r"(?m)^\s*总合计\s*[（(]?元[）)]?\s*\n\s*([\d,.，]+)",
            text,
        )
        price = f"{price_match.group(1).strip()}元" if price_match else ""
        return [{"标段": "", "中标人名称": name, "中标价": price}]

    @classmethod
    def _contact_fields_qianji(
        cls, d: Mapping[str, Any], contacts: Mapping[str, Mapping[str, str]], *, award: bool
    ) -> dict[str, str]:
        result = cls._contact_fields(contacts, award=award)
        owner_key = "招标人/采购人" if award else "招标人/采购人名称"
        result[owner_key] = cls._value(d, "zbUnitName") or result[owner_key]
        result["招标代理机构"] = cls._value(d, "dlUnitName") or result["招标代理机构"]
        return result

    @classmethod
    def attachments(cls, d: Mapping[str, Any]) -> list[dict[str, Any]]:
        result = []
        values = d.get("attachmentList") or []
        if not isinstance(values, list):
            return result
        for item in values:
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("attachmentAddr") or "").strip()
            if not url:
                continue
            result.append({
                "source_file_id": str(item.get("id") or ""),
                "file_name": str(item.get("attachmentName") or url.rsplit("/", 1)[-1]),
                "file_url": url,
                "file_type": "application/pdf" if ".pdf" in url.lower() else "application/octet-stream",
                "parse_status": "PENDING",
                "source": "detail_attachment",
            })
        return result
