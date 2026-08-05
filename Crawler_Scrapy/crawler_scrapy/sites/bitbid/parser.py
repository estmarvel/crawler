"""比比网四类招采公告的站点专用字段解析。"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Mapping

from crawler_scrapy.sites.bitbid import config


_SPACE_RE = re.compile(r"[\t\r\f\v \u3000]+")
_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"(?i)<(?:br\s*/?|/p|/div|/tr|/li|/h[1-6])\s*>")


def clean_html(value: Any) -> str:
    source = str(value or "")
    source = re.sub(r"(?is)<(?:script|style).*?>.*?</(?:script|style)>", "", source)
    source = _BREAK_RE.sub("\n", source)
    source = re.sub(r"(?i)</t[dh]\s*>", "\t", source)
    source = _TAG_RE.sub("", source)
    source = html.unescape(source).replace("\xa0", " ")
    lines = [_SPACE_RE.sub(" ", line).strip(" \t") for line in source.splitlines()]
    return _normalize_labels("\n".join(line for line in lines if line).strip())


def _normalize_labels(value: str) -> str:
    """合并公告模板为排版而拆开的中文标签，如“联 系 人”。"""

    result = value
    for compact in (
        "招标人", "地址", "联系人", "联系电话", "电话", "电子邮件",
        "项目名称", "项目负责人", "招标代理机构",
    ):
        spaced = r"\s*".join(map(re.escape, compact))
        result = re.sub(spaced, compact, result)
    return result


class BitbidParser:
    """把比比网接口字段、HTML正文和PDF文字统一映射到公共Schema。"""

    @classmethod
    def parse(
        cls,
        category: str,
        payload: Mapping[str, Any],
        *,
        pdf_text: str = "",
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]], str, str]:
        detail = cls._detail_object(category, payload)
        raw_html = cls.raw_html(category, detail)
        html_text = clean_html(raw_html)
        text = cls._merge_text(html_text, pdf_text)

        if category == "plan":
            data = cls._plan(detail, text)
        elif category == "tender":
            data = cls._tender(detail, payload, text)
        elif category == "candidate":
            data = cls._candidate(detail, text)
        elif category == "award":
            data = cls._award(detail, text)
        else:
            raise ValueError(f"不支持的比比网栏目：{category}")
        xm_info = payload.get("xmInfo") or {}
        if not isinstance(xm_info, Mapping):
            xm_info = {}
        data["项目编号"] = cls._label(
            text,
            "招标项目编号",
            "项目编号",
            "投资项目统一代码",
            "项目代码",
        ) or cls._value(
            xm_info,
            "projectCode",
            "xiangMuBianHao",
            "projectNo",
        )
        data["招标编号"] = cls._label(
            text,
            "招标编号",
            "采购编号",
            "代理编号",
        ) or cls._value(
            detail,
            "codeByPlatform",
            "gongGaoBianHao",
            "winCandidateBulletinCode",
        )
        return config.CATEGORIES[category]["label"], data, cls.attachments(category, detail), raw_html, text

    @staticmethod
    def _detail_object(category: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        key = {
            "plan": "zbjhInfo",
            "tender": "ggInfo",
            "candidate": "hxrInfo",
            "award": "zbjgInfo",
        }[category]
        value = payload.get(key) or {}
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _merge_text(html_text: str, pdf_text: str) -> str:
        pdf = _normalize_labels(str(pdf_text or "").strip())
        if not pdf:
            return html_text
        if not html_text:
            return pdf
        return f"{html_text}\n\n--- PDF文字层 ---\n{pdf}"

    @classmethod
    def _plan(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        return {
            "项目性质": cls._value(d, "nameApproval") or "自主发布",
            "招标方式": cls._value(d, "capital"),
            "项目名称": cls._value(d, "name", "title"),
            "项目类型": cls._value(d, "planProjectOverview"),
            "项目总投资": cls._value(d, "investEstimation"),
            "招标内容": cls._value(d, "planProjectBidSco"),
            "招标人名称": cls._value(d, "legalPerson", "cityName"),
            "行政监督部门": cls._value(d, "approvalNumber"),
            "建设地点": cls._value(d, "provinceName"),
            "建设内容及规模": cls._value(d, "scale"),
            "招标公告（资格预审公告）预计发布时间": cls._value(d, "remark"),
            "发布日期": cls._value(d, "fabuTime", "planFabuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _tender(
        cls, d: Mapping[str, Any], payload: Mapping[str, Any], text: str
    ) -> dict[str, Any]:
        xm = payload.get("xmInfo") or {}
        if not isinstance(xm, Mapping):
            xm = {}
        contacts = cls._contacts(text)
        return {
            "项目性质": "招标信息",
            "源站公告性质": "招标公告",
            "项目名称": cls._value(xm, "faBaoMingCheng") or cls._project_name(d, text),
            "所属行业": cls._value(d, "suoShuHangYe") or cls._label(text, "所属行业"),
            "组织形式": cls._organization(d),
            "开标时间": cls._label(text, "开标时间", "开启时间") or cls._value(d, "submitDocEndTime"),
            "项目编号/招标编号": cls._value(d, "gongGaoBianHao", "codeByPlatform") or cls._number(text),
            "项目类型/行业分类": cls._value(xm, "xiangMuLeiXing", "publicResourcesClassification"),
            "项目总投资/估算金额": cls._label(text, "项目总投资", "估算金额", "投资估算"),
            "招标金额": cls._label(text, "招标金额", "最高投标限价", "预算金额"),
            "资金来源": cls._value(xm, "ziJinLaiYuan") or cls._label(text, "资金来源"),
            "项目地点": cls._label(text, "服务地点", "交货地点", "项目地点", "建设地点") or cls._region(xm),
            "招标人/采购人名称": cls._value(d, "zhaoBiaoRenName") or contacts["owner"].get("name", ""),
            "项目规模": cls._label(text, "项目规模"),
            "工期/服务期/供货日期": cls._label(text, "服务期限", "服务期", "工期", "交货期限", "供货期"),
            "质量要求": cls._label(text, "质量要求", "质量标准"),
            "招标内容与范围": cls._section(text, ("招标内容与范围", "招标范围"), ("投标人资格要求", "申请人资格要求", "招标文件的获取")),
            "申请人资格要求/投标人资格要求": cls._section(text, ("投标人资格要求", "申请人资格要求"), ("招标文件的获取", "投标文件的递交")),
            "预审文件获取时间": cls._range(cls._value(d, "bidDocStartTime", "preDocStartTime"), cls._value(d, "bidDocEndTime", "preDocEndTime")) or cls._label(text, "获取时间"),
            "获取方式": cls._label(text, "获取方式"),
            "递交截止时间": cls._value(d, "submitDocEndTime") or cls._label(text, "递交截止时间"),
            "递交方法": cls._value(d, "submitDocMethod") or cls._label(text, "递交方法", "递交方式"),
            "开启时间": cls._label(text, "开标时间", "开启时间") or cls._value(d, "submitDocEndTime"),
            "开启方式": cls._label(text, "开标方式", "开启方式"),
            "开启地点": cls._label(text, "开标地点", "开启地点") or cls._online_place(text),
            "评审办法": cls._value(d, "evaluationMethod") or cls._label(text, "评标办法", "评审办法"),
            "投标保证金方式": cls._label(text, "投标保证金方式", "保证金递交方式"),
            **cls._contact_fields(contacts, award=False),
            "发布日期": cls._value(d, "gongGaoFaBuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _candidate(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        contacts = cls._contacts(text)
        details = cls._candidate_details(text)
        return {
            "项目性质": "招标信息",
            "源站公告性质": "中标候选人公示",
            "项目名称": cls._project_name(d, text),
            "所属行业": cls._label(text, "所属行业"),
            "组织形式": cls._label(text, "组织形式"),
            "开标时间": cls._label(text, "开标时间"),
            "公示时间": cls._range(cls._value(d, "startTime"), cls._value(d, "endTime")) or cls._publicity_time(text),
            "招标编号/项目编号": cls._value(d, "gongGaoBianHao", "winCandidateBulletinCode") or cls._number(text),
            "中标候选人名称": [row["候选人名称"] for row in details],
            "中标候选人报价": [row["候选人报价"] for row in details],
            "中标候选人明细": details,
            **cls._contact_fields(contacts, award=True),
            "发布日期": cls._value(d, "gongGaoFaBuTime", "faBuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _award(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        contacts = cls._contacts(text)
        details = cls._award_details(text)
        return {
            "项目性质": "招标信息",
            "源站公告性质": "中标结果公示",
            "项目名称": cls._project_name(d, text),
            "所属行业": cls._label(text, "所属行业"),
            "组织形式": cls._label(text, "组织形式"),
            "招标方式": cls._label(text, "招标方式"),
            "中标人名称": [row["中标人名称"] for row in details],
            "联合体成员": cls._list_label(text, "联合体成员"),
            "中标价": [row["中标价"] for row in details],
            "中标结果明细": details,
            "工期": cls._label(text, "工期", "交货期", "服务期"),
            "项目经理": cls._label(text, "项目经理", "项目负责人"),
            "项目经理证书名称": cls._label(text, "证书名称"),
            "项目经理证书编号": cls._label(text, "证书编号"),
            **cls._contact_fields(contacts, award=True),
            "依据文件": cls._label(text, "依据文件"),
            "依据文号": cls._value(d, "gongGaoBianHao", "winBidBulletinCode") or cls._number(text),
            "发布日期": cls._value(d, "gongGaoFaBuTime", "faBuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @staticmethod
    def raw_html(category: str, detail: Mapping[str, Any]) -> str:
        if category == "tender":
            return str(detail.get("gongGaoNeiRong") or "")
        if category in {"candidate", "award"}:
            return str(detail.get("neiRong") or "")
        if category == "plan":
            body = html.escape(json.dumps(dict(detail), ensure_ascii=False, indent=2))
            return f"<pre data-source=\"bitbid-zbjhInfo\">{body}</pre>"
        return ""

    @classmethod
    def attachments(cls, category: str, detail: Mapping[str, Any]) -> list[dict[str, Any]]:
        notice_id = cls._value(detail, "id")
        result: list[dict[str, Any]] = []
        if category != "plan" and notice_id:
            url = config.pdf_url(category, notice_id)
            if url:
                result.append({
                    "file_name": cls._value(detail, "signFileNameLocal") or f"{notice_id}.pdf",
                    "file_url": url,
                    "mime_type": "application/pdf",
                    "source": "signed_pdf",
                })
        elif category == "plan" and notice_id:
            name = cls._value(detail, "project_reg_cert_fileLocal")
            if name:
                result.append({
                    "file_name": name,
                    "file_url": f"{config.PDF_BASE_URL}/tPlanProject!planRegCertFileDownload.action?tPlanProject3.id={notice_id}",
                    "mime_type": "application/pdf",
                    "source": "plan_attachment",
                })
        return result

    @classmethod
    def _project_name(cls, d: Mapping[str, Any], text: str) -> str:
        title = cls._value(d, "gongGaoMingCheng", "gongShiBiaoTi", "title", "name")
        return re.sub(r"(?:招标公告|中标候选人公示|中标结果公示|中标结果公告)$", "", title).strip()

    @staticmethod
    def _organization(d: Mapping[str, Any]) -> str:
        value = str(d.get("zhaoBiaoZuZhiXingShi") or "").strip()
        return {"1": "自行招标", "2": "委托招标"}.get(value, value)

    @classmethod
    def _region(cls, xm: Mapping[str, Any]) -> str:
        return "".join(filter(None, (cls._value(xm, "shengName"), cls._value(xm, "shiName"))))

    @staticmethod
    def _value(d: Mapping[str, Any], *keys: str) -> str:
        for key in keys:
            value = d.get(key)
            if value not in (None, "", [], {}):
                return str(value).strip()
        return ""

    @staticmethod
    def _range(start: str, end: str) -> str:
        if start and end:
            return f"{start} 至 {end}"
        return start or end

    @staticmethod
    def _label(text: str, *labels: str) -> str:
        for label in labels:
            pattern = rf"(?m)(?:^|\n)\s*[（(]?(?:\d+(?:\.\d+)*[、.]?\s*)?{re.escape(label)}\s*[：:]\s*([^\n]+)"
            match = re.search(pattern, text)
            if match:
                value = match.group(1).strip(" ：:;；")
                if (
                    value.endswith("）") and value.count("（") < value.count("）")
                ) or (
                    value.endswith(")") and value.count("(") < value.count(")")
                ):
                    value = value[:-1].rstrip()
                return value
        return ""

    @classmethod
    def _list_label(cls, text: str, label: str) -> list[str]:
        value = cls._label(text, label)
        return [x.strip() for x in re.split(r"[、,，;/；]", value) if x.strip()]

    @staticmethod
    def _section(text: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
        start_re = "|".join(re.escape(x) for x in starts)
        end_re = "|".join(re.escape(x) for x in ends)
        match = re.search(
            rf"(?ms)(?:^|\n)\s*(?:[一二三四五六七八九十]+[、.]|\d+(?:\.\d+)*[、.]?)?\s*(?:{start_re})\s*[：:]?\s*(.*?)(?=\n\s*(?:[一二三四五六七八九十]+[、.]|\d+(?:\.\d+)*[、.]?)?\s*(?:{end_re})\s*[：:]?|\Z)",
            text,
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _number(text: str) -> str:
        match = re.search(r"(?:招标项目编号|项目编号|招标编号)\s*[：:]\s*([^）)\s]+)", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _publicity_time(text: str) -> str:
        start = re.search(r"公示开始时间\s*[：:]\s*([^\n]+?)(?=\s+公示结束时间|$)", text, re.M)
        end = re.search(r"公示结束时间\s*[：:]\s*([^\n]+)", text)
        if start and end:
            return f"{start.group(1).strip()} 至 {end.group(1).strip()}"
        return (start or end).group(1).strip() if start or end else ""

    @staticmethod
    def _online_place(text: str) -> str:
        if "比比网电子招投标交易平台" in text or "比比网电子招标投标交易平台" in text:
            return "比比网电子招投标交易平台"
        return ""

    @classmethod
    def _candidate_details(cls, text: str) -> list[dict[str, str]]:
        block = cls._section(text, ("中标候选人基本情况",), ("中标候选人按照", "中标候选人响应", "提出异议")) or text
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        company_re = re.compile(r"(?m)^\s*(\d+)\s+([^\n]{2,80}?(?:公司|厂|院|中心|集团|研究所))\s+(.+)$")
        for match in company_re.finditer(block):
            name = _SPACE_RE.sub(" ", match.group(2)).strip()
            if name in seen:
                continue
            tail = match.group(3).strip()
            amount = cls._amount(tail)
            rows.append({"标段": cls._label(block, "标段", "标段（包）"), "候选人名称": name, "候选人报价": amount})
            seen.add(name)
        return rows

    @classmethod
    def _award_details(cls, text: str) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        pattern = re.compile(r"中标人\s*[：:]\s*([^\n]{2,100}?)(?=\s+中标(?:价格|价)\s*[：:]|\n|$)\s*(?:中标(?:价格|价)\s*[：:]\s*([^\n]+))?")
        for match in pattern.finditer(text):
            name = match.group(1).strip()
            price = (match.group(2) or "").strip()
            if name and not any(row["中标人名称"] == name for row in rows):
                rows.append({"标段": cls._label(text, "标段", "标段（包）"), "中标人名称": name, "中标价": price})
        return rows

    @staticmethod
    def _amount(text: str) -> str:
        match = re.search(r"(?:报价|总价|金额|价格)[^:：\n]{0,15}[：:]?\s*([\d,.，]+\s*(?:万元|元)?(?:\s*;?\s*税率[^;；\n]*)?)", text)
        return match.group(1).strip() if match else ""

    @classmethod
    def _contacts(cls, text: str) -> dict[str, dict[str, str]]:
        result = {"owner": {}, "agency": {}}
        owner = re.search(r"(?s)招\s*标\s*人\s*[：:]\s*(.*?)(?=招标代理机构\s*[：:]|\Z)", text)
        agency = re.search(r"(?s)招标代理机构\s*[：:]\s*(.*?)(?=招标人或其招标代理机构|\Z)", text)
        for key, match in (("owner", owner), ("agency", agency)):
            if not match:
                continue
            block = match.group(1).strip()
            first = block.splitlines()[0].strip() if block else ""
            result[key] = {
                "name": first,
                "address": cls._label(block, "地址", "联系地址"),
                "contact": cls._label(block, "联系人", "联 系 人"),
                "phone": cls._label(block, "电话", "联系电话"),
            }
        return result

    @staticmethod
    def _contact_fields(contacts: Mapping[str, Mapping[str, str]], *, award: bool) -> dict[str, str]:
        owner = contacts.get("owner", {})
        agency = contacts.get("agency", {})
        owner_name = "招标人/采购人" if award else "招标人/采购人名称"
        return {
            owner_name: owner.get("name", ""),
            "招标人地址": owner.get("address", ""),
            "招标人联系人": owner.get("contact", ""),
            "招标人联系方式": owner.get("phone", ""),
            "招标代理机构": agency.get("name", ""),
            "招标代理机构地址": agency.get("address", ""),
            "招标代理机构联系人": agency.get("contact", ""),
            "招标代理机构联系方式": agency.get("phone", ""),
        }
