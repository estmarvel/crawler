"""山西新点HTML公告的站点专用字段解析器。"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from lxml import etree, html as lxml_html

from crawler_scrapy.ai.field_contracts import normalize_project_nature
from crawler_scrapy.sites.bitbid.parser import BitbidParser, clean_html
from crawler_scrapy.sites.qianji.parser import QianjiParser
from crawler_scrapy.sites.qianji.ai_review import (
    extract_identifier_evidence,
    unique_identifiers,
)
from crawler_scrapy.sites.sxxindian import config


_TITLE_SUFFIX_RE = re.compile(
    r"(?:招标计划|资格预审公告|招标公告|采购公告|谈判采购公告|谈判公告|磋商采购公告|磋商公告|"
    r"询比采购公告|询价采购公告|询价公告|变更公告|更正公告|终止公告|终止公示|废标公告|废标公示|"
    r"流标公告|流标公示|中标候选人公示|成交候选人公示|中标结果公示|中标结果公告|结果公示|"
    r"结果公告|成交结果公示|成交结果公告|成交公告|合同公示|征求意见公告)$"
)
_DATE_RE = re.compile(
    r"(20\d{2})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?"
    r"(?:\s*(上午|下午)?\s*(\d{1,2})\s*[:时]\s*(\d{1,2})(?:\s*分|:(\d{1,2}))?)?"
)


class SxxindianParser(QianjiParser):
    """把新点HTML正文映射到公共公告Schema。"""

    @classmethod
    def parse(
        cls,
        feed: str,
        html_text: str,
        list_record: Mapping[str, Any],
    ) -> tuple[str, str, dict[str, Any], list[dict[str, Any]], str, str]:
        document = lxml_html.fromstring(html_text or "<html></html>")
        title_nodes = document.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), ' ewb-info-tt ')]"
        )
        full_title = (
            " ".join(title_nodes[0].text_content().split())
            if title_nodes
            else str(list_record.get("title") or "")
        )
        article_nodes = document.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), ' ewb-article ')]"
        )
        article = article_nodes[0] if article_nodes else None
        if article is not None:
            for node in article.xpath(
                ".//script|.//style|"
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' ewb-article-buttonbox ')]|"
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' ewb-article-nav ')]"
            ):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            raw_html = (article.text or "") + "".join(
                etree.tostring(child, encoding="unicode", method="html")
                for child in article
            )
        else:
            raw_html = ""
        text = clean_html(raw_html)
        publish_time = cls._publish_time(document, list_record)
        title = str(list_record.get("title") or cls._strip_title_tags(full_title)).strip()
        module, category, project_type = feed.split(".", 2)
        source_method = (
            cls.procurement_method(full_title, title, text)
            if module == "purchase" or category == "other"
            else ""
        )
        notice_type = cls._notice_type(module, category, title, text)
        detail = {
            "title": title,
            "full_title": full_title,
            "publish_time": publish_time,
            "module": module,
            "category": category,
            "project_type": project_type,
            "source_method": source_method,
        }

        if notice_type == "招标计划":
            data = cls._plan(detail, text)
        elif notice_type in {"招标公告", "资格预审公告"}:
            data = cls._tender(detail, text, prequalification=notice_type == "资格预审公告")
        elif notice_type == "中标候选人公示":
            data = cls._candidate(detail, text, raw_html)
        elif notice_type == "中标结果公示":
            data = cls._award(detail, text, raw_html)
        elif notice_type == "合同与履约":
            data = cls._contract(detail, text)
        else:
            data = cls._correction(detail, text)
        project_number, tender_number = cls._identifiers(text)
        data["项目编号"] = project_number
        data["招标编号"] = tender_number
        combined = "；".join(
            dict.fromkeys(filter(None, (project_number, tender_number)))
        )
        for field_name in ("项目编号/招标编号", "招标编号/项目编号"):
            if field_name in data:
                data[field_name] = combined
        return notice_type, source_method, data, cls.attachments(article), raw_html, text

    @staticmethod
    def _strip_title_tags(value: str) -> str:
        return re.sub(r"^(?:\s*\[[^\]]+\]\s*)+", "", value).strip()

    @staticmethod
    def _publish_time(document: Any, record: Mapping[str, Any]) -> str:
        for node in document.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), ' ewb-is-content ')]"
        ):
            text = " ".join(node.text_content().split())
            match = re.search(r"发布时间\s*[：:]\s*(.+)", text)
            if match:
                return match.group(1).strip()
        return str(record.get("date") or "").strip()

    @staticmethod
    def _notice_type(module: str, category: str, title: str, text: str = "") -> str:
        if module == "bidding":
            # “其他公告”并不是一种公告类型，历史数据中混有招标、询比、结果、流标等公告，
            # 必须先按详情标题判型，不能全部套用更正公告字段。
            if category == "other":
                source = f"{title}\n{text[:600]}"
                if re.search(r"变更|更正|补充|终止|废标|流标|撤销|延期", source):
                    return "更正结果公示"
                if re.search(r"资格预审", source):
                    return "资格预审公告"
                if re.search(r"(?:中标|成交).*候选人|候选人公示", source):
                    return "中标候选人公示"
                if re.search(r"(?:中标|成交).*(?:结果|公告|公示)|结果公示", source):
                    return "中标结果公示"
                if re.search(r"合同公示|合同公告", source):
                    return "合同与履约"
                if re.search(r"招标|采购|询比|谈判|磋商|询价|竞价", source):
                    return "招标公告"
                return "更正结果公示"
            fixed = {
                "plan": "招标计划",
                "tender": "招标公告",
                "prequalification": "资格预审公告",
                "change": "更正结果公示",
                "candidate": "中标候选人公示",
                "award": "中标结果公示",
            }
            if category in fixed:
                return fixed[category]
            if re.search(r"候选人", title):
                return "中标候选人公示"
            if re.search(r"中标|成交.*(?:结果|公告)", title):
                return "中标结果公示"
            return "更正结果公示"
        return {
            "notice": "招标公告",
            "tender": "招标公告",
            "change": "更正结果公示",
            "award": "中标结果公示",
            "contract": "合同与履约",
            "opinion": "招标公告",
        }[category]

    @staticmethod
    def procurement_method(full_title: str, title: str, text: str) -> str:
        tags = re.findall(r"\[([^\]]+)\]", full_title)
        ignored = {"进行中", "已结束", "已终止", "工程", "货物", "服务", "施工", "材料设备", "其他项目"}
        for tag in tags:
            if tag not in ignored and not tag.startswith("山西省"):
                return tag
        source = f"{title}\n{text[:500]}"
        for keyword in (
            "公开招标", "询比采购", "谈判采购", "竞争性谈判", "磋商采购",
            "竞争性磋商", "单一来源", "询价采购", "询价", "框架协议",
        ):
            if keyword in source:
                return keyword
        return "其他"

    @classmethod
    def _plan(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        return {
            "项目性质": cls._project_nature(text),
            "招标方式": cls._label_fuzzy(text, "招标方式"),
            "项目名称": cls._label_fuzzy(text, "项目名称") or cls._project_name(d["title"]),
            "项目类型": cls._label_fuzzy(text, "项目类型"),
            "项目总投资": cls._label_fuzzy(text, "项目总投资", "投资估算"),
            "招标内容": cls._label_fuzzy(text, "招标内容"),
            "招标人名称": cls._label_fuzzy(text, "招标人名称", "招标人"),
            "行政监督部门": cls._label_fuzzy(text, "行政监督部门"),
            "建设地点": cls._label_fuzzy(text, "建设地点"),
            "建设内容及规模": cls._section(text, ("建设内容及规模",), ("建设地点", "项目总投资")) or cls._label_fuzzy(text, "建设内容及规模"),
            "招标公告（资格预审公告）预计发布时间": cls._label_fuzzy(text, "招标公告（资格预审公告）预计发布时间", "预计发布时间"),
            "发布日期": d["publish_time"],
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _tender(cls, d: Mapping[str, Any], text: str, *, prequalification: bool) -> dict[str, Any]:
        contacts = cls._contacts_xindian(text)
        project_type = cls._project_type_name(d)
        source_nature = "资格预审公告" if prequalification else (
            config.PURCHASE_CATEGORIES[d["category"]][1] if d["module"] == "purchase" else config.BIDDING_CATEGORIES[d["category"]][1]
        )
        owner_key = "招标人/采购人名称"
        scope_key = "项目概况与招标范围" if prequalification else "招标内容与范围"
        data = {
            "项目性质": cls._project_nature(text),
            "源站公告性质": source_nature,
            "项目名称": cls._label_fuzzy(text, "采购项目名称", "项目名称") or cls._project_name(d["title"]),
            "所属行业": cls._label_fuzzy(text, "所属行业", "行业分类"),
            "组织形式": "委托招标" if contacts["agency"].get("name") else "",
            "招标方式": d.get("source_method", "") or cls._label_fuzzy(text, "招标方式", "采购方式"),
            "开标时间": cls._datetime(cls._last_label(text, "开标时间", "开启时间")),
            "项目编号/招标编号": cls._number(text),
            "项目类型/行业分类": project_type,
            "项目总投资/估算金额": cls._label_fuzzy(text, "项目总投资", "估算金额", "投资估算"),
            "招标金额": cls._unambiguous_tender_amount(text),
            "资金来源": cls._funding(text),
            "项目地点": cls._label_fuzzy(
                text, "招标项目所在地区", "项目所在地区", "项目所在地", "项目地点", "建设地点",
                "服务地点", "交货地点", "实施地点", "履约地点"
            ),
            owner_key: contacts["owner"].get("name", "") or cls._sentence_owner(text),
            "项目规模": cls._label_fuzzy(text, "项目规模", "采购项目概况"),
            "工期/服务期/供货日期": cls._label_fuzzy(
                text, "计划工期", "合同履行期限", "服务期限", "服务期", "服务周期", "工期",
                "交货期", "供货期", "交付时间", "完成期限"
            ),
            "质量要求": cls._label_fuzzy(text, "质量要求", "质量标准", "质量标准或服务标准", "服务标准"),
            scope_key: cls._section(
                text,
                ("项目概况与招标范围", "项目概况和招标范围", "采购范围及相关要求", "招标内容与范围", "招标范围", "采购范围"),
                ("投标人资格要求", "供应商资格要求", "申请人资格要求", "报价人资格", "响应人资格", "招标文件的获取", "采购文件的获取"),
            ),
            "申请人资格要求/投标人资格要求": cls._section(
                text,
                ("投标人资格要求", "供应商资格要求", "申请人资格要求", "报价人资格", "响应人资格"),
                (
                    "招标文件的获取", "采购文件的获取", "询比采购文件的获取",
                    "谈判采购文件的获取", "磋商采购文件的获取", "询价采购文件的获取",
                    "资格预审文件的获取", "投标文件的递交", "响应文件的递交",
                    "报价文件的递交",
                ),
            ),
            "预审文件获取时间": cls._file_time(text),
            "获取方式": cls._last_label(text, "获取方式", "获取方法", "获取地点"),
            "递交截止时间": cls._submission_deadline(text),
            "递交方法": cls._submission_method(text),
            "开启时间": cls._opening_time(text),
            "开启方式": cls._last_label(text, "开标方式", "开启方式", "文件开启方式"),
            "开启地点": cls._last_label(text, "开标地点", "开标地址", "开启地点", "开启地址"),
            "评审办法": cls._label_fuzzy(text, "评标办法", "评审办法"),
            "投标保证金方式": cls._guarantee(text),
            **cls._contact_fields_xindian(contacts, award=False),
            "发布日期": d["publish_time"],
            "发布网站": config.PLATFORM_NAME,
        }
        return data

    @classmethod
    def _candidate(cls, d: Mapping[str, Any], text: str, raw_html: str) -> dict[str, Any]:
        contacts = cls._contacts_xindian(text)
        details = cls._candidate_table(raw_html) or BitbidParser._candidate_details(text)
        return {
            "项目性质": cls._project_nature(text),
            "源站公告性质": "中标候选人公示",
            "项目名称": cls._project_name(d["title"]),
            "所属行业": cls._label_fuzzy(text, "所属行业", "行业分类"),
            "组织形式": "委托招标" if contacts["agency"].get("name") else "",
            "开标时间": cls._datetime(cls._last_label(text, "开标时间")),
            "公示时间": cls._publicity_time(text),
            "招标编号/项目编号": cls._number(text),
            "中标候选人名称": [x["候选人名称"] for x in details],
            "中标候选人报价": [x["候选人报价"] for x in details],
            "中标候选人明细": details,
            **cls._contact_fields_xindian(contacts, award=True),
            "发布日期": d["publish_time"],
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _award(cls, d: Mapping[str, Any], text: str, raw_html: str) -> dict[str, Any]:
        contacts = cls._contacts_xindian(text)
        details = cls._award_table(raw_html) or cls._award_text(text)
        return {
            "项目性质": cls._project_nature(text),
            "源站公告性质": config.PURCHASE_CATEGORIES[d["category"]][1] if d["module"] == "purchase" else "结果公告",
            "项目名称": cls._project_name(d["title"]),
            "所属行业": cls._label_fuzzy(text, "所属行业", "行业分类"),
            "组织形式": "委托招标" if contacts["agency"].get("name") else "",
            "招标方式": d.get("source_method", ""),
            "中标人名称": [x["中标人名称"] for x in details],
            "联合体成员": cls._list_label(text, "联合体成员"),
            "中标价": [x["中标价"] for x in details],
            "中标结果明细": details,
            "工期": cls._label_fuzzy(text, "计划工期", "合同履行期限", "工期", "服务期", "交货期"),
            "项目经理": cls._label_fuzzy(text, "项目经理", "项目负责人"),
            "项目经理证书名称": cls._label_fuzzy(text, "证书名称"),
            "项目经理证书编号": cls._label_fuzzy(text, "证书编号"),
            **cls._contact_fields_xindian(contacts, award=True),
            "依据文件": cls._label_fuzzy(text, "依据文件"),
            "依据文号": cls._label_fuzzy(text, "依据文号", "批准文号"),
            "发布日期": d["publish_time"],
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _correction(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        contacts = cls._contacts_xindian(text)
        return {
            "公共类型": config.PURCHASE_CATEGORIES[d["category"]][1] if d["module"] == "purchase" else config.BIDDING_CATEGORIES[d["category"]][1],
            "项目名称": cls._project_name(d["title"]),
            "所属行业": cls._label_fuzzy(text, "所属行业", "行业分类"),
            "组织形式": "委托招标" if contacts["agency"].get("name") else "",
            "开标时间": cls._datetime(cls._last_label(text, "开标时间")),
            "标书发售时间": cls._file_time(text),
            "公告内容": text,
            **cls._contact_fields_xindian(contacts, award=True),
            "监督部门地址": "",
            "监督部门联系人": "",
            "监督部门联系方式": cls._label_fuzzy(text, "监督电话"),
            "依据文件": cls._label_fuzzy(text, "依据文件"),
            "依据文号": cls._label_fuzzy(text, "依据文号", "批准文号"),
            "发布日期": d["publish_time"],
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _contract(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        return {
            "项目名称": cls._label_fuzzy(text, "项目名称") or cls._project_name(d["title"]),
            "项目编号": cls._number(text),
            "合同名称": cls._label_fuzzy(text, "合同名称"),
            "招标人名称": cls._label_fuzzy(text, "甲方", "采购人", "招标人"),
            "中标人名称": cls._label_fuzzy(text, "乙方", "供应商", "中标人"),
            "合同金额": cls._label_fuzzy(text, "合同金额", "合同价款"),
            "合同期限": cls._label_fuzzy(text, "合同期限", "履约期限"),
            "合同签署时间": cls._label_fuzzy(text, "合同签署时间", "签订时间"),
            "合同主要内容": cls._section(text, ("合同主要内容",), ("联系方式",)) or text,
            "发布日期": d["publish_time"],
            "发布网站": config.PLATFORM_NAME,
        }

    @staticmethod
    def _project_name(title: str) -> str:
        value = re.sub(r"\s+", "", str(title or "")).replace("[变更公告]", "")
        return _TITLE_SUFFIX_RE.sub("", value).strip(" -—_（）()")

    @staticmethod
    def _project_type_name(d: Mapping[str, Any]) -> str:
        value = str(d.get("project_type") or "")
        return config.PROJECT_TYPES.get(value, ("", ""))[1] if value != "all" else ""

    @classmethod
    def _project_nature(cls, text: str) -> str:
        return normalize_project_nature(
            cls._label_fuzzy(text, "项目性质", "发布类型")
        )

    @staticmethod
    def _identifiers(text: str) -> tuple[str, str]:
        """先按原文标签区分两个编号，再生成兼容组合字段。"""

        evidence = extract_identifier_evidence(text)
        project_values = unique_identifiers(
            item.value
            for item in evidence
            if item.label
            in {
                "招标项目编号",
                "采购项目编号",
                "投资项目统一代码",
                "项目代码",
                "项目编号",
            }
        )
        tender_values = unique_identifiers(
            item.value
            for item in evidence
            if item.label in {"招标编号", "采购编号", "代理编号"}
        )
        return (
            next(iter(project_values), ""),
            next(iter(tender_values), ""),
        )

    @classmethod
    def _number(cls, text: str) -> str:
        """兼容本站同时使用采购编号、项目编号、招标编号和项目代码。"""

        project_number, tender_number = cls._identifiers(text)
        return project_number or tender_number

    @classmethod
    def _submission_deadline(cls, text: str) -> str:
        value = cls._last_label(
            text,
            "递交截止时间",
            "投标截止时间",
            "响应文件递交的截止时间",
            "响应文件递交截止时间",
            "响应文件提交截止时间",
            "报价截止时间",
        )
        if not value:
            matches = list(re.finditer(
                r"(?:递交截止时间|投标截止时间|响应文件递交的截止时间|响应文件递交截止时间|响应文件提交截止时间|报价截止时间)\s*为\s*([^；;\n]+)",
                text,
            ))
            value = matches[-1].group(1) if matches else ""
        return cls._datetime(value)

    @classmethod
    def _opening_time(cls, text: str) -> str:
        value = cls._last_label(text, "开标时间", "开启时间", "文件开启时间")
        if any(x in value for x in ("同递交截止时间", "同响应文件递交截止时间", "同投标文件递交截止时间", "同投标截止时间")):
            return cls._submission_deadline(text)
        return cls._datetime(value)

    @classmethod
    def _publicity_time(cls, text: str) -> str:
        value = super()._publicity_time(text)
        if value:
            return value
        # 本站候选人公告把公示区间标成“公告开始/结束时间”。
        start = cls._datetime(cls._last_label(text, "公告开始时间"))
        end = cls._datetime(cls._last_label(text, "公告结束时间"))
        if start and end:
            return f"{start} 至 {end}"
        return start or end

    @classmethod
    def _submission_method(cls, text: str) -> str:
        """只保留递交通道/动作，剔除其后的到场及逾期提示。"""
        value = cls._last_label(text, "递交方法", "递交方式", "提交方式")
        if not value:
            return ""
        value = re.split(
            r"(?:届时|逾期|未按(?:上述|规定)|否则|不予受理|采购人将拒绝)",
            value,
            maxsplit=1,
        )[0]
        return value.strip(" ，,。；;：:")

    @classmethod
    def _label_fuzzy(cls, text: str, *labels: str) -> str:
        value = cls._last_label(text, *labels)
        if value:
            return value
        return cls._fuzzy_label(text, *labels)

    @staticmethod
    def _last_label(text: str, *labels: str) -> str:
        found: list[tuple[int, str]] = []
        for label in labels:
            pattern = rf"{re.escape(label)}\s*[：:]\s*(?:\n\s*)?([^\n；;]+)"
            found.extend((m.start(), m.group(1).strip(" ：:;；")) for m in re.finditer(pattern, text))
        return max(found, default=(-1, ""), key=lambda x: x[0])[1]

    @classmethod
    def _unambiguous_tender_amount(cls, text: str) -> str:
        """只返回唯一明确的项目/标段金额。

        新点多标段公告会重复出现“招标金额”。通用的“取最后一个”
        会把最后一个标段价误当成整个项目金额。未明示项目总额时宁可
        留空，各标段金额仍保留在“招标内容与范围”和原始正文中。
        """

        labels = (
            "招标金额",
            "最高限价",
            "最高投标限价",
            "预算金额",
            "采购预算",
            "预算价",
            "控制价",
        )
        found: list[tuple[int, str]] = []
        for label in labels:
            pattern = rf"{re.escape(label)}\s*[：:]\s*(?:\n\s*)?([^\n；;]+)"
            found.extend(
                (match.start(), match.group(1).strip(" ：:;；"))
                for match in re.finditer(pattern, text)
                if match.group(1).strip(" ：:;；")
            )
        values = [value for _, value in sorted(found)]
        distinct = list(dict.fromkeys(values))
        return distinct[0] if len(distinct) == 1 else ""

    @staticmethod
    def _datetime(value: str) -> str:
        match = _DATE_RE.search(str(value or ""))
        if not match:
            return ""
        year, month, day, meridiem, hour, minute, second = match.groups()
        if hour is None:
            return f"{year}-{int(month):02d}-{int(day):02d}"
        h = int(hour)
        if meridiem == "下午" and h < 12:
            h += 12
        if meridiem == "上午" and h == 12:
            h = 0
        return f"{year}-{int(month):02d}-{int(day):02d} {h:02d}:{int(minute):02d}:{int(second or 0):02d}"

    @classmethod
    def _file_time(cls, text: str) -> str:
        for label in (
            "获取招标文件时间", "招标文件获取时间", "采购文件获取时间", "获取采购文件时间",
            "获取时间", "资格预审文件的获取", "采购文件的获取", "报名及获取采购文件时间"
        ):
            pos = text.rfind(label)
            if pos >= 0:
                block = text[pos:pos + 800]
                boundaries = [
                    block.find(x, len(label))
                    for x in ("投标文件的递交", "响应文件的递交", "开启时间及地点", "开标时间及地点")
                ]
                valid_boundaries = [x for x in boundaries if x > 0]
                if valid_boundaries:
                    block = block[:min(valid_boundaries)]
                values = [cls._datetime(m.group(0)) for m in _DATE_RE.finditer(block)]
                values = [x for x in values if x]
                if values:
                    return " 至 ".join(values[:2])
        return cls._last_label(text, "获取时间")

    @staticmethod
    def _funding(text: str) -> str:
        match = re.search(r"资金来源(?:为|[：:])\s*([^，,。；;\n]+)", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _sentence_owner(text: str) -> str:
        matches = list(re.finditer(r"(?:招标人|采购人)为\s*([^，。；;\n]+)", text))
        return matches[-1].group(1).strip() if matches else ""

    @classmethod
    def _contacts_xindian(cls, text: str) -> dict[str, dict[str, str]]:
        result = {"owner": {}, "agency": {}}
        contact_headings = list(re.finditer(
            r"(?m)^\s*(?:(?:[一二三四五六七八九十]+|\d+)[、.．]\s*)?"
            r"(?:采购机构)?联系方式\s*[：:]?\s*$",
            text,
        ))
        contacts_pos = contact_headings[-1].start() if contact_headings else 0
        tail = text[contacts_pos:]
        owner_label = r"(?:招标人|招标单位|采购人|采购单位|建设单位)"
        agency_label = r"(?:招标代理机构|招标代理|采购代理机构|代理机构)"
        invalid_markers = ("主要负责人", "盖章", "签字", "____", "（章）", "(章)")

        def clean(value: str) -> str:
            value = " ".join(str(value or "").split()).strip(" ：:，,。；;")
            return re.sub(
                r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value
            )

        def role_name(role: str, opposite: str) -> str:
            pattern = rf"{role}\s*[：:]\s*(.*?)(?=\s*{opposite}\s*[：:]|\n|\Z)"
            preferred = list(re.finditer(pattern, tail, re.S))
            candidates = preferred or list(re.finditer(pattern, text, re.S))
            for matched in reversed(candidates):
                value = re.split(
                    r"\s*[（(]?\s*(?:联系电话|电话|联系方式)\s*[：:]",
                    clean(matched.group(1)),
                    maxsplit=1,
                )[0].rstrip("（(").strip()
                if value and not any(marker in value for marker in invalid_markers):
                    return value
            return ""

        owner_name = role_name(owner_label, agency_label)
        agency_name = role_name(agency_label, owner_label)
        all_labels = (
            r"招标人|招标单位|采购人|采购单位|建设单位|招标代理机构|招标代理|采购代理机构|"
            r"代理机构|联系地址|地址|项目负责人|联系人|负责人|联系电话|"
            r"联系方式|移动电话|办公电话|电话/传真|电话|电子邮箱|邮箱|邮编"
        )

        def labelled_values(labels: str) -> list[str]:
            values: list[str] = []
            for line in tail.splitlines():
                if "招标人或其招标代理机构主要负责人" in line:
                    continue
                matches = list(re.finditer(rf"(?:{labels})\s*[：:]", line))
                boundaries = list(re.finditer(rf"(?:{all_labels})\s*[：:]", line))
                for matched in matches:
                    end = len(line)
                    for boundary in boundaries:
                        if boundary.start() >= matched.end():
                            end = boundary.start()
                            break
                    value = clean(line[matched.end():end])
                    if (
                        value
                        and not any(marker in value for marker in invalid_markers)
                        and value not in values
                    ):
                        values.append(value)
            return values

        def distribute(values: list[str]) -> tuple[str, str]:
            if not values:
                return "", ""
            if owner_name and agency_name:
                return values[0], "；".join(values[1:])
            if agency_name and not owner_name:
                return "", "；".join(values)
            return "；".join(values), ""

        owner_address, agency_address = distribute(
            labelled_values(r"联系地址|地址")
        )
        owner_contact, agency_contact = distribute(
            labelled_values(r"项目负责人|联系人|负责人")
        )
        owner_phone, agency_phone = distribute(
            labelled_values(r"联系电话|联系方式|移动电话|办公电话|电话/传真|电话")
        )
        result["owner"] = {
            "name": owner_name or cls._sentence_owner(text),
            "address": owner_address,
            "contact": owner_contact,
            "phone": owner_phone,
        }
        result["agency"] = {
            "name": agency_name,
            "address": agency_address,
            "contact": agency_contact,
            "phone": agency_phone,
        }
        return result

    @classmethod
    def _contact_block(cls, block: str, name: str) -> dict[str, str]:
        return {
            "name": name,
            "address": cls._last_label(block, "联系地址", "地址", "地点"),
            "contact": cls._last_label(block, "联系人", "负责人"),
            "phone": cls._last_label(block, "联系电话", "联系方式", "移动电话", "办公电话", "电话/传真", "电话"),
        }

    @staticmethod
    def _contact_fields_xindian(contacts: Mapping[str, Mapping[str, str]], *, award: bool) -> dict[str, str]:
        owner, agency = contacts.get("owner", {}), contacts.get("agency", {})
        return {
            "招标人/采购人" if award else "招标人/采购人名称": owner.get("name", ""),
            "招标人地址": owner.get("address", ""),
            "招标人联系人": owner.get("contact", ""),
            "招标人联系方式": owner.get("phone", ""),
            "招标代理机构": agency.get("name", ""),
            "招标代理机构地址": agency.get("address", ""),
            "招标代理机构联系人": agency.get("contact", ""),
            "招标代理机构联系方式": agency.get("phone", ""),
        }

    @classmethod
    def _candidate_table(cls, raw_html: str) -> list[dict[str, str]]:
        result = []
        for rows in cls._tables(raw_html):
            if not rows:
                continue
            header = rows[0]
            ni = next((i for i, x in enumerate(header) if "候选人名称" in x), -1)
            pi = next((i for i, x in enumerate(header) if any(k in x for k in ("报价", "价格", "费率"))), -1)
            if ni < 0:
                continue
            for row in rows[1:]:
                if ni >= len(row):
                    continue
                name = row[ni].strip()
                price = row[pi].strip() if 0 <= pi < len(row) else ""
                if name and name not in {x["候选人名称"] for x in result}:
                    result.append({"标段": "", "候选人名称": name, "候选人报价": price})
            if result:
                return result
        return result

    @classmethod
    def _award_table(cls, raw_html: str) -> list[dict[str, str]]:
        result = []
        for rows in cls._tables(raw_html):
            if not rows:
                continue
            # 部分 Word HTML 使用单行“标签、值、标签、值”表格，不存在
            # 独立表头行。先按键值对识别，避免把章节标题当成中标人。
            for row in rows:
                for index, cell in enumerate(row[:-1]):
                    label = re.sub(r"\s+", "", cell).strip("：:")
                    if label not in {
                        "中标人", "中标单位", "成交人", "成交供应商", "供应商名称"
                    }:
                        continue
                    name = cls._clean_entity_name(row[index + 1])
                    if not cls._valid_entity_name(name):
                        continue
                    price = ""
                    for price_index, price_label in enumerate(row[:-1]):
                        compact_label = re.sub(r"\s+", "", price_label)
                        if any(
                            marker in compact_label
                            for marker in ("中标价", "成交价", "成交报价", "报价", "价格")
                        ):
                            price = row[price_index + 1].strip()
                            break
                    result.append(
                        {"标段": "", "中标人名称": name, "中标价": price}
                    )
            if result:
                return result
            header = rows[0]
            ni = next((i for i, x in enumerate(header) if any(k in x for k in ("中标人", "成交供应商", "供应商名称"))), -1)
            pi = next((i for i, x in enumerate(header) if any(k in x for k in ("中标价", "成交报价", "报价", "价格", "费率", "优惠"))), -1)
            if ni < 0:
                continue
            for row in rows[1:]:
                if ni >= len(row):
                    continue
                name = cls._clean_entity_name(row[ni])
                price = row[pi].strip() if 0 <= pi < len(row) else ""
                if cls._valid_entity_name(name) and name not in {x["中标人名称"] for x in result}:
                    result.append({"标段": "", "中标人名称": name, "中标价": price})
            if result:
                return result
        return result

    @staticmethod
    def _clean_entity_name(value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return re.sub(
            r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])",
            "",
            text,
        )

    @staticmethod
    def _valid_entity_name(value: str) -> bool:
        text = str(value or "").strip()
        if not text or len(text) > 240:
            return False
        return not bool(
            re.fullmatch(r"(?:[一二三四五六七八九十]+[、.]?)?评审情况", text)
            or re.search(r"_{3,}|签章|盖章|签名", text)
        )

    @classmethod
    def _award_text(cls, text: str) -> list[dict[str, str]]:
        rows = BitbidParser._award_details(text)
        if rows:
            cleaned = []
            for row in rows:
                name = cls._clean_entity_name(row.get("中标人名称", ""))
                if not cls._valid_entity_name(name):
                    continue
                cleaned.append({**row, "中标人名称": name})
            if cleaned:
                return cleaned
        name = cls._label_fuzzy(text, "中标人", "成交供应商", "成交人", "供应商名称")
        price = cls._label_fuzzy(text, "中标价格", "中标价", "成交报价", "成交价")
        name = cls._clean_entity_name(name)
        return (
            [{"标段": "", "中标人名称": name, "中标价": price}]
            if cls._valid_entity_name(name)
            else []
        )

    @classmethod
    def _guarantee(cls, text: str) -> str:
        value = cls._label_fuzzy(text, "投标保证金方式", "保证金递交方式")
        if value:
            return value
        return cls._section(text, ("提交投标保证金的形式",), ("提出异议", "其他公告内容", "监督部门"))

    @staticmethod
    def attachments(article: Any | None) -> list[dict[str, Any]]:
        if article is None:
            return []
        result, seen = [], set()
        for anchor in article.xpath(".//a[@href]"):
            href = str(anchor.get("href") or "").strip()
            parsed = urlparse(href)
            path = parsed.path.lower()
            if not href or href.startswith(("javascript:", "mailto:", "#")):
                continue
            if not (re.search(r"\.(?:pdf|docx?|xlsx?|pptx?|wps|ofd|zip|rar|7z)(?:$|\?)", href, re.I) or "/upload" in path or "filestorage" in path):
                continue
            url = urljoin(config.BASE_URL, href)
            if url in seen:
                continue
            name = " ".join(anchor.text_content().split()) or PurePosixPath(parsed.path).name or "附件"
            result.append({"file_name": name, "file_url": url, "source": "detail_attachment"})
            seen.add(url)
        return result
