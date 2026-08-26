"""千极链 Base64 HTML、结构化字段及附件的站点专用解析。"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Mapping

from crawler_scrapy.sites.bitbid.parser import BitbidParser, clean_html
from crawler_scrapy.sites.qianji import config
from crawler_scrapy.sites.qianji.ai_review import (
    extract_identifier_evidence,
    normalize_identifier,
    unique_identifiers,
)


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
            if re.search(r"(?:中标结果|成交结果).*(?:更正|变更)|(?:更正|变更).*(?:中标结果|成交结果)", cls._value(detail, "title")):
                notice_type = "更正结果公示"
                data = cls._award_correction_qianji(detail, text)
            else:
                notice_type = "中标结果公示"
                data = cls._award_qianji(detail, text, raw_html, project_type_name)
        else:
            raise ValueError(f"不支持的千极链栏目：{feed}")
        project_number, tender_number = cls._identifiers_qianji(detail, text)
        data["项目编号"] = project_number
        data["招标编号"] = tender_number
        combined = "；".join(dict.fromkeys(filter(None, (project_number, tender_number))))
        for field in ("项目编号/招标编号", "招标编号/项目编号"):
            if field in data:
                data[field] = combined
        return notice_type, data, cls.attachments(detail), raw_html, text

    @classmethod
    def _identifiers_qianji(
        cls, detail: Mapping[str, Any], text: str
    ) -> tuple[str, str]:
        """按千极链/API语义严格区分项目编号与招标编号。

        ``projectCode`` 是详情 API 返回的平台项目代码，并不保证在公告正文
        中可见；当前将其映射为项目编号，是为了使用源站稳定项目身份做后续
        数据库关联。正文用于校验该映射、API 缺失时补项目编号，以及提取
        招标/采购/代理编号。变更公告偶尔把与 ``projectCode`` 不同的代理编号
        写成“项目编号”，这种值只能作为招标编号兜底，不能覆盖 API 项目代码。
        """

        evidence = extract_identifier_evidence(text)
        detail_code = cls._value(detail, "projectCode")
        explicit_project = unique_identifiers(
            item.value
            for item in evidence
            if item.label
            in {"招标项目编号", "采购项目编号", "投资项目统一代码", "项目代码"}
        )
        generic_project = unique_identifiers(
            item.value for item in evidence if item.label == "项目编号"
        )
        explicit_tender = unique_identifiers(
            item.value
            for item in evidence
            if item.label in {"招标编号", "采购编号", "代理编号"}
        )
        project_number = detail_code or next(
            iter(explicit_project or generic_project), ""
        )
        tender_number = next(iter(explicit_tender), "")
        if not tender_number and detail_code:
            tender_number = next(
                (
                    value
                    for value in generic_project
                    if normalize_identifier(value)
                    != normalize_identifier(detail_code)
                ),
                "",
            )
        return project_number, tender_number

    @classmethod
    def identifier_source_metadata(
        cls, detail: Mapping[str, Any], text: str
    ) -> dict[str, Any]:
        """记录编号的真实来源，避免 API-only 值被误认为正文提取值。"""

        project_number, tender_number = cls._identifiers_qianji(detail, text)
        evidence = extract_identifier_evidence(text)
        api_code = cls._value(detail, "projectCode")
        api_normalized = normalize_identifier(api_code)
        project_visible = bool(
            api_normalized and api_normalized in normalize_identifier(text)
        )
        tender_labels = list(
            dict.fromkeys(
                item.label
                for item in evidence
                if tender_number
                and normalize_identifier(item.value)
                == normalize_identifier(tender_number)
            )
        )
        return {
            "version": "qianji-identifiers-v2",
            "projectNumber": {
                "value": project_number,
                "source": "detail_api.projectCode"
                if api_code
                else ("body_exact_label" if project_number else "missing"),
                "visibleInBody": project_visible if api_code else bool(project_number),
                "bodyLabels": list(
                    dict.fromkeys(
                        item.label
                        for item in evidence
                        if project_number
                        and normalize_identifier(item.value)
                        == normalize_identifier(project_number)
                    )
                ),
            },
            "tenderNumber": {
                "value": tender_number,
                "source": "body_exact_label" if tender_number else "missing",
                "visibleInBody": bool(tender_number),
                "bodyLabels": tender_labels,
            },
        }

    @classmethod
    def _plan_qianji(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        return {
            "项目性质": cls._project_nature_qianji(
                cls._label(text, "发布类型") or cls._value(d, "bidSituation")
            ),
            "招标方式": cls._label(text, "招标方式") or cls._value(d, "bidTypeName"),
            "项目名称": cls._label(text, "项目名称") or cls._project_name_qianji(d, text),
            "项目类型": cls._label(text, "项目类型"),
            "项目总投资": cls._plan_total_investment_qianji(text),
            "招标内容": cls._label(text, "招标内容"),
            "招标人名称": cls._label(text, "招标人名称", "招标人") or cls._value(d, "zbUnitName"),
            "行政监督部门": cls._label(text, "行政监督部门"),
            "建设地点": cls._label(text, "建设地点"),
            "建设内容及规模": cls._label(text, "建设内容及规模") or cls._section(text, ("建设内容及规模",), ("招标内容", "招标方式")),
            "招标公告（资格预审公告）预计发布时间": (
                cls._planned_publish_time_qianji(text)
                or cls._planned_publish_time_qianji(
                    cls._value(d, "noticeEndTime")
                )
            ),
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
            "项目性质": cls._project_nature_qianji(cls._value(d, "bidSituation")),
            "源站公告性质": source_nature,
            "项目名称": cls._project_name_qianji(d, text),
            "所属行业": cls._label(text, "所属行业"),
            "组织形式": cls._label(text, "组织形式") or ("委托招标" if d.get("dlUnitName") else ""),
            "开标时间": cls._datetime_value(cls._last_exact_label(text, "开标时间")),
            "项目编号/招标编号": cls._value(d, "projectCode") or cls._number(text),
            "项目类型/行业分类": project_type,
            "项目总投资/估算金额": cls._fuzzy_label(
                text, "项目总投资", "总投资额", "估算金额", "投资估算"
            ),
            "招标金额": cls._tender_amount_qianji(text),
            "资金来源": cls._funding_source(text),
            "项目地点": cls._fuzzy_label(
                text,
                "建设地址",
                "建设地点",
                "项目地点",
                "项目所在地",
                "招标项目所在地",
                "工程地址",
                "服务地点",
                "交货地点",
            ),
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
            # 常见模板在“项目概况与招标范围”大节内依次列出项目名称、
            # 编号、规模、招标范围和总投资。优先读取 2.4 等独立标签，
            # 避免把相邻字段一并写入；只有没有独立标签时才保留整节兜底。
            "招标内容与范围": cls._fuzzy_label(
                text,
                "招标内容与范围",
                "招标范围及内容",
                "招标内容及范围",
                "招标内容",
                "招标范围",
            )
            or cls._section(
                text,
                ("项目概况与招标范围", "招标内容与范围", "招标范围"),
                ("投标人资格要求", "申请人资格要求", "招标文件的获取"),
            ),
            "申请人资格要求/投标人资格要求": cls._section(text, ("投标人资格要求", "申请人资格要求"), ("招标文件的获取", "投标文件的递交")),
            "预审文件获取时间": cls._file_acquisition_time_qianji(text),
            "获取方式": cls._last_fuzzy_label(text, "获取方式", "获取方法"),
            "递交截止时间": cls._datetime_value(
                cls._last_exact_label(
                    text,
                    "递交截止时间",
                    "递交的截止时间",
                    "投标截止时间",
                )
            ),
            "递交方法": cls._submission_method(text),
            # 开标时间与开启时间是两个独立预设字段。即使源站常把两者
            # 安排在同一时刻，也不能仅因数值相同就把“开标时间”复制过来。
            "开启时间": cls._datetime_value(cls._last_exact_label(text, "开启时间")),
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
            "项目性质": cls._project_nature_qianji(cls._value(d, "bidSituation")),
            "源站公告性质": "中标候选人公示",
            "项目名称": cls._project_name_qianji(d, text),
            # 工程/货物/服务是源站栏目类型，不是行业。只有正文明确给出
            # 行业时才填，避免导入后污染 Project.industry。
            "所属行业": cls._label(text, "所属行业", "行业分类"),
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
            or cls._award_admitted_details(award_text)
        )
        return {
            "项目性质": cls._project_nature_qianji(cls._value(d, "bidSituation")),
            "源站公告性质": "结果公告",
            "项目名称": cls._project_name_qianji(d, text),
            "所属行业": cls._label(text, "所属行业", "行业分类"),
            "组织形式": "委托招标" if d.get("dlUnitName") else "",
            "招标方式": cls._value(d, "bidTypeName") or cls._label(text, "招标方式"),
            "中标人名称": [x["中标人名称"] for x in details],
            "联合体成员": cls._consortium_members_qianji(text),
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
            # projectCode 是项目编号，不是立项/批准文件文号。
            "依据文号": cls._label(text, "依据文号", "批准文号"),
            "发布日期": cls._value(d, "noticeStartTime", "createTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _award_correction_qianji(
        cls, d: Mapping[str, Any], text: str
    ) -> dict[str, Any]:
        """结果栏目中的更正公告按 CORRECTION Schema 保存，不伪造中标人。"""

        contacts = cls._contacts_qianji(text)
        supervision = cls._supervision_contacts_qianji(text)
        return {
            "公共类型": "更正公告",
            "项目名称": cls._project_name_qianji(d, text),
            "所属行业": cls._label(text, "所属行业", "行业分类"),
            "组织形式": "委托招标" if d.get("dlUnitName") else "",
            "开标时间": cls._datetime_value(cls._last_exact_label(text, "开标时间")),
            "标书发售时间": cls._last_fuzzy_label(
                text, "招标文件获取时间", "文件发售时间", "获取时间"
            ),
            # 更正正文内部常用“1、项目名称 / 2、监督部门”列出被改字段，
            # 通用章节切分会把这些子项误当成外层章节。完整保留正文最安全，
            # 同时满足更正前后内容和溯源要求。
            "公告内容": text,
            "招标人地址": contacts["owner"].get("address", ""),
            "招标人联系人": contacts["owner"].get("contact", ""),
            "招标人联系方式": contacts["owner"].get("phone", ""),
            "招标代理机构": cls._value(d, "dlUnitName")
            or contacts["agency"].get("name", ""),
            "招标代理机构地址": contacts["agency"].get("address", ""),
            "招标代理机构联系人": contacts["agency"].get("contact", ""),
            "招标代理机构联系方式": contacts["agency"].get("phone", ""),
            "监督部门地址": supervision.get("address", ""),
            "监督部门联系人": supervision.get("contact", ""),
            "监督部门联系方式": supervision.get("phone", ""),
            "依据文件": cls._label(text, "依据文件"),
            "依据文号": cls._label(text, "依据文号", "批准文号"),
            "发布日期": cls._value(d, "noticeStartTime", "createTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @staticmethod
    def _online_place_qianji(text: str) -> str:
        return config.PLATFORM_NAME if "千极" in text and re.search(r"在线|线上|电子", text) else ""

    @classmethod
    def _project_name_qianji(cls, d: Mapping[str, Any], text: str) -> str:
        """删除公告阶段/轮次后缀，只保留可用于跨公告关联的项目名称。"""

        title = cls._value(d, "title", "name") or cls._project_name(d, text)
        value = str(title or "").strip()
        suffixes = (
            r"中标结果(?:公告|公示)(?:更正|变更)",
            r"招标暂停\s*/\s*终止公告",
            r"招标(?:暂停|终止|撤销)公告",
            r"招标(?:第?[一二三四五六七八九十\d]+次)?变更公告",
            r"招标(?:第?[一二三四五六七八九十\d]+次)?延期公告",
            r"招标控制价(?:变更)?(?:公告)?",
            r"最高投标限价(?:公告)?",
            r"(?:流标|废标|终止|撤销|暂停)(?:公告|公示)",
            r"中标候选人公示",
            r"中标结果(?:公告|公示)",
            r"招标公告",
        )
        for suffix in suffixes:
            updated = re.sub(rf"(?:\s*[-—–]\s*)?(?:{suffix})\s*$", "", value)
            if updated != value:
                value = updated.strip()
                break
        # “二次/三次”表示公告轮次而不是项目实体名称；只在刚删除了
        # 招标公告类后缀后处理末尾轮次，避免误删“二期”等项目分期。
        value = re.sub(
            r"(?:\s*[-—–]\s*)?(?:第?[一二三四五六七八九十\d]+次|重新)\s*$",
            "",
            value,
        ).strip()
        return value.rstrip("/-—– ")

    @staticmethod
    def _project_nature_qianji(value: Any) -> str:
        """严格输出数据库/字段契约允许的项目性质枚举。"""

        text = re.sub(r"\s+", "", str(value or ""))
        if re.search(
            r"(?:(?:非依法|不属于依法|不属依法|无需依法|不需要依法|不是依法)"
            r"(?:必须)?(?:进行)?招标|非依法(?:必须)?招标项目|非依法项目)",
            text,
        ):
            return "非依法招标"
        if re.search(
            r"(?:依法(?:必须)?(?:进行)?招标|依法(?:必须)?招标项目|依法项目)",
            text,
        ):
            return "依法必须招标"
        return ""

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
            # 标签后的冒号必须紧邻标签（可带“北京时间”等短括注）。旧实现
            # 允许跨越 25 个任意字符，会把“获取时间内通过 https:”中的 URL
            # 冒号误当字段分隔符，最终提取成 //www.qianjilink.com。
            pattern = (
                rf"{re.escape(label)}"
                rf"(?:\s*[（(][^）)\n]{{0,20}}[）)])?\s*[：:]\s*"
                rf"([^\n；;]+)"
            )
            candidates.extend(
                (m.start(), m.group(1).strip(" ：:;；"))
                for m in re.finditer(pattern, text)
            )
            next_line = rf"(?m)^{re.escape(label)}\s*[：:]?\s*\n\s*([^\n]+)"
            candidates.extend(
                (m.start(), m.group(1).strip(" ：:;；"))
                for m in re.finditer(next_line, text)
            )
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
            r"(\d{1,2})\s*[:时]\s*(\d{1,2})?"
            r"(?:\s*分|\s*:\s*(\d{1,2}))?",
            text,
        )
        if not match:
            return ""
        year, month, day, period, hour, minute, second = match.groups()
        hour_value = int(hour)
        if period in {"下午", "晚上"} and hour_value < 12:
            hour_value += 12
        return (
            f"{year}-{int(month):02d}-{int(day):02d} "
            f"{hour_value:02d}:{int(minute or 0):02d}:{int(second or 0):02d}"
        )

    @classmethod
    def _plan_total_investment_qianji(cls, text: str) -> str:
        """保留计划表金额单位，避免“440万元”被当作440元。"""

        value = cls._fuzzy_label(text, "项目总投资", "投资估算")
        if not value or re.search(r"亿元|万元|元", value):
            return value
        label = re.search(
            r"(?:项目总投资|投资估算)\s*[（(]([^）)\n]+)[）)]",
            text,
        )
        if label and "万元" in label.group(1) and re.fullmatch(
            r"[\d,.，]+", value.strip()
        ):
            return f"{value.strip()}万元"
        return value

    @classmethod
    def _planned_publish_time_qianji(cls, text: str) -> str:
        """优先保留正文计划日期，并把紧凑时间格式变成可读格式。"""

        value = cls._fuzzy_label(
            text,
            "招标公告（资格预审公告）预计发布时间",
            "资格预审公告预计发布时间",
            "招标公告预计发布时间",
            "预计发布时间",
        )
        if not value:
            plain = str(text or "").strip()
            if not re.fullmatch(
                r"(?:20\d{2})(?:[-年/.]?\d{1,2})?(?:[-月/.]?\d{1,2})?"
                r"(?:[ T日]?\d{1,2}(?:[:时]\d{1,2})?(?:[:分]\d{1,2})?秒?)?",
                plain,
            ):
                return ""
            value = plain
        digits = re.sub(r"\D", "", value)
        if len(digits) == 14:
            date = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            return (
                date
                if digits[8:] == "000000"
                else f"{date} {digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
            )
        if len(digits) == 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        if len(digits) == 6:
            return f"{digits[:4]}-{digits[4:6]}"
        return value

    @staticmethod
    def _funding_source(text: str) -> str:
        match = re.search(
            r"(?:建设)?资金(?:来源)?\s*(?:为|是|由|[：:])\s*"
            r"(.+?)(?=[，,]\s*(?:项目出资比例|招标人|采购人|项目业主)"
            r"|[。；;\n]|$)",
            text,
        )
        return match.group(1).strip(" ，,") if match else ""

    @classmethod
    def _file_acquisition_time_qianji(cls, text: str) -> str:
        """提取招标文件获取区间，兼容“获取电子招标文件开始时间”。"""

        range_pattern = re.compile(
            r"(?:获取电子招标文件|电子招标文件获取|招标文件获取|文件获取)"
            r"(?:的)?开始时间\s*[：:]\s*([^；;\n]+?)\s*"
            r"[；;]\s*(?:(?:获取电子招标文件|电子招标文件获取|招标文件获取|文件获取)"
            r"(?:的)?)?(?:截止|结束)时间\s*[：:]\s*([^；;\n]+)"
        )
        matches = list(range_pattern.finditer(text))
        if matches:
            match = matches[-1]
            start = match.group(1).strip(" 。；;")
            end = match.group(2).strip(" 。；;")
            return f"{start}---{end}"
        return cls._last_fuzzy_label(
            text,
            "获取电子招标文件开始时间",
            "电子招标文件获取时间",
            "招标文件获取时间",
            "文件获取时间",
            "获取时间",
            "文件发售时间",
        )

    @staticmethod
    def _consortium_members_qianji(text: str) -> list[str]:
        """只读取正文明确标注的联合体成员，不把牵头人重复写入。"""

        values: list[str] = []
        pattern = re.compile(
            r"(?:联合体(?:成员|单位)(?:名称)?|成员单位(?:名称)?)\s*[：:]\s*"
            r"([^\n；;]+)"
        )
        for match in pattern.finditer(text):
            for value in re.split(r"[、,，；;]", match.group(1)):
                cleaned = value.strip()
                if cleaned and cleaned not in values:
                    values.append(cleaned)
        return values

    @staticmethod
    def _tender_amount_qianji(text: str) -> str:
        """金额字段优先取可写入数据库 Decimal 的数字小写值。"""

        labels = (
            "招标控制价总价",
            "最高投标限价总价",
            "招标控制价",
            "招标金额",
            "最高投标限价",
            "预算金额",
        )
        pattern = "|".join(re.escape(label) for label in labels)
        candidates: list[tuple[int, str]] = []
        for match in re.finditer(
            rf"(?:{pattern})\s*[：:]\s*([\s\S]{{0,180}})", text
        ):
            nearby = "\n".join(match.group(1).splitlines()[:3])
            numbers = re.findall(
                r"(?:小写\s*[：:]\s*)?([\d,.，]+\s*(?:亿元|万元|元))",
                nearby,
            )
            if numbers:
                candidates.append((match.start(), numbers[0].replace("，", ",")))
        return max(candidates, default=(-1, ""), key=lambda item: item[0])[1]

    @staticmethod
    def _online_submission(text: str) -> str:
        if re.search(r"(?s)(?:投标文件|电子版投标文件).{0,160}(?:在线递交|网上递交|上传|电子交易平台)", text):
            return "通过千极数采电子交易平台在线递交"
        return ""

    @classmethod
    def _submission_method(cls, text: str) -> str:
        value = cls._last_fuzzy_label(text, "递交方法", "递交方式")
        if not value:
            return cls._online_submission(text)
        # 个别源站模板把无实际语义的“届时”误留在上传动作末尾，并在同一
        # 行继续拼接“递交地址”。方法字段只保留动作，地址由独立字段承担。
        value = re.split(r"递交地址\s*[：:]", value, maxsplit=1)[0]
        return re.sub(r"届时\s*[。；;]?\s*$", "", value).strip(" 。；;")

    @staticmethod
    def _online_opening(text: str) -> str:
        if re.search(r"开标(?:地点|方式).{0,100}线上开标", text):
            return "线上开标"
        return ""

    @classmethod
    def _guarantee_method(cls, text: str) -> str:
        direct = cls._last_fuzzy_label(text, "投标保证金方式", "保证金递交方式")
        match = re.search(
            r"(?s)提交(?:投标)?保证金的(?:形式|方式)\s*\n?\s*(.*?)"
            r"(?=\n\s*(?:[一二三四五六七八九十]+|\d+)\s*[、.．]?\s*\S|\Z)",
            text,
        )
        value = direct or (" ".join(match.group(1).split()).strip() if match else "")
        value = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", value)
        value = re.sub(r"^(?:本项目)?(?:可以|可)?采用\s*", "", value)
        value = re.sub(
            r"\s*提交(?:本项目)?(?:的)?投标保证金\s*[。；;]?$",
            "",
            value,
        )
        return value.strip(" 。；;")

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

    @classmethod
    def _supervision_contacts_qianji(cls, text: str) -> dict[str, str]:
        match = re.search(
            r"(?ms)^\s*(?:[一二三四五六七八九十]+[、.．]?\s*)?监督部门\s*$"
            r"(.*?)(?=^\s*(?:[一二三四五六七八九十]+[、.．]?\s*)?联系方式\s*$|\Z)",
            text,
        )
        if not match:
            return {}
        block = match.group(1)
        return {
            "address": cls._label(block, "地址"),
            "contact": cls._label(block, "联系人"),
            "phone": cls._label(block, "联系电话", "联系方式", "电话"),
        }

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
            if name_index < 0:
                continue
            # 优先使用源站明确的总价列。若只有多个组成价格（例如EPC的
            # 建安工程费+设计费），不能把第一列冒充总报价，保留带列名的
            # 原始组成文本，使关系库金额保持为空而 structured_data 可追溯。
            total_index = next(
                (
                    i
                    for i, value in enumerate(headers)
                    if any(
                        key in value.replace(" ", "")
                        for key in ("投标总价", "投标报价", "中标总价", "总报价")
                    )
                ),
                -1,
            )
            price_indexes = [
                i
                for i, value in enumerate(headers)
                if i != name_index
                and any(
                    key in value.replace(" ", "")
                    for key in (
                        "报价",
                        "价格",
                        "工程费",
                        "设计费",
                        "服务费",
                        "采购及安装费",
                        "采购费",
                        "安装费",
                    )
                )
            ]
            for cells in rows[1:]:
                if name_index >= len(cells):
                    continue
                name = cells[name_index].strip()
                if total_index >= 0 and total_index < len(cells):
                    price = cells[total_index].strip()
                elif not price_indexes:
                    price = ""
                else:
                    components = [
                        f"{headers[i].strip()}：{cells[i].strip()}"
                        for i in price_indexes
                        if i < len(cells) and cells[i].strip()
                    ]
                    price = "；".join(components)
                if not name:
                    continue
                existing = next(
                    (item for item in result if item["候选人名称"] == name), None
                )
                if existing is None:
                    result.append(
                        {"标段": "", "候选人名称": name, "候选人报价": price}
                    )
                elif not existing["候选人报价"] and price:
                    existing["候选人报价"] = price
                elif price and price != existing["候选人报价"]:
                    # 同一企业确实在不同价格行出现时保留第二行；无报价的
                    # 项目经理/资格响应重复表不会制造额外候选人。
                    result.append(
                        {"标段": "", "候选人名称": name, "候选人报价": price}
                    )
        return result

    @classmethod
    def _award_winner_name(cls, value: Any) -> str:
        """从中标人单元格提取牵头人，并拒绝表头等伪名称。"""

        text = " ".join(str(value or "").replace("\u00a0", " ").split()).strip()
        if not text:
            return ""
        leader = re.search(
            r"(?:联合体)?牵头人(?:单位)?名称\s*[：:]\s*"
            r"(.+?)(?=\s*(?:联合体(?:成员|单位)(?:名称)?|成员单位(?:名称)?)\s*[：:]|$)",
            text,
        )
        if leader:
            text = leader.group(1).strip()
        else:
            text = re.split(
                r"\s*(?:联合体(?:成员|单位)(?:名称)?|成员单位(?:名称)?)\s*[：:]",
                text,
                maxsplit=1,
            )[0]
            text = re.sub(
                r"^(?:中标人(?:名称)?|中标单位名称|投标人名称|单位名称)\s*[：:]\s*",
                "",
                text,
            ).strip()
        invalid = {
            "中标人", "中标人名称", "中标单位名称", "投标人名称", "单位名称",
            "中标价", "中标价格", "投标总价", "含税总价", "总合计", "排序", "序号",
        }
        return "" if text.rstrip("：:") in invalid or text.isdigit() else text

    @staticmethod
    def _award_price_value(header: Any, value: Any) -> str:
        """只返回源文明确的单一中标总价，复合价格组成不冒充总价。"""

        header_text = "".join(str(header or "").split())
        text = " ".join(str(value or "").replace("\u00a0", " ").split()).strip()
        if not text:
            return ""
        component_labels = re.findall(
            r"(?:建安工程费|建筑安装工程费|设计费|设备费|采购费|安装费|服务费)"
            r"(?:\s*[（(][^）)]*[）)])?\s*[：:]",
            text,
        )
        if component_labels and not re.search(
            r"(?:中标总价|投标总价|含税总价|总合计)\s*[：:]", text
        ):
            return ""
        text = re.sub(
            r"^(?:中标价格?|中标金额|投标总价|含税总价|总合计)\s*[：:]\s*",
            "",
            text,
        ).strip()
        if header_text.rstrip("：:") in {"中标人", "中标人名称", "排序", "序号"}:
            return ""
        return text

    @classmethod
    def _award_table_details(cls, raw_html: str) -> list[dict[str, str]]:
        tables = cls._tables(raw_html)
        result: list[dict[str, str]] = []
        for rows in tables:
            if not rows:
                continue
            headers = rows[0]
            name_index = next(
                (
                    i
                    for i, value in enumerate(headers)
                    if any(
                        key in value.replace(" ", "")
                        for key in (
                            "中标人名称",
                            "中标人",
                            "中标单位名称",
                            "投标人名称",
                            "单位名称",
                        )
                    )
                ),
                -1,
            )
            price_index = next(
                (
                    i
                    for i, value in enumerate(headers)
                    if any(
                        key in value.replace(" ", "")
                        for key in (
                            "中标价格",
                            "中标价",
                            "投标总价",
                            "含税总价",
                            "总合计",
                        )
                    )
                ),
                -1,
            )
            if name_index >= 0 and len(rows) > 1:
                for cells in rows[1:]:
                    if name_index >= len(cells):
                        continue
                    name = cls._award_winner_name(cells[name_index])
                    price = (
                        cls._award_price_value(headers[price_index], cells[price_index])
                        if price_index >= 0 and price_index < len(cells)
                        else ""
                    )
                    if name and not any(item["中标人名称"] == name for item in result):
                        result.append(
                            {"标段": "", "中标人名称": name, "中标价": price}
                        )
                continue

            name = price = ""
            for cells in rows:
                for index, cell in enumerate(cells):
                    normalized = cell.replace(" ", "")
                    if normalized.rstrip("：:") == "中标人" and index + 1 < len(cells):
                        name = cls._award_winner_name(cells[index + 1])
                    if ("中标价格" in normalized or normalized.rstrip("：:") == "中标价") and index + 1 < len(cells):
                        price = cls._award_price_value(cell, cells[index + 1])
                    if "总合计" in normalized and len(cells) > 1:
                        price = cls._award_price_value(cell, cells[-1])
            if name and not any(item["中标人名称"] == name for item in result):
                result.append({"标段": "", "中标人名称": name, "中标价": price})
        return result

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

    @staticmethod
    def _award_admitted_details(text: str) -> list[dict[str, str]]:
        """解析框架协议/银行服务等只公布入围单位、不公布价格的结果。"""

        match = re.search(
            r"(?ms)^\s*(?:一|1)[、.．]\s*入围单位信息\s*[：:]?\s*(.*?)"
            r"(?=^\s*(?:二|2)[、.．]\s*其他公示内容|\Z)",
            text,
        )
        if not match:
            return []
        result = []
        for line in match.group(1).splitlines():
            value = line.strip(" ：:；;")
            if not value or re.match(r"^\d{3}\s", value):
                continue
            if not re.search(
                r"(?:公司|集团|银行|支行|分行|联合社|信用社|事务所|研究院|设计院)$",
                value,
            ):
                continue
            result.append({"标段": "", "中标人名称": value, "中标价": ""})
        return result

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
