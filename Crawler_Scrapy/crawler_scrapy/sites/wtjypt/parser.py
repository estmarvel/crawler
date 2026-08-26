"""伟拓公开详情JSON到公共公告Schema的映射。"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape
from typing import Any, Mapping
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler_scrapy.sites.bitbid.parser import BitbidParser, clean_html
from crawler_scrapy.sites.sxxindian.parser import SxxindianParser
from crawler_scrapy.sites.wtjypt import config


class WtjyptParser(SxxindianParser):
    @classmethod
    def parse(
        cls, feed: str, payload: Mapping[str, Any], list_record: Mapping[str, Any],
        *, pdf_text: str = "",
    ) -> tuple[str, str, dict[str, Any], list[dict[str, Any]], str, str, str, str]:
        module, category, project_type = feed.split(".", 2)
        title = str(payload.get("noticeName") or payload.get("planName") or list_record.get("noticeName") or list_record.get("projectPlanName") or "").strip()
        publish_time = cls._timestamp(payload.get("publishTime") or payload.get("publishDate") or list_record.get("publishTime") or list_record.get("publishDate"))
        raw_html = str(payload.get("noticeContent") or "")
        if category == "plan" and not raw_html:
            # 伟拓的招标计划详情接口只返回结构化 JSON，没有 noticeContent。
            # 将接口原始业务字段生成稳定、可读的 HTML，确保统一快照管线落盘。
            raw_html = cls._plan_html(payload)
        html_text = clean_html(raw_html) if raw_html else cls._plan_text(payload)
        raw_text = cls._merge_text(html_text, pdf_text)
        source_method = str(payload.get("tenderModeName") or list_record.get("tenderMode") or "").strip()
        detail = {
            "title": title,
            "publish_time": publish_time,
            "module": module,
            "category": category,
            "project_type": project_type,
            "source_method": source_method,
        }

        if category == "plan":
            notice_type = "招标计划"
            data = cls._plan_wt(detail, payload)
        elif category in {"tender", "notice"}:
            if re.search(r"变更|更正|延期|终止|撤销|废标|流标|补充", title):
                notice_type = "更正结果公示"
                data = cls._correction(detail, raw_text)
                cls._merge_correction_api(data, payload, module, title)
            else:
                notice_type = "资格预审公告" if "资格预审" in title else "招标公告"
                data = cls._tender(detail, raw_text, prequalification=notice_type == "资格预审公告")
                cls._merge_tender_api(data, payload, module)
        elif category == "candidate":
            notice_type = "中标候选人公示"
            data = cls._candidate(detail, raw_text, raw_html)
            details = cls._ranked_candidate_details(raw_text)
            if details:
                data["中标候选人名称"] = [x["候选人名称"] for x in details]
                data["中标候选人报价"] = [x["候选人报价"] for x in details]
                data["中标候选人明细"] = details
            cls._merge_result_api(data, payload, candidate=True, module=module)
        else:
            notice_type = "中标结果公示"
            data = cls._award(detail, raw_text, raw_html)
            cls._merge_result_api(data, payload, candidate=False, module=module)

        if category != "plan":
            cls._fill_site_specific_fields(data, raw_text, notice_type)

        data["发布网站"] = config.PLATFORM_NAME
        attachments = cls._attachments(payload)
        return notice_type, source_method, data, attachments, raw_html, raw_text, title, publish_time

    @staticmethod
    def _merge_text(html_text: str, pdf_text: str) -> str:
        """与千极链一致：规则解析和 AI 同时使用 HTML 与可提取的 PDF 正文。"""
        html_text = str(html_text or "").strip()
        pdf_text = str(pdf_text or "").strip()
        if not pdf_text:
            return html_text
        if not html_text:
            return pdf_text
        if pdf_text in html_text:
            return html_text
        return f"{html_text}\n\n【附件PDF正文】\n{pdf_text}"

    @staticmethod
    def _timestamp(value: Any) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = int(value)
            if number > 10_000_000_000:
                number //= 1000
            return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
        return str(value)

    @staticmethod
    def _plan_text(p: Mapping[str, Any]) -> str:
        mapping = (
            ("项目名称", "projectName"), ("招标计划名称", "planName"),
            ("建设地点", "region"), ("预计发布时间", "noticeTime"),
            ("招标人名称", "legalPerson"), ("资金来源", "fundSource"),
            ("项目总投资", "contributionScale"), ("建设内容及规模", "projectOverview"),
            ("招标内容", "tenderContent"), ("项目规模", "projectScale"),
        )
        return "\n".join(f"{label}：{p.get(key)}" for label, key in mapping if p.get(key))

    @classmethod
    def _plan_html(cls, p: Mapping[str, Any]) -> str:
        mapping = (
            ("招标计划名称", "planName"), ("项目名称", "projectName"),
            ("建设地点", "region"), ("预计公告发布时间", "noticeTime"),
            ("招标人名称", "legalPerson"), ("招标人代码", "legalPersonCode"),
            ("资金来源", "fundSource"), ("项目总投资", "contributionScale"),
            ("审批名称", "approvalName"), ("审批文号", "approvalNumber"),
            ("建设内容及规模", "projectOverview"), ("招标内容", "tenderContent"),
            ("项目规模", "projectScale"), ("其他说明", "otherContent"),
        )
        rows = []
        for label, key in mapping:
            value = p.get(key)
            if value not in (None, ""):
                rows.append(
                    f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>"
                )
        return (
            '<article class="wtjypt-plan-snapshot" data-source="findPlanDetail">'
            '<h1>' + escape(str(p.get("planName") or p.get("projectName") or "招标计划")) + '</h1>'
            '<table><tbody>' + "".join(rows) + '</tbody></table></article>'
        )

    @classmethod
    def _plan_wt(cls, d: Mapping[str, Any], p: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "项目性质": "招标信息",
            "招标方式": "",
            "项目名称": p.get("projectName") or p.get("planName") or d["title"],
            "项目类型": "",
            "项目总投资": p.get("contributionScale") or "",
            "招标内容": p.get("tenderContent") or "",
            "招标人名称": p.get("legalPerson") or "",
            "行政监督部门": p.get("approvalName") or "",
            "建设地点": p.get("region") or "",
            "建设内容及规模": cls._join_distinct(p.get("projectOverview"), p.get("projectScale")),
            "招标公告（资格预审公告）预计发布时间": p.get("noticeTime") or "",
            "发布日期": d["publish_time"],
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _merge_tender_api(cls, data: dict[str, Any], p: Mapping[str, Any], module: str) -> None:
        data["项目性质"] = "采购项目" if module == "purchase" else "招标信息"
        data["源站公告性质"] = "采购公告" if module == "purchase" else "招标公告"
        data["项目编号/招标编号"] = p.get("tenderProjectCode") or data.get("项目编号/招标编号")
        data["所属行业"] = p.get("dicIndustriesType") or data.get("所属行业")
        data["项目类型/行业分类"] = p.get("classificationName") or data.get("项目类型/行业分类")
        data["招标方式"] = p.get("tenderModeName") or data.get("招标方式")
        data["开标时间"] = p.get("bidOpenTime") or data.get("开标时间")
        data["开启时间"] = p.get("bidOpenTime") or data.get("开启时间")
        owner_key = "招标人/采购人名称"
        data[owner_key] = p.get("tendereeName") or data.get(owner_key)
        data["招标代理机构"] = p.get("tenderAgencyName") or data.get("招标代理机构")

    @classmethod
    def _merge_result_api(cls, data: dict[str, Any], p: Mapping[str, Any], *, candidate: bool, module: str) -> None:
        data["项目性质"] = "采购项目" if module == "purchase" else "招标信息"
        data["源站公告性质"] = ("成交公示" if candidate else "成交结果") if module == "purchase" else ("评标结果" if candidate else "中标结果")
        number_key = "招标编号/项目编号" if candidate else "依据文号"
        data[number_key] = p.get("tenderProjectCode") or data.get(number_key)
        data["所属行业"] = p.get("dicIndustriesType") or data.get("所属行业")
        data["组织形式"] = "委托招标" if p.get("tenderAgencyName") else data.get("组织形式")
        data["招标方式"] = p.get("tenderModeName") or data.get("招标方式", "")
        data["开标时间"] = p.get("bidOpenTime") or data.get("开标时间")
        data["招标人/采购人"] = p.get("tendereeName") or data.get("招标人/采购人")
        data["招标代理机构"] = p.get("tenderAgencyName") or data.get("招标代理机构")

    @classmethod
    def _merge_correction_api(cls, data: dict[str, Any], p: Mapping[str, Any], module: str, title: str) -> None:
        nature = next((x for x in ("终止公告", "撤销公告", "废标公告", "流标公告", "延期公告", "变更公告", "更正公告", "补充公告") if x[:-2] in title), "采购公告" if module == "purchase" else "招标公告")
        data["公共类型"] = nature
        data["项目名称"] = cls._project_name(title)
        data["所属行业"] = p.get("dicIndustriesType") or data.get("所属行业")
        data["组织形式"] = "委托招标" if p.get("tenderAgencyName") else data.get("组织形式")
        data["开标时间"] = p.get("bidOpenTime") or data.get("开标时间")
        data["依据文号"] = p.get("tenderProjectCode") or data.get("依据文号")
        data["招标人/采购人"] = p.get("tendereeName") or data.get("招标人/采购人", "")
        data["招标代理机构"] = p.get("tenderAgencyName") or data.get("招标代理机构")

    @staticmethod
    def _attachments(p: Mapping[str, Any]) -> list[dict[str, Any]]:
        source = p.get("fjEnclosure") if p.get("fjEnclosure") is not None else p.get("enclosureList")
        if isinstance(source, Mapping):
            source = list(source.values()) if not source.get("enclosurePath") else [source]
        result = []
        for item in source or []:
            if not isinstance(item, Mapping):
                continue
            path = str(item.get("enclosurePath") or "").strip()
            if not path:
                continue
            result.append({
                "file_name": str(item.get("enclosureName") or path.rsplit("/", 1)[-1] or "附件"),
                "file_url": urljoin(config.BASE_URL, path),
                "source_file_id": str(item.get("id") or item.get("enclosureId") or ""),
                "source": "detail_api",
            })
        return result

    @staticmethod
    def _ranked_candidate_details(text: str) -> list[dict[str, str]]:
        pattern = re.compile(
            r"第\s*(\d+)\s*名\s*[：:]\s*([^，,；;\n]+?)\s*[，,]"
            r"[^\n；;]*?(?:投标|响应|成交)?报价(?:\s*[（(][^）)]*[）)])?\s*[：:]\s*"
            r"([^，,；;\n]+)"
        )
        return [
            {"排序": match.group(1), "候选人名称": match.group(2).strip(), "候选人报价": match.group(3).strip()}
            for match in pattern.finditer(text)
        ]

    @staticmethod
    def _join_distinct(*values: Any) -> str:
        result = []
        for value in values:
            cleaned = str(value or "").strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
        return "\n".join(result)

    @classmethod
    def _fill_site_specific_fields(cls, data: dict[str, Any], text: str, notice_type: str) -> None:
        """补齐伟拓正文的固定写法；只按明确标签取值，禁止地址跨主体串位。"""
        if not text:
            return
        normalized = re.sub(r"[ \t\u3000]+", " ", text).strip()

        if notice_type in {"招标公告", "资格预审公告"}:
            deadline = cls._label_value(
                normalized,
                r"(?:投标文件)?递交(?:的)?截止时间|投标截止时间",
            )
            if deadline:
                data["递交截止时间"] = deadline
            open_time = cls._label_value(normalized, r"开标时间")
            if open_time and cls._has_clock(open_time):
                data["开标时间"] = open_time
                data["开启时间"] = open_time
            online_address = cls._label_value(normalized, r"线上递交地址|递交地点")
            if online_address:
                data["开启地点"] = data.get("开启地点") or online_address
            guarantee = cls._guarantee_value(normalized)
            if guarantee:
                data["投标保证金方式"] = guarantee

        if notice_type == "更正结果公示":
            changed_open = cls._last_label_value(normalized, r"开标时间")
            if changed_open:
                data["开标时间"] = changed_open

        if notice_type == "中标结果公示":
            names = cls._all_values(normalized, r"中标(?:单位名称|单位|人名称|人)")
            prices = cls._all_values(normalized, r"中标(?:价格|价|金额)")
            if names:
                data["中标人名称"] = names
            if prices:
                data["中标价"] = prices

        cls._fill_party_block(data, normalized, owner=True)
        cls._fill_party_block(data, normalized, owner=False)

    @staticmethod
    def _has_clock(value: str) -> bool:
        return bool(re.search(r"(?:\d{1,2}\s*[时:]\s*\d{2})", value))

    @staticmethod
    def _clean_value(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip(" ：:；;。")

    @classmethod
    def _label_value(cls, text: str, label: str) -> str:
        match = re.search(
            rf"(?:{label})\s*[：:]\s*([^\n；;]+)",
            text,
            re.I,
        )
        return cls._clean_value(match.group(1)) if match else ""

    @classmethod
    def _last_label_value(cls, text: str, label: str) -> str:
        values = re.findall(rf"{label}\s*[：:]\s*([^\n；;]+)", text, re.I)
        return cls._clean_value(values[-1]) if values else ""

    @classmethod
    def _all_values(cls, text: str, label: str) -> list[str]:
        values = []
        for value in re.findall(rf"{label}\s*[：:]\s*([^\n；;]+)", text, re.I):
            cleaned = cls._clean_value(value)
            if cleaned and cleaned not in values:
                values.append(cleaned)
        return values

    @classmethod
    def _guarantee_value(cls, text: str) -> str:
        match = re.search(
            r"投标保证金(?:的递交)?\s*[：:]?\s*([^\n]{0,100})",
            text,
            re.I,
        )
        if not match:
            return ""
        value = cls._clean_value(match.group(1))
        return value if value else "详见公告正文"

    @classmethod
    def _fill_party_block(cls, data: dict[str, Any], text: str, *, owner: bool) -> None:
        if owner:
            start = r"(?:招\s*标\s*人|招标单位|采购人)"
            stop = r"(?:招标代理机构|招标代理|采购代理机构|采购代理|监督部门|监督单位)"
            name_key, address_key = "招标人/采购人", "招标人地址"
            contact_key, phone_key = "招标人联系人", "招标人联系方式"
            alt_name_key = "招标人/采购人名称"
        else:
            start = r"(?:招标代理机构|招标代理|采购代理机构|采购代理)"
            stop = r"(?:监督部门|监督单位|异议|开户名|$)"
            name_key, address_key = "招标代理机构", "招标代理机构地址"
            contact_key, phone_key = "招标代理机构联系人", "招标代理机构联系方式"
            alt_name_key = ""
        blocks = list(re.finditer(rf"{start}\s*[：:]\s*([^\n]+)(.*?)(?=\n\s*{stop}\s*[：:]|\Z)", text, re.S | re.I))
        if not blocks:
            return
        match = blocks[-1]
        name = cls._clean_value(match.group(1))
        block = match.group(2)
        address = cls._label_value(block, r"地\s*址")
        contact = cls._label_value(block, r"联\s*系\s*人")
        phone = cls._label_value(block, r"(?:联系电话|联系方式|电\s*话)")
        if name:
            data[name_key] = data.get(name_key) or name
            if alt_name_key:
                data[alt_name_key] = data.get(alt_name_key) or name
        if address:
            data[address_key] = data.get(address_key) or address
        if contact:
            data[contact_key] = data.get(contact_key) or contact
        if phone:
            data[phone_key] = data.get(phone_key) or phone
