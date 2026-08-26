"""比比网四类招采公告的站点专用字段解析。"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Mapping

from lxml import html as lxml_html

from crawler_scrapy.schemas.notice_fields import create_empty_notice_data
from crawler_scrapy.sites.bitbid import config


_SPACE_RE = re.compile(r"[\t\r\f\v \u3000]+")
_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"(?i)<(?:br\s*/?|/p|/div|/tr|/li|/h[1-6])\s*>")
_IDENTIFIER_STOP_LABELS = (
    "项目名称", "预算金额", "最高限价", "采购需求", "资金来源", "招标范围",
    "计划期限", "计划工期", "质量要求", "标段划分", "招标人", "采购人",
    "发布日期", "公告日期", "采购品目", "代理机构内部编号", "代理公司编号",
    "采购代理机构编号", "委托代理编号", "代理编号", "交易登记号", "交易编号",
    "公共资源交易编号", "涉及包号", "项目内容", "招标内容", "工程名称",
    "项目地址", "建设地点", "工程概况", "建设规模及内容", "建设规模",
    "项目名称及数量", "采购内容及要求", "采购内容", "采购计划编号",
    "采购项目标书编号", "项目标书编号", "标书编号", "采购单位", "竞价时间",
    "项目简要说明", "采购方式", "项目序列号",
)
_IDENTIFIER_PROSE_MARKERS = (
    "资金来源", "招标人", "采购人", "已由", "批准", "本项目", "经评标委员会",
    "经评审", "现将", "现对", "进行公开招标", "在本地区", "建设单位",
    "项目名称", "预算金额", "最高限价", "采购需求",
    "招标范围", "计划工期", "质量要求", "发布日期", "采购品目", "涉及包号",
    "项目内容", "招标内容", "工程名称", "项目地址", "建设地点", "工程概况",
    "建设规模", "采购内容", "采购计划编号", "标书编号", "采购单位", "竞价时间",
    "招标公告", "采购公告", "中标公告", "成交公告", "候选人公示", "结果公示",
    "网上投标", "建设项目房建", "建设项目施工",
)


def valid_identifier(value: Any) -> bool:
    """编号入顶层字段前的统一兜底校验，避免把接口中的公告标题当编号。"""

    candidate = str(value or "").strip()
    return (
        4 <= len(candidate) <= 128
        and bool(re.search(r"\d", candidate))
        and not re.search(r"[|；;：:&]", candidate)
        and not any(marker in candidate for marker in _IDENTIFIER_PROSE_MARKERS)
        and all(
            candidate.count(opening) == candidate.count(closing)
            for opening, closing in (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】"))
        )
    )


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
        "招标人", "地址", "联系人", "项目联系人", "联系电话", "电话", "电子邮件",
        "项目名称", "项目负责人", "招标代理机构",
    ):
        spaced = r"\s*".join(map(re.escape, compact))
        result = re.sub(spaced, compact, result)
    return result


class BitbidParser:
    """把比比网接口字段、HTML正文和PDF文字统一映射到公共Schema。"""

    parser_version = "bitbid-v6-spaced-time-and-guarantee"

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
        title = cls._value(
            detail, "gongGaoMingCheng", "gongShiBiaoTi", "title", "name"
        )
        notice_type = cls._notice_type(category, title)

        if notice_type == "招标计划":
            extracted = cls._plan(detail, text)
        elif notice_type == "资格预审公告":
            extracted = cls._prequalification(detail, payload, text)
        elif notice_type == "招标公告":
            extracted = cls._tender(detail, payload, text)
        elif notice_type == "中标候选人公示":
            extracted = cls._candidate(detail, text, raw_html)
        elif notice_type == "定标候选人公示":
            extracted = cls._finalization_candidate(detail, text, raw_html)
        elif notice_type == "中标结果公示":
            extracted = cls._award(detail, text, raw_html)
        elif notice_type == "更正结果公示":
            extracted = cls._correction(detail, payload, text, title)
        else:
            raise ValueError(f"不支持的比比网公告类型：{notice_type}")
        data = create_empty_notice_data(
            notice_type, include_parser_diagnostics=True
        )
        for field, value in extracted.items():
            if field in data:
                data[field] = value
        xm_info = payload.get("xmInfo") or {}
        if not isinstance(xm_info, Mapping):
            xm_info = {}
        project_identifier = cls._identifier_label(
            text,
            "招标项目编号",
            "投资项目统一代码",
            "项目代码",
            "项目编号",
        ) or cls._value(
            xm_info,
            "projectCode",
            "xiangMuBianHao",
            "projectNo",
        ) or (cls._value(detail, "codeByAuth") if category == "plan" else "")
        tender_identifier = cls._identifier_label(
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
        data["项目编号"] = project_identifier if valid_identifier(project_identifier) else ""
        data["招标编号"] = tender_identifier if valid_identifier(tender_identifier) else ""
        combined = "；".join(
            dict.fromkeys(
                value for value in (data["项目编号"], data["招标编号"]) if value
            )
        )
        for field in ("项目编号/招标编号", "招标编号/项目编号"):
            if field in data:
                data[field] = combined
        return notice_type, data, cls.attachments(category, detail), raw_html, text

    @staticmethod
    def _notice_type(category: str, title: str) -> str:
        if category == "plan":
            return "招标计划"
        if category == "candidate":
            return "中标候选人公示"
        correction_words = (
            "控制价", "最高限价", "变更", "更正", "澄清", "延期", "终止", "暂停",
            "撤销", "流标", "废标", "补充通知",
        )
        if any(word in title for word in correction_words):
            return "更正结果公示"
        if category == "award":
            return "中标结果公示"
        if "定标候选人" in title:
            return "定标候选人公示"
        if any(word in title for word in ("中标候选人", "成交候选人", "候选人公示")):
            return "中标候选人公示"
        if any(
            word in title
            for word in (
                "中标结果", "成交结果", "中标公告", "成交公告",
                "招标结果公告", "采购结果公告", "中标（成交）公告",
                "中标(成交)公告",
            )
        ):
            return "中标结果公示"
        if any(word in title for word in ("资格预审", "资审公告")):
            return "资格预审公告"
        return "招标公告"

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
            # 比比网新旧版本复用了这些键：只有值域符合字段语义时才采用，
            # 例如旧数据 nameApproval 可能是批复文件标题，capital 可能是资金来源。
            "项目性质": cls._project_nature(cls._value(d, "nameApproval")),
            "招标方式": cls._tender_method(cls._value(d, "capital")),
            "项目名称": cls._clean_project_name(cls._value(d, "name", "title")),
            "项目类型": cls._plan_project_type(cls._value(d, "planProjectOverview")),
            "项目总投资": cls._value(d, "investEstimation"),
            "招标内容": clean_html(cls._value(d, "planProjectBidSco")),
            "招标人名称": cls._organization_name(
                cls._value(d, "legalPerson", "cityName")
            ),
            "行政监督部门": cls._supervision_department(
                cls._value(d, "approvalNumber")
            ),
            "建设地点": cls._plan_location(d),
            "建设内容及规模": clean_html(cls._value(d, "scale")),
            "招标公告（资格预审公告）预计发布时间": cls._planned_publish_time(
                cls._value(d, "remark")
            ),
            "发布日期": cls._value(d, "fabuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _prequalification(
        cls, d: Mapping[str, Any], payload: Mapping[str, Any], text: str
    ) -> dict[str, Any]:
        result = cls._tender(d, payload, text)
        result["源站公告性质"] = "资格预审公告"
        result["项目概况与招标范围"] = result.pop("招标内容与范围", "")
        return result

    @classmethod
    def _tender(
        cls, d: Mapping[str, Any], payload: Mapping[str, Any], text: str
    ) -> dict[str, Any]:
        xm = payload.get("xmInfo") or {}
        if not isinstance(xm, Mapping):
            xm = {}
        contacts = cls._contacts(text)
        return {
            "项目性质": "",
            "源站公告性质": (
                "招标控制价公告"
                if "控制价" in cls._value(d, "gongGaoMingCheng", "title")
                else "招标公告"
            ),
            # 历史接口的 faBaoMingCheng 存在“开标测试校验值”等占位脏值；
            # 正文显式项目名称/公告标题可回溯，优先级必须高于该项目表字段。
            "项目名称": cls._project_name(d, text)
            or cls._clean_project_name(cls._value(xm, "faBaoMingCheng")),
            "所属行业": cls._value(d, "suoShuHangYe") or cls._label(text, "所属行业"),
            "组织形式": cls._organization(d),
            "开标时间": cls._label(text, "开标时间", "开启时间"),
            "项目类型/行业分类": cls._value(xm, "xiangMuLeiXing", "publicResourcesClassification"),
            "项目总投资/估算金额": cls._label(text, "项目总投资", "估算金额", "投资估算"),
            "招标金额": cls._label(
                text, "招标金额", "招标控制价", "最高投标限价", "预算金额"
            ),
            "资金来源": cls._value(xm, "ziJinLaiYuan") or cls._funding_source(text),
            "项目地点": cls._label(
                text, "服务地点", "交货地点", "项目地点", "建设地点"
            ) or cls._detail_region(d) or cls._region(xm),
            "招标人/采购人名称": cls._value(d, "zhaoBiaoRenName") or contacts["owner"].get("name", ""),
            "项目规模": cls._label(text, "项目规模"),
            "工期/服务期/供货日期": cls._label(
                text, "合同履行期限", "服务期限", "服务期", "工程工期",
                "计划工期", "工期", "交货期限", "供货期",
            ),
            "质量要求": cls._label(text, "质量要求", "质量标准"),
            "招标内容与范围": cls._tender_scope(text),
            "申请人资格要求/投标人资格要求": cls._section(
                text,
                (
                    "对投标人资格要求", "投标人资格要求", "投标人资格条件",
                    "投标人资格要求及提交资料",
                    "投标供应商资格要求", "投标供应商资格条件",
                    "本次招标为公开招标，投标人资格要求如下",
                    "申请人资格要求", "供应商资格要求", "供应商资格条件",
                    "合格投标人资格要求", "资格要求",
                ),
                (
                    "招标文件的获取", "获取招标文件", "报名及招标文件获取",
                    "报名及招标文件的获取", "报名方式", "报名须知",
                    "投标报名及招标文件的获取方式",
                    "报名材料", "投标人报名时须提供", "投标报名时间",
                    "征集文件的获取", "采购文件的获取", "招标文件发售",
                    "投标文件的递交", "响应文件的递交", "开标时间及地点",
                    "发布公告的媒介", "联系方式",
                ),
            ),
            "预审文件获取时间": cls._range(cls._value(d, "bidDocStartTime", "preDocStartTime"), cls._value(d, "bidDocEndTime", "preDocEndTime")) or cls._label(text, "获取时间"),
            "获取方式": cls._label(text, "获取方式", "获取方法"),
            "递交截止时间": cls._value(d, "submitDocEndTime") or cls._label(text, "递交截止时间"),
            "递交方法": cls._value(d, "submitDocMethod") or cls._label(text, "递交方法", "递交方式"),
            "开启时间": cls._label(text, "开启时间", "开标时间"),
            "开启方式": cls._label(text, "开标方式", "开启方式"),
            "开启地点": cls._label(text, "开标地点", "开启地点") or cls._online_place(text),
            "评审办法": cls._value(d, "evaluationMethod") or cls._label(text, "评标办法", "评审办法"),
            "投标保证金方式": cls._label(
                text, "投标保证金方式", "保证金递交方式"
            ) or cls._section(
                text,
                (
                    "提交投标保证金的形式",
                    "投标保证金的形式",
                    "响应保证金的递交",
                    "投标保证金的递交",
                ),
                (
                    "提出异议的渠道和方式",
                    "提出异议的渠道",
                    "其他公示内容",
                    "其他公告内容",
                    "监督部门",
                    "联系方式",
                ),
            ),
            **cls._contact_fields(contacts, award=False),
            "发布日期": cls._value(d, "gongGaoFaBuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _candidate(
        cls, d: Mapping[str, Any], text: str, source_html: str = ""
    ) -> dict[str, Any]:
        contacts = cls._contacts(text)
        details = cls._candidate_details(text, source_html=source_html)
        return {
            "项目性质": "",
            "源站公告性质": "中标候选人公示",
            "项目名称": cls._project_name(d, text),
            "所属行业": cls._label(text, "所属行业"),
            "组织形式": cls._label(text, "组织形式"),
            "开标时间": cls._label(text, "开标时间") or cls._opening_time(text),
            "公示时间": cls._range(cls._value(d, "startTime"), cls._value(d, "endTime")) or cls._publicity_time(text),
            "中标候选人名称": [row["候选人名称"] for row in details],
            "中标候选人报价": [
                row["候选人报价"] if row["候选人报价"] else None
                for row in details
            ],
            "中标候选人明细": details,
            **cls._contact_fields(contacts, award=True),
            "发布日期": cls._value(d, "gongGaoFaBuTime", "faBuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _award(
        cls, d: Mapping[str, Any], text: str, source_html: str = ""
    ) -> dict[str, Any]:
        contacts = cls._contacts(text)
        details = cls._award_details(text, source_html=source_html)
        return {
            "项目性质": "",
            "源站公告性质": "中标结果公示",
            "项目名称": cls._project_name(d, text),
            "所属行业": cls._label(text, "所属行业"),
            "组织形式": cls._label(text, "组织形式"),
            "招标方式": cls._label(text, "招标方式"),
            "中标人名称": [row["中标人名称"] for row in details],
            "联合体成员": cls._list_label(text, "联合体成员"),
            "中标价": [row["中标价"] if row["中标价"] else None for row in details],
            "中标结果明细": details,
            "工期": cls._label(text, "工期", "交货期", "服务期"),
            "项目经理": cls._label(text, "项目经理", "项目负责人"),
            "项目经理证书名称": cls._label(text, "证书名称"),
            "项目经理证书编号": cls._label(text, "证书编号"),
            **cls._contact_fields(contacts, award=True),
            "依据文件": cls._label(text, "依据文件"),
            # 公告编号/招标编号不是法律或批复“依据文号”，不得混写。
            "依据文号": cls._label(text, "依据文号"),
            "发布日期": cls._value(d, "gongGaoFaBuTime", "faBuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _finalization_candidate(
        cls, d: Mapping[str, Any], text: str, source_html: str = ""
    ) -> dict[str, Any]:
        contacts = cls._contacts(text)
        details = cls._candidate_details(text, source_html=source_html)
        return {
            "项目性质": "",
            "项目名称": cls._clean_project_name(
                cls._html_key_value(
                    source_html, "招标工程名称", "工程名称", "项目名称"
                )
                or cls._inline_label(text, "招标项目名称")
                or cls._project_name(d, text)
            ),
            "所属行业": cls._label(text, "所属行业"),
            "组织形式": cls._label(text, "组织形式"),
            "开标时间": cls._label(text, "开标时间") or cls._opening_time(text),
            "公示时间": cls._range(
                cls._value(d, "startTime"), cls._value(d, "endTime")
            ) or cls._html_key_value(source_html, "公示时间") or cls._publicity_time(text),
            "定标候选人名称": [row["候选人名称"] for row in details],
            "定标候选人报价": [
                row["候选人报价"] if row["候选人报价"] else None
                for row in details
            ],
            "定标候选人项目经理": cls._label(text, "项目经理"),
            "定标候选人项目经理相关证书及编号": cls._label(
                text, "项目经理相关证书及编号", "项目经理证书及编号"
            ),
            "定标候选人项目副经理": cls._label(text, "项目副经理"),
            "定标候选人项目副经理相关证书及编号": cls._label(
                text, "项目副经理相关证书及编号", "项目副经理证书及编号"
            ),
            "定标候选人资信情况": cls._label(text, "资信情况"),
            "定标候选人业绩情况（名称、日期、金额）": cls._label(
                text, "业绩情况", "候选人业绩"
            ),
            **cls._contact_fields(contacts, award=True),
            "依据文件": cls._label(text, "依据文件"),
            "依据文号": cls._label(text, "依据文号"),
            "发布日期": cls._value(d, "gongGaoFaBuTime", "faBuTime"),
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _correction(
        cls,
        d: Mapping[str, Any],
        payload: Mapping[str, Any],
        text: str,
        title: str,
    ) -> dict[str, Any]:
        contacts = cls._contacts(text)
        return {
            "公共类型": cls._correction_type(title),
            "项目名称": cls._project_name(d, text),
            "所属行业": cls._value(d, "suoShuHangYe") or cls._label(text, "所属行业"),
            "组织形式": cls._organization(d),
            "开标时间": cls._last_label(text, "变更后开标时间", "开标时间"),
            "标书发售时间": cls._range(
                cls._value(d, "bidDocStartTime", "preDocStartTime"),
                cls._value(d, "bidDocEndTime", "preDocEndTime"),
            ),
            "公告内容": cls._correction_content(text, title),
            **cls._contact_fields(contacts, award=True),
            "监督部门地址": cls._label(text, "监督部门地址"),
            "监督部门联系人": cls._label(text, "监督部门联系人"),
            "监督部门联系方式": cls._label(
                text, "监督部门联系方式", "监督部门联系电话"
            ),
            "依据文件": cls._label(text, "依据文件"),
            "依据文号": cls._label(text, "依据文号"),
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
                    "source_file_id": cls._value(detail, "signFileNameServer") or None,
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
                    "file_url": f"{config.PLAN_FILE_BASE_URL}/tPlanProject!planRegCertFileDownload.action?tPlanProject3.id={notice_id}",
                    "mime_type": "application/pdf",
                    "source": "plan_attachment",
                })
        return result

    @classmethod
    def _project_name(cls, d: Mapping[str, Any], text: str) -> str:
        # 正文的“项目名称”比接口公告标题更接近项目实体；例如历史候选公示
        # 的接口标题可能是“某项目招标公告中标候选人公示”。
        value = cls._label(
            text, "招标项目名称", "项目名称", "招标工程名称", "工程名称"
        ) or cls._value(
            d, "gongGaoMingCheng", "gongShiBiaoTi", "title", "name"
        )
        return cls._clean_project_name(value)

    @staticmethod
    def _clean_project_name(value: str) -> str:
        result = str(value or "").strip()
        prefix = r"^(?:[【\[（(](?:变更公告|更正公告|澄清公告|延期公告|终止公告)[】\]）)]\s*)+"
        result = re.sub(prefix, "", result)
        # 公告标题常带轮次，如“国际招标公告(1)”；轮次属于公告而非项目名。
        result = re.sub(
            r"((?:国际)?招标公告|采购公告|询价公告|竞价公告|"
            r"竞争性(?:磋商|谈判)公告)[（(](?:第?\s*)?[一二三四五六七八九十\d]+(?:\s*次)?[）)]$",
            r"\1",
            result,
        )
        suffix = re.compile(
            r"(?:\s*[-—]\s*(?:采购公告|采购需求公示)|"
            r"(?:第?\s*[一二三四五六七八九十\d]+\s*次)?"
            r"(?:资格预审公告|中标候选人公示|定标候选人公示|中标结果公示|中标结果公告|"
            r"废标结果公示|控制价(?:变更)?|变更公告|更正公告|澄清公告|"
            r"招标控制价(?:第?\s*[一二三四五六七八九十\d]+\s*次)?变更|"
            r"延期公告|终止公告|撤销公告|流标公告|废标公告|招标公告|采购公告|"
            r"国际招标公告|询价公告|竞价公告|竞争性磋商公告|竞争性谈判公告|"
            r"比选公告|遴选公告|征集公告|控制价公告|处置公告|"
            r"进口产品(?:采购)?的公示|"
            r"最高限价公示|中标公告|成交公告|成交结果公告|"
            r"技术参数公示|(?:的)?公告|公示|"
            r"(?:公开)?招标结果公告|采购结果公告|中标[（(]成交[）)]公告|"
            r"项目公告|采购需求公示))$"
        )
        previous = None
        while result and previous != result:
            previous = result
            result = suffix.sub("", result).strip()
        return result.strip(" -—_。；;，,")

    @staticmethod
    def _organization(d: Mapping[str, Any]) -> str:
        value = str(d.get("zhaoBiaoZuZhiXingShi") or "").strip()
        return {"1": "自行招标", "2": "委托招标"}.get(value, value)

    @classmethod
    def _region(cls, xm: Mapping[str, Any]) -> str:
        values = (cls._value(xm, "shengName"), cls._value(xm, "shiName"))
        return "".join(value for value in values if value and not value.isdigit())

    @classmethod
    def _detail_region(cls, detail: Mapping[str, Any]) -> str:
        return "".join(
            filter(
                None,
                (
                    (
                        value
                        if value and not value.isdigit()
                        else ""
                    )
                    for value in (
                        cls._value(detail, "xiangmushudiSheng"),
                        cls._value(detail, "xiangmushudiShi"),
                    )
                ),
            )
        )

    @classmethod
    def _tender_scope(cls, text: str) -> str:
        """按同级编号截取招标内容，排除期限、质量和资格等相邻字段。"""

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        labels = (
            "项目概况与招标范围", "项目概况和招标范围",
            "招标内容与范围", "招标范围", "采购需求", "采购范围", "采购内容",
        )
        for index, line in enumerate(lines):
            match = re.match(
                r"^(?P<number>\d+(?:\.\d+)*)?[、.．]?\s*"
                rf"(?:{'|'.join(re.escape(label) for label in labels)})"
                r"\s*[：:]\s*(?P<value>.*)$",
                line,
            )
            if not match:
                continue
            selected = [match.group("value").strip()] if match.group("value").strip() else []
            number = [int(value) for value in (match.group("number") or "").split(".") if value]
            for following in lines[index + 1 :]:
                if re.match(
                    r"^(?:[一二三四五六七八九十百]+[、.．]|\d+[、.．])\s*"
                    r"(?:投标人|申请人|供应商|招标文件|采购文件|投标文件|"
                    r"响应文件|开标|发布公告|其他公告|其他公示|联系方式)",
                    following,
                ):
                    break
                next_number = re.match(r"^(\d+(?:\.\d+)*)[、.．]?\s*", following)
                is_lot_code = bool(
                    next_number and re.fullmatch(r"\d{3}", next_number.group(1))
                )
                if next_number and number and not is_lot_code:
                    parts = [int(value) for value in next_number.group(1).split(".")]
                    if (
                        len(parts) == len(number)
                        and parts[:-1] == number[:-1]
                        and parts[-1] > number[-1]
                    ) or (len(parts) < len(number) and parts[0] > number[0]):
                        break
                if re.match(
                    r"^(?:投标人|申请人|供应商)(?:资格要求|资格条件)|"
                    r"^(?:招标文件|采购文件|征集文件)(?:的获取|获取)|"
                    r"^(?:投标文件|响应文件)(?:的递交|递交)|"
                    r"^(?:开标时间|开启时间|发布公告|联系方式)\s*[：:]?",
                    following,
                ):
                    break
                if re.match(
                    r"^(?:项目地点|建设地点|服务地点|交货地点|标段划分|投放周期|"
                    r"服务期限|服务期|工期|质量要求|质量标准|服务标准|安全要求|"
                    r"缺陷责任期|资格审查方式)\s*[：:]",
                    following,
                ):
                    continue
                selected.append(following)
            return "\n".join(selected).strip()
        return ""

    @staticmethod
    def _project_nature(value: str) -> str:
        compact = str(value or "").strip()
        if compact in {"依法必须招标", "非依法招标"}:
            return compact
        # 自主/委托发布描述的是发布方式，不是项目法定性质。
        return ""

    @staticmethod
    def _tender_method(value: str) -> str:
        compact = str(value or "").strip()
        allowed = (
            "公开招标", "邀请招标", "询比采购", "询价采购", "竞争性谈判",
            "竞争性磋商", "单一来源采购", "框架协议采购",
        )
        return compact if compact in allowed else ""

    @staticmethod
    def _plan_project_type(value: str) -> str:
        compact = clean_html(value)
        if not compact or len(compact) > 40 or "\n" in compact:
            return ""
        if any(word in compact for word in ("建设内容", "主要建设", "项目概况")):
            return ""
        return compact

    @classmethod
    def _plan_location(cls, detail: Mapping[str, Any]) -> str:
        primary = cls._value(detail, "provinceName")
        secondary = cls._value(detail, "cityName")
        if not primary:
            return "" if cls._organization_name(secondary) else secondary
        # 新版 API 的 provinceName 实际存完整项目地点，cityName 可能是代理机构；
        # 旧版仅存“山西省”，此时只拼接确实像行政区划的 cityName。
        if primary.endswith(("省", "自治区", "市")) and secondary and not cls._organization_name(secondary):
            return primary if secondary in primary else f"{primary}{secondary}"
        return primary

    @staticmethod
    def _organization_name(value: str) -> str:
        compact = clean_html(value)
        markers = (
            "公司", "集团", "中心", "委员会", "管理局", "人民政府", "学校",
            "医院", "研究院", "研究所", "事务所", "合作社", "厂", "矿",
        )
        return compact if any(marker in compact for marker in markers) else ""

    @staticmethod
    def _supervision_department(value: str) -> str:
        compact = clean_html(value)
        if not compact or re.search(r"[（(]?\d{4}[）)]?\s*\d*号$", compact):
            return ""
        markers = (
            "局", "委员会", "办公室", "部门", "中心", "人民政府", "管理处",
        )
        return compact if any(compact.endswith(marker) for marker in markers) else ""

    @staticmethod
    def _planned_publish_time(value: str) -> str:
        compact = clean_html(value)
        patterns = (
            r"\d{4}年\d{1,2}月(?:\d{1,2}日)?",
            r"\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?",
            r"\d{4}年第?[一二三四1234]季度",
            r"\d{4}年(?:上半年|下半年)",
        )
        return compact if any(re.fullmatch(pattern, compact) for pattern in patterns) else ""

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
    def _last_label(cls, text: str, *labels: str) -> str:
        values: list[str] = []
        for line in text.splitlines():
            for label in labels:
                match = re.search(
                    rf"{re.escape(label)}\s*[：:]\s*([^\n]+)", line
                )
                if match:
                    value = match.group(1).strip(" ：:;；。")
                    if value:
                        values.append(value)
        return values[-1] if values else ""

    @staticmethod
    def _inline_label(text: str, *labels: str) -> str:
        for label in labels:
            match = re.search(rf"{re.escape(label)}\s*[：:]\s*([^\n]+)", text)
            if match:
                return match.group(1).strip(" ：:;；。")
        return ""

    @staticmethod
    def _correction_type(title: str) -> str:
        for keyword, value in (
            ("更正", "更正公告"),
            ("变更", "变更公告"),
            ("澄清", "澄清公告"),
            ("延期", "延期公告"),
            ("流标", "流标公告"),
            ("废标", "废标公告"),
            ("撤销", "撤销公告"),
            ("终止", "终止公告"),
            ("控制价", "其他"),
            ("最高限价", "其他"),
            ("补充通知", "其他"),
            ("暂停", "其他"),
        ):
            if keyword in title:
                return value
        return "其他"

    @classmethod
    def _correction_content(cls, text: str, title: str) -> str:
        headings = (
            "变更内容", "更正内容", "澄清内容", "延期内容", "公告内容", "内容",
            "终止原因", "撤销原因", "流标原因", "废标原因", "补充内容",
        )
        for heading in headings:
            value = cls._section(
                text,
                (heading,),
                ("监督部门", "联系方式", "招标人", "采购人"),
            )
            if value:
                return value
        control = cls._label(
            text, "招标控制价", "最高投标限价", "控制价", "变更后内容", "更正后内容"
        )
        if control:
            return control
        # 更正类若无统一章节，保留去掉标题后的正文作为证据；长度异常会由
        # 混合 AI 管线触发局部复核，而不是在规则层自行摘要或编造。
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines and cls._clean_project_name(lines[0]) == cls._clean_project_name(title):
            lines = lines[1:]
        return "\n".join(lines).strip()

    @staticmethod
    def _opening_time(text: str) -> str:
        match = re.search(
            r"本项目于\s*([^，。；;\n]{6,40}?)\s*(?:（北京时间）|\(北京时间\))?\s*在[^\n]{0,100}?公开开标",
            text,
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _identifier_label(text: str, *labels: str) -> str:
        """在整段正文中按明确标签取编号，优先保留项目代码语义。"""

        for label in labels:
            match = re.search(
                rf"{re.escape(label)}\s*[：:]\s*([^\n，,。；;]+)", text
            )
            if not match:
                continue
            value = html.unescape(match.group(1)).replace("\xa0", " ").strip()
            stop_pattern = "|".join(
                re.escape(marker)
                for marker in sorted(_IDENTIFIER_STOP_LABELS, key=len, reverse=True)
            )
            value = re.split(
                rf"(?:(?:\d+(?:\.\d+)+[、.]?|\d+[、.])\s*)?"
                rf"(?:{stop_pattern})\s*[：:]",
                value,
                maxsplit=1,
            )[0].strip().rstrip("（([【")
            # ``2.3、招标范围`` 一类标题可带章节号；``已由`` 等正文连接词
            # 不能共用这个可选数字前缀，否则会把编号末尾的数字一起吞掉。
            value = re.split(
                r"(?:\d+(?:\.\d+)*[、.]?)?\s*"
                r"(?:招标条件|采购内容及要求|招标公告内容|重新采购品目)",
                value,
                maxsplit=1,
            )[0]
            value = re.split(
                r"(?:已由|文件批准|批准建设|本项目)", value, maxsplit=1
            )[0].strip().rstrip("（([【、")
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
            parts = value.strip().split()
            candidates: list[str] = []
            if len(parts) >= 2 and re.fullmatch(r"[A-Za-z]", parts[0]):
                # 源站存在“招标项目编号：M M110...”排版错误，也存在 PDF
                # 把 ``M110...`` 从 M 后换行的情况。优先选择/还原完整编号。
                candidates.append(
                    parts[1]
                    if parts[1].upper().startswith(parts[0].upper())
                    else f"{parts[0]}{parts[1]}"
                )
            candidates.extend(parts)
            value = ""
            for candidate in candidates:
                candidate = candidate.strip("：:，,。；;、")
                if valid_identifier(candidate):
                    value = candidate
                    break
            while (
                value.endswith("）") and value.count("（") < value.count("）")
            ) or (
                value.endswith(")") and value.count("(") < value.count(")")
            ):
                value = value[:-1].rstrip()
            if value:
                return value
        return ""

    @staticmethod
    def _funding_source(text: str) -> str:
        labelled = BitbidParser._label(text, "项目资金来源", "资金来源")
        if labelled:
            return labelled
        match = re.search(
            r"(?:项目)?资金来源(?:为|是|由)\s*([^，,。；;\n]+)", text
        )
        return match.group(1).strip() if match else ""

    @classmethod
    def _list_label(cls, text: str, label: str) -> list[str]:
        value = cls._label(text, label)
        return [x.strip() for x in re.split(r"[、,，;/；]", value) if x.strip()]

    @staticmethod
    def _section(text: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
        # 长标签优先，避免“投标人资格要求”先吞掉
        # “投标人资格要求及提交资料”的前半段。
        start_re = "|".join(
            re.escape(x) for x in sorted(starts, key=len, reverse=True)
        )
        end_re = "|".join(
            re.escape(x) for x in sorted(ends, key=len, reverse=True)
        )
        number = r"(?:[一二三四五六七八九十]+[、.．]|\d+(?:\.\d+)*[、.．]?)"
        line_start = re.compile(
            rf"(?m)(?:^|\n)\s*(?:{number})?\s*(?:{start_re})\s*[：:]?\s*"
        )
        inline_numbered_start = re.compile(
            rf"(?<!\d){number}\s*(?:{start_re})\s*[：:]?\s*"
        )
        match = line_start.search(text) or inline_numbered_start.search(text)
        if not match:
            return ""
        tail = text[match.end() :]
        end_patterns = (
            re.compile(
                rf"(?m)(?:^|\n)\s*(?:{number})?\s*(?:{end_re})\s*[：:]?"
            ),
            re.compile(rf"(?<!\d){number}\s*(?:{end_re})\s*[：:]?"),
        )
        boundaries = [
            end.start()
            for pattern in end_patterns
            if (end := pattern.search(tail)) is not None
        ]
        return tail[: min(boundaries)].strip() if boundaries else tail.strip()

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
        if start or end:
            return (start or end).group(1).strip()
        period = re.search(
            r"公示期(?:为|：|:)\s*([^，。；;\n]{4,60}?\s+至\s+[^，。；;\n]{4,60})",
            text,
        )
        return period.group(1).strip() if period else ""

    @staticmethod
    def _online_place(text: str) -> str:
        if "比比网电子招投标交易平台" in text or "比比网电子招标投标交易平台" in text:
            return "比比网电子招投标交易平台"
        return ""

    @classmethod
    def _candidate_details(
        cls, text: str, *, source_html: str = ""
    ) -> list[dict[str, Any]]:
        html_rows = cls._html_result_details(source_html, text=text, candidate=True)
        if html_rows:
            return html_rows
        rows: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        company_re = re.compile(r"(?m)^\s*(\d+)\s+([^\n]{2,80}?(?:公司|厂|院|中心|集团|研究所))\s+(.+)$")
        segment_re = re.compile(r"(?m)^\s*标段（包）\s*([^：:\n]+)\s*[：:]")
        matches = list(segment_re.finditer(text))
        segments: list[tuple[str, str]] = []
        if matches:
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                segments.append((match.group(1).strip(), text[match.end():end]))
        else:
            segments.append(("", text))

        for segment, segment_text in segments:
            block = cls._section(
                segment_text,
                ("中标候选人基本情况",),
                ("中标候选人按照", "中标候选人响应", "提出异议"),
            ) or segment_text
            for match in company_re.finditer(block):
                name = _SPACE_RE.sub(" ", match.group(2)).strip()
                identity = (segment, name)
                if identity in seen:
                    continue
                tail = match.group(3).strip()
                rows.append({
                    "标段": segment,
                    "候选人名称": name,
                    "候选人报价": cls._candidate_amount(tail),
                })
                seen.add(identity)
        if rows:
            return rows

        # 早期公告没有标准表格，常使用“第一中标候选人：…”或
        # “标段一中标单位：…”；这些明确角色标签可以安全规则提取。
        narrative = re.compile(
            r"(?m)^\s*(?:(?P<section>[^\n：:]{0,30}?(?:标段|包)[一二三四五六七八九十\d]*)\s*)?"
            r"(?:\d+[.、]\s*)?"
            r"(?:第?[一二三四五六七八九十\d]+\s*(?:中标|成交|定标)?候选人|"
            r"(?:中标|成交|定标)?候选人(?:名称)?|投标人名称|中标单位)\s*[：:]\s*"
            r"(?P<name>[^\n；;]{2,180})"
        )
        for match in narrative.finditer(text):
            name = cls._clean_result_name(match.group("name"))
            # 同行若继续写报价标签，只保留名称部分。
            name = re.split(r"\s+(?=(?:报价|投标价|中标价)[：:]?)", name, 1)[0]
            section = (match.group("section") or "").strip()
            identity = (section, name)
            if not name or identity in seen:
                continue
            context = text[match.end() : match.end() + 600]
            next_role = re.search(
                r"\n\s*(?:(?:第?[一二三四五六七八九十\d]+\s*)?"
                r"(?:中标|成交|定标)?候选人(?:名称)?|投标人名称|中标单位)\s*[：:]",
                context,
            )
            if next_role:
                context = context[: next_role.start()]
            rows.append({
                "标段": section,
                "候选人名称": name,
                "候选人报价": cls._amount(context),
            })
            seen.add(identity)
        return rows

    @classmethod
    def _candidate_amount(cls, tail: str) -> str:
        labelled = cls._amount(tail)
        if labelled:
            return labelled
        numeric = re.match(
            r"^[￥¥]?\s*([\d,.，]+)\s*(亿元|万元|元|%|％)?(?=\s|$)", tail
        )
        if numeric:
            return f"{numeric.group(1)}{numeric.group(2) or ''}"
        if "%" in tail or "％" in tail:
            value = re.split(
                r"\s+(?=合格(?:\s|$)|符合(?:\s|$)|满足(?:\s|$)|响应(?:\s|$))",
                tail,
                maxsplit=1,
            )[0].strip()
            return value if "%" in value or "％" in value else ""
        return ""

    @classmethod
    def _award_details(
        cls, text: str, *, source_html: str = ""
    ) -> list[dict[str, Any]]:
        html_rows = cls._html_result_details(source_html, text=text, candidate=False)
        if html_rows:
            return html_rows
        rows: list[dict[str, str]] = []
        # 入围项目通常不是表格，而是连续多行“入围供应商 + 下浮率”。
        # 每一行都是独立成交主体，不能只取第一行价格。
        shortlisted = re.compile(
            r"(?m)^\s*入围供应商\s*[：:]\s*(?P<name>.+?)\s+"
            r"(?:其他类型)?中标价\s*[：:]\s*(?P<price>[^\n]+?)\s*$"
        )
        for match in shortlisted.finditer(text):
            name = cls._clean_result_name(match.group("name"))
            price = cls._clean_result_price(match.group("price"))
            if name and not any(row["中标人名称"] == name for row in rows):
                rows.append(
                    {
                        "标段": cls._label(text, "标段", "标段（包）"),
                        "中标人名称": name,
                        "中标价": price,
                    }
                )
        if rows:
            return rows
        # PDF 转文本后字段标签可能被排版字符间距拆成“中 标 人”，
        # 但机构名称本身仍应保持原样；只对标签字符允许空白。
        pattern = re.compile(
            r"中\s*标\s*人\s*[：:]\s*([^\n]{2,100}?)"
            r"(?=\s+中\s*标\s*(?:价格|价)\s*[：:]|\n|$)\s*"
            r"(?:中\s*标\s*(?:价格|价)\s*[：:]\s*([^\n]+))?"
        )
        for match in pattern.finditer(text):
            name = cls._clean_result_name(match.group(1))
            price = (match.group(2) or "").strip()
            if name and not any(row["中标人名称"] == name for row in rows):
                rows.append({"标段": cls._label(text, "标段", "标段（包）"), "中标人名称": name, "中标价": price})
        return rows

    @classmethod
    def _html_result_details(
        cls, source_html: str, *, text: str, candidate: bool
    ) -> list[dict[str, Any]]:
        if not source_html.strip() or "<table" not in source_html.lower():
            return []
        try:
            root = lxml_html.fragment_fromstring(source_html, create_parent="div")
        except (ValueError, TypeError):
            return []
        name_words = (
            ("中标候选人名称", "候选人名称", "投标人名称")
            if candidate
            else (
                "中标人名称",
                "成交人名称",
                "中标单位",
                "成交单位",
                # 框架采购常同时确定多家入围供应商；其角色等同于该结果
                # 公告的成交主体，必须与每行下浮率保持一一对应。
                "入围供应商",
            )
        )
        price_words = (
            "报价", "投标价", "中标价", "成交价", "价格", "金额", "下浮率",
        )
        sections = [
            re.split(r"[：:]", line, maxsplit=1)[-1].strip()
            for line in text.splitlines()
            if "标段（包）" in line or re.match(r"^\s*\d{3}\s*[^\n]*标段", line)
        ]
        groups: list[list[tuple[str, str]]] = []
        for table in root.xpath(".//table[not(.//table)]"):
            table_rows: list[list[str]] = []
            for row in table.xpath(".//tr"):
                cells = [
                    re.sub(r"\s+", " ", "".join(cell.itertext())).strip()
                    for cell in row.xpath("./th|./td")
                ]
                if cells:
                    table_rows.append(cells)
            # 定标候选人表常按“字段为行、候选人为列”转置展示，例如：
            # 投标人名称 | 甲 | 乙；投标报价 | 100 | 200。
            transposed_name = next(
                (
                    row
                    for row in table_rows
                    if len(row) > 1
                    and any(word in row[0] for word in name_words)
                    and not any(
                        word in cell
                        for cell in row[1:]
                        for word in price_words
                    )
                ),
                None,
            )
            if transposed_name is not None:
                transposed_price = next(
                    (
                        row
                        for row in table_rows
                        if len(row) > 1
                        and any(word in row[0] for word in price_words)
                    ),
                    [],
                )
                names = transposed_name[1:]
                prices = transposed_price[1:] if transposed_price else []
                group = [
                    (
                        cls._clean_result_name(name),
                        cls._clean_result_price(
                            prices[index] if index < len(prices) else ""
                        ),
                    )
                    for index, name in enumerate(names)
                    if name.strip()
                ]
                if group:
                    groups.append(group)
                    continue
            header_index = next(
                (
                    index
                    for index, row in enumerate(table_rows)
                    if any(any(word in cell for word in name_words) for cell in row)
                ),
                -1,
            )
            if header_index < 0:
                continue
            header = table_rows[header_index]
            name_index = next(
                index
                for index, cell in enumerate(header)
                if any(word in cell for word in name_words)
            )
            price_index = next(
                (
                    index
                    for index, cell in enumerate(header)
                    if any(word in cell for word in price_words)
                ),
                -1,
            )
            group: list[tuple[str, str]] = []
            for row in table_rows[header_index + 1 :]:
                if name_index >= len(row):
                    continue
                name = cls._clean_result_name(row[name_index])
                if (
                    not name
                    or name in name_words
                    or re.fullmatch(r"\d+", name)
                    or len(name) > 300
                ):
                    continue
                price = cls._clean_result_price(
                    row[price_index] if 0 <= price_index < len(row) else ""
                )
                group.append((name, price))
            if group:
                groups.append(group)
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for group_index, group in enumerate(groups):
            section = sections[group_index] if group_index < len(sections) else ""
            for name, price in group:
                identity = (section, name)
                if identity in seen:
                    continue
                seen.add(identity)
                if candidate:
                    result.append({"标段": section, "候选人名称": name, "候选人报价": price})
                else:
                    result.append({"标段": section, "中标人名称": name, "中标价": price})
        return result

    @staticmethod
    def _clean_result_name(value: str) -> str:
        result = re.sub(r"\s+", " ", str(value or "")).strip(" ，,。")
        # HTML/PDF 排版会把中文机构名拆成“有限 公司”，这不是实体名空格。
        return re.sub(r"(?<=[\u4e00-\u9fff）)])\s+(?=[\u4e00-\u9fff（(])", "", result)

    @staticmethod
    def _clean_result_price(value: str) -> str:
        result = re.sub(r"\s+", " ", str(value or "")).strip()
        if re.fullmatch(r"[￥¥]?\s*[\d,.， ]+\s*(?:亿元|万元|元|%|％)?", result):
            return result.replace(" ", "")
        return result

    @staticmethod
    def _html_key_value(source_html: str, *labels: str) -> str:
        if not source_html.strip() or "<table" not in source_html.lower():
            return ""
        try:
            root = lxml_html.fragment_fromstring(source_html, create_parent="div")
        except (ValueError, TypeError):
            return ""
        for row in root.xpath(".//tr"):
            cells = [
                re.sub(r"\s+", " ", "".join(cell.itertext())).strip()
                for cell in row.xpath("./th|./td")
            ]
            if len(cells) >= 2 and any(label in cells[0] for label in labels):
                return cells[1]
        return ""

    @staticmethod
    def _amount(text: str) -> str:
        match = re.search(r"(?:报价|总价|金额|价格)[^:：\n]{0,15}[：:]?\s*([\d,.，]+\s*(?:万元|元)?(?:\s*;?\s*税率[^;；\n]*)?)", text)
        return match.group(1).strip() if match else ""

    @classmethod
    def _contacts(cls, text: str) -> dict[str, dict[str, str]]:
        result = {"owner": {}, "agency": {}}
        owner_label = r"(?:招\s*标\s*人|采\s*购\s*人|建设单位)"
        agency_label = r"(?:招标代理机构(?:名称)?|采购代理机构|招标代理|代理机构)"
        owner = re.search(
            rf"(?s){owner_label}\s*[：:]\s*(.*?)(?={agency_label}\s*[：:]|\Z)",
            text,
        )
        agency = re.search(
            rf"(?s){agency_label}\s*[：:]\s*(.*?)(?=招标人或其招标代理机构|\Z)",
            text,
        )
        for key, match in (("owner", owner), ("agency", agency)):
            if not match:
                continue
            block = match.group(1).strip()
            first = block.splitlines()[0].strip() if block else ""
            if key == "agency" and re.search(r"签名|签章|盖章", first):
                # 某些 HTML 的签章模板先出现“招标代理机构： （盖章）”，
                # 实际机构名称随后以“代理机构名称”公布。
                alternatives = re.findall(
                    r"(?m)^\s*(?:招标代理机构名称|采购代理机构名称|代理机构名称)"
                    r"\s*[：:]\s*([^\n]+)",
                    text,
                )
                first = next(
                    (
                        value.strip()
                        for value in reversed(alternatives)
                        if value.strip() and not re.search(r"签名|签章|盖章", value)
                    ),
                    "",
                )
            result[key] = {
                "name": first,
                "address": cls._label(block, "地址", "联系地址"),
                "contact": cls._label(block, "项目联系人", "联系人", "联 系 人"),
                "phone": cls._label(
                    block, "电话", "联系电话", "联系方式", "联络电话"
                ),
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
