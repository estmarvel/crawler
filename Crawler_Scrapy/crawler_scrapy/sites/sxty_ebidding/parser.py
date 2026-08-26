"""招采进宝山西详情 payload、正文 HTML 和附件解析。"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from crawler_scrapy.sites.bitbid.parser import clean_html, valid_identifier
from crawler_scrapy.sites.sxxindian.parser import SxxindianParser
from crawler_scrapy.sites.sxty_ebidding import config


CAPTCHA_MARKERS = (
    "pointsVerify",
    "captchaVerification",
    "/cms/captcha/complete",
    "安全验证",
    "人机验证",
)


@dataclass(frozen=True)
class DetailMatch:
    project: Mapping[str, Any]
    package: Mapping[str, Any]
    content: Mapping[str, Any]


@dataclass(frozen=True)
class ParsedNotice:
    notice_type: str
    title: str
    publish_time: str
    raw_html: str
    raw_text: str
    data: dict[str, Any]
    attachments: list[dict[str, Any]]
    validation_warnings: list[str]


def contains_captcha(value: bytes | str) -> bool:
    text = (
        value.decode("utf-8", errors="ignore")
        if isinstance(value, bytes)
        else str(value or "")
    )
    return any(marker.lower() in text.lower() for marker in CAPTCHA_MARKERS)


def decode_dynamic_res(value: Any) -> dict[str, Any]:
    """复用官网 detail.js 的 base64 + URL decode 逻辑。"""

    if isinstance(value, Mapping):
        return dict(value)
    encoded = str(value or "").strip()
    if not encoded:
        raise ValueError("详情接口缺少 res")
    try:
        decoded = base64.b64decode(encoded, validate=True).decode(
            "utf-8", errors="strict"
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("详情接口 res 不是有效的 base64 UTF-8 数据") from exc
    candidates = (decoded, unquote(decoded), unquote(unquote(decoded)))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return dict(payload)
    raise ValueError("详情接口 res 解码后不是 JSON 对象")


def find_detail_match(
    payload: Mapping[str, Any],
    content_id: str | int,
    list_record: Mapping[str, Any] | None = None,
) -> DetailMatch:
    """在多标段、多阶段 payload 中精确定位当前公告。"""

    wanted = str(content_id).strip()
    expected_package_code = str((list_record or {}).get("packageCode") or "").strip()
    matches: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    for package in payload.get("packages") or []:
        if not isinstance(package, Mapping):
            continue
        package_code = str(package.get("code") or "").strip()
        package_score = 2 if expected_package_code and package_code == expected_package_code else 0
        if str(package.get("isCurrent") or "") == "1":
            package_score += 1
        for category in package.get("categoryContents") or []:
            if not isinstance(category, Mapping):
                continue
            for content in category.get("contents") or []:
                if not isinstance(content, Mapping):
                    continue
                if str(content.get("id") or "").strip() == wanted:
                    matches.append((package_score, package, content))
    if not matches:
        raise ValueError(f"详情 payload 中没有 contentId={wanted}")
    matches.sort(key=lambda item: item[0], reverse=True)
    _, package, content = matches[0]
    project = payload.get("project")
    return DetailMatch(
        project=dict(project) if isinstance(project, Mapping) else {},
        package=dict(package),
        content=dict(content),
    )


class SxtyEbiddingParser(SxxindianParser):
    parser_version = "sxty-ebidding-v3-hybrid-ai-ready"

    BID_TYPE_LABELS = {
        "10": "工程",
        "20": "货物",
        "30": "服务",
    }
    ORGANIZATION_FORM_LABELS = {
        # 该字典由实采项目的第三方代理关系和平台项目结构交叉核验：10 为
        # 委托招标，20 为自行组织。未知代码必须保持空值，不能交给 AI 猜。
        "10": "委托招标",
        "20": "自行招标",
    }

    @classmethod
    def parse(
        cls,
        feed: str,
        match: DetailMatch,
        list_record: Mapping[str, Any],
    ) -> ParsedNotice:
        definition = config.FEEDS[feed]
        project = dict(match.project)
        package = dict(match.package)
        content = dict(match.content)
        title = str(content.get("title") or list_record.get("title") or "").strip()
        publish_time = cls._time(
            content.get("publishDate") or list_record.get("publishDate")
        )
        raw_html = str(content.get("text") or "")
        raw_text = cls._html_rule_text(raw_html)
        category = definition["category"]
        notice_type = cls._notice_type_local(category, title, raw_text)
        parser_category = "change" if category == "termination" else category
        if parser_category not in {
            "plan", "tender", "change", "candidate", "award", "other"
        }:
            parser_category = "other"
        detail = {
            "title": title,
            "publish_time": publish_time,
            "module": "bidding",
            "category": parser_category,
            "project_type": "all",
            "source_method": str(
                content.get("purchaseName")
                or list_record.get("purchaseName")
                or ""
            ).strip(),
        }
        if notice_type == "招标计划":
            data = cls._plan(detail, raw_text)
            cls._merge_plan_table_fields_local(data, raw_text)
        elif notice_type in {"招标公告", "资格预审公告"}:
            data = cls._tender(
                detail, raw_text, prequalification=notice_type == "资格预审公告"
            )
            cls._merge_tender_sections_local(data, notice_type, raw_text)
        elif notice_type == "中标候选人公示":
            data = cls._candidate(detail, raw_text, raw_html)
            cls._merge_candidate_period_local(data, raw_text)
            candidate_details = cls._candidate_details_local(raw_html, raw_text)
            if candidate_details:
                data["中标候选人名称"] = [
                    item["候选人名称"] for item in candidate_details
                ]
                data["中标候选人报价"] = [
                    item["候选人报价"] for item in candidate_details
                ]
                data["中标候选人明细"] = candidate_details
        elif notice_type == "中标结果公示":
            data = cls._award(detail, raw_text, raw_html)
            award_details = cls._award_details_local(raw_html, raw_text)
            if award_details:
                data["中标人名称"] = [item["中标人名称"] for item in award_details]
                data["中标价"] = [item["中标价"] for item in award_details]
                data["中标结果明细"] = award_details
            cls._merge_award_execution_fields_local(data, raw_html, raw_text)
        else:
            data = cls._correction(detail, raw_text)
            data["公告内容"] = cls._correction_content_local(raw_text)

        warnings: list[str] = []
        cls._merge_structured_fields(
            data,
            notice_type,
            definition,
            project,
            package,
            content,
            raw_text,
        )
        if not raw_html:
            warnings.append("详情接口当前公告没有正文 HTML")
        if not project:
            warnings.append("详情接口没有 project 结构")
        if not package:
            warnings.append("详情接口没有匹配标段结构")
        return ParsedNotice(
            notice_type=notice_type,
            title=title,
            publish_time=publish_time,
            raw_html=raw_html,
            raw_text=raw_text,
            data=data,
            attachments=cls._attachments(content.get("resourceList")),
            validation_warnings=warnings,
        )

    @staticmethod
    def _notice_type_local(category: str, title: str, text: str) -> str:
        source = f"{title}\n{text[:800]}"
        if category == "plan":
            return "招标计划"
        if category in {"change", "termination"}:
            return "更正结果公示"
        # 源站偶尔把“中标结果公示更正”继续放在结果栏目；真实公告语义优先
        # 于栏目，避免进入中标结果 Schema 后生成虚假的项目经理等字段。
        if re.search(r"变更|更正|补充|终止|暂停|废标|流标|撤销|延期", source):
            return "更正结果公示"
        if category == "candidate":
            return "中标候选人公示"
        if category == "award":
            return "中标结果公示"
        if category == "other":
            # 官网“其他公告”中的招标控制价、补充、暂停等均是对既有项目
            # 的后续公告，不是新的招标公告；按框架的更正类 Schema 保存，
            # 正文和源站栏目仍完整保留其真实语义。
            return "更正结果公示"
        if category == "tender":
            if re.search(r"资格预审", source):
                return "资格预审公告"
            if re.search(r"变更|更正|延期|终止|撤销|废标|流标|补充", source):
                return "更正结果公示"
            return "招标公告"
        if re.search(r"变更|更正|补充|终止|暂停|废标|流标|撤销|延期", source):
            return "更正结果公示"
        if "资格预审" in source:
            return "资格预审公告"
        if re.search(r"(?:中标|成交).*候选人|候选人公示", source):
            return "中标候选人公示"
        if re.search(r"(?:中标|成交).*(?:结果|公告|公示)|结果公示", source):
            return "中标结果公示"
        if re.search(r"招标|采购|询比|谈判|磋商|询价|竞价", source):
            return "招标公告"
        return "更正结果公示"

    @classmethod
    def _merge_structured_fields(
        cls,
        data: dict[str, Any],
        notice_type: str,
        definition: Mapping[str, str],
        project: Mapping[str, Any],
        package: Mapping[str, Any],
        content: Mapping[str, Any],
        text: str,
    ) -> None:
        project_name = str(
            project.get("name") or package.get("name") or data.get("项目名称") or ""
        ).strip()
        project_number, tender_number = cls._identifiers_local(text)
        # 官网 detail.js 的项目信息表明确把 project.code 标为“项目编号”。正文
        # 若另有“招标项目编号/招标编号”则以精确标签为准；没有明确项目编号时
        # 才用结构化 project.code 补项目编号，绝不能据此猜测招标编号。
        project_number = project_number or cls._business_code(project.get("code"))
        combined = "；".join(dict.fromkeys(filter(None, (project_number, tender_number))))
        # “建设工程/企业采购”是源站频道，不是依法必须招标等法律性质。
        # 无明确性质证据时必须留空，不能按栏目推断。
        project_nature = ""
        source_label = definition["source_label"]
        owner = str(
            project.get("tendereeOrgName")
            or content.get("tendereeOrgName")
            or ""
        ).strip()
        agency = str(
            project.get("agencyOrgName")
            or content.get("agencyOrgName")
            or ""
        ).strip()
        purchase_name = str(content.get("purchaseName") or "").strip()
        location = "".join(
            dict.fromkeys(
                filter(
                    None,
                    (
                        str(content.get("provinceName") or "").strip(),
                        str(content.get("cityName") or "").strip(),
                        str(content.get("countyName") or "").strip(),
                    ),
                )
            )
        )
        quote_end_time = cls._time(content.get("quoteEndTime"))
        bid_open_time = cls._time(package.get("bidOpenTime"))
        sale_range = cls._range(
            content.get("tenderFileSaleBeginTime") or package.get("saleBeginTime"),
            content.get("tenderFileSaleEndTime") or package.get("saleEndTime"),
        )

        data["发布网站"] = config.PLATFORM_NAME
        if "项目性质" in data:
            data["项目性质"] = project_nature
        if "源站公告性质" in data:
            data["源站公告性质"] = source_label
        if project_name and "项目名称" in data:
            data["项目名称"] = project_name
        data["项目编号"] = project_number
        data["招标编号"] = tender_number
        for field in ("项目编号/招标编号", "招标编号/项目编号"):
            if field in data:
                data[field] = combined

        if notice_type in {"招标公告", "资格预审公告"}:
            if owner:
                data["招标人/采购人名称"] = owner
            if agency:
                data["招标代理机构"] = agency
            organization_form = cls.ORGANIZATION_FORM_LABELS.get(
                str(project.get("bidOrganizationWay") or "").strip(), ""
            )
            if organization_form:
                data["组织形式"] = organization_form
            if purchase_name:
                data["招标方式"] = purchase_name
            bid_type = cls.BID_TYPE_LABELS.get(
                str(content.get("bidType") or "").strip(), ""
            )
            if bid_type:
                data["项目类型/行业分类"] = bid_type
            data["项目地点"] = data.get("项目地点") or location
            # quoteEndTime 是递交/报价截止时间，不能无条件复制为开标或开启
            # 时间。只有正文出现对应语义标签时才使用结构化时间补值。
            if re.search(r"开\s*标\s*时间\s*[：:]", text):
                data["开标时间"] = (
                    data.get("开标时间") or bid_open_time or quote_end_time
                )
            else:
                data["开标时间"] = ""
            if re.search(r"开\s*启\s*时间\s*[：:]", text):
                data["开启时间"] = (
                    data.get("开启时间") or quote_end_time or bid_open_time
                )
            else:
                data["开启时间"] = ""
            data["递交截止时间"] = (
                data.get("递交截止时间") or quote_end_time
            )
            data["预审文件获取时间"] = (
                data.get("预审文件获取时间") or sale_range
            )
        elif notice_type in {"中标候选人公示", "中标结果公示"}:
            if owner:
                data["招标人/采购人"] = owner
            if agency:
                data["招标代理机构"] = agency
            organization_form = cls.ORGANIZATION_FORM_LABELS.get(
                str(project.get("bidOrganizationWay") or "").strip(), ""
            )
            if organization_form:
                data["组织形式"] = organization_form
            if "开标时间" in data:
                data["开标时间"] = data.get("开标时间") or bid_open_time
        elif notice_type == "更正结果公示":
            data["公共类型"] = cls._correction_nature(
                str(content.get("title") or ""), source_label
            )
            if owner:
                data["招标人/采购人"] = owner
            if agency:
                data["招标代理机构"] = agency
            organization_form = cls.ORGANIZATION_FORM_LABELS.get(
                str(project.get("bidOrganizationWay") or "").strip(), ""
            )
            if organization_form:
                data["组织形式"] = organization_form
            # 更正类只能保存正文明确发布的最终时间；终止/流标公告不能把原
            # 项目的 API 时间重新写成仍有效时间。
            if not re.search(r"开\s*标\s*时间\s*[：:]", text):
                data["开标时间"] = ""
            if not re.search(
                r"(?:标书|招标文件|采购文件|询比文件).{0,8}(?:发售|获取)时间\s*[：:]|获取时间\s*[：:]",
                text,
            ):
                data["标书发售时间"] = ""
            # 项目编号/招标编号不属于依据文号。依据字段只能由正文明确标签
            # 或后续混合 AI 的有证据候选填充。
            if data.get("依据文号") in {project_number, tender_number, combined}:
                data["依据文号"] = ""
        elif notice_type == "招标计划":
            data["项目性质"] = project_nature
            data["项目名称"] = project_name or data.get("项目名称")
            data["招标人名称"] = owner or data.get("招标人名称")
            data["建设地点"] = data.get("建设地点") or location
        cls._merge_party_fields(data, notice_type, text)

    @staticmethod
    def _business_code(value: Any) -> str:
        code = re.sub(r"\s+", "", str(value or "")).strip("：:；;,，")
        if not code or not re.search(r"\d", code):
            return ""
        # 纯 16~20 位数字通常是平台内部雪花 ID，不能冒充业务编号。
        if re.fullmatch(r"\d{16,20}", code):
            return ""
        return code

    @staticmethod
    def _html_rule_text(raw_html: str) -> str:
        """按段落/表格单元恢复文字，避免同一标签被 span 拆成多行。"""

        if not raw_html:
            return ""
        soup = BeautifulSoup(raw_html, "html.parser")
        lines: list[str] = []
        for node in soup.select("h1,h2,h3,h4,h5,h6,p,li,tr"):
            # 表格单元格内常再嵌套 p/span；tr 已输出完整的“列 | 列”行，
            # 不能随后把每个单元格又输出一次，否则正文和章节字段会重复。
            if node.name != "tr" and node.find_parent("table") is not None:
                continue
            if node.name == "tr":
                cells = [
                    re.sub(r"\s+", " ", cell.get_text("", strip=True)).strip()
                    for cell in node.select(":scope > th, :scope > td")
                ]
                cells = [cell for cell in cells if cell]
                if len(cells) == 2 and re.search(
                    r"(?:编号|代码|名称|时间|地点|金额|投资|招标人|采购人)[：:]?$",
                    cells[0],
                ):
                    value = f"{cells[0].rstrip('：:')}：{cells[1]}"
                else:
                    value = " | ".join(cells)
            else:
                value = re.sub(r"\s+", " ", node.get_text("", strip=True)).strip()
            if value and (not lines or value != lines[-1]):
                lines.append(value)
        block_text = "\n".join(lines).strip()
        fallback = clean_html(raw_html)
        # 极少数正文只使用 div；此时段落选择器可能遗漏大段文字，回退到公共
        # HTML 清洗结果，宁可标签被拆行也不能丢正文。
        if len(block_text) < max(80, int(len(fallback) * 0.6)):
            return fallback
        return block_text

    @staticmethod
    def _identifiers_local(text: str) -> tuple[str, str]:
        """从行内标签精确截取编号，避免把标签后的整句当编号。"""

        def first(patterns: Sequence[str]) -> str:
            for label in patterns:
                matched = re.search(
                    rf"(?:{label})\s*[：:]\s*"
                    r"([A-Za-z0-9][A-Za-z0-9._/\-\s]{1,100}?)(?:号)?"
                    r"(?=[，,。；;）)\]】\n]|$)",
                    text,
                    re.I,
                )
                if not matched:
                    continue
                value = re.sub(r"\s+", "", matched.group(1)).strip("：:；;,，")
                if valid_identifier(value):
                    return value
            return ""

        project_number = first((
            "招标项目编号", "采购项目编号", "投资项目统一代码", "项目代码",
            r"(?<!招标)(?<!采购)项目编号",
        ))
        tender_number = first(("招标编号", "采购编号", "代理编号"))
        return project_number, tender_number

    @classmethod
    def _merge_plan_table_fields_local(
        cls, data: dict[str, Any], text: str
    ) -> None:
        """解析本站计划公告的四列表格文本，阻止相邻字段互相串入。"""

        label_to_field = {
            "项目类型": "项目类型",
            "项目总投资": "项目总投资",
            "招标内容": "招标内容",
            "招标方式": "招标方式",
            "招标人名称": "招标人名称",
            "行政监督部门": "行政监督部门",
        }
        for line in str(text or "").splitlines():
            cells = [cell.strip().strip("：:") for cell in line.split("|")]
            cells = [cell for cell in cells if cell]
            if len(cells) < 2:
                continue
            for index in range(0, len(cells) - 1, 2):
                field = label_to_field.get(cells[index])
                if field and cells[index + 1]:
                    data[field] = cells[index + 1]

        scale = cls._section(
            text,
            ("建设内容及规模",),
            ("招标公告（资格预审公告）预计发布时间", "预计发布时间"),
        )
        if scale:
            data["建设内容及规模"] = scale.lstrip("|丨 ")

    @classmethod
    def _merge_candidate_period_local(
        cls, data: dict[str, Any], text: str
    ) -> None:
        """兼容只公布单日的“公示时间”，不虚构结束时刻。"""

        if data.get("公示时间"):
            return
        matched = re.search(
            r"公示(?:时间|期|期限)\s*[：:]\s*"
            r"(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}(?:日)?"
            r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
            text,
        )
        if matched:
            data["公示时间"] = cls._time(matched.group(1))

    @classmethod
    def _merge_award_execution_fields_local(
        cls, data: dict[str, Any], raw_html: str, text: str
    ) -> None:
        """清理签章占位符，并补齐结果表中的工期和证书编号。"""

        manager = str(data.get("项目经理") or "").strip()
        if re.search(r"_{3,}|签名|签章|盖章", manager) or re.fullmatch(
            r"\d{4}年\d{1,2}月\d{1,2}日", manager
        ):
            data["项目经理"] = ""

        if not data.get("工期"):
            rows = cls._table_rows(raw_html)
            for index, row in enumerate(rows[:-1]):
                normalized = [re.sub(r"\s+", "", cell) for cell in row]
                period_index = next(
                    (
                        pos
                        for pos, value in enumerate(normalized)
                        if re.fullmatch(r"(?:计划)?工期|服务期|服务期限|供货期|交货期", value)
                    ),
                    -1,
                )
                if period_index >= 0 and len(rows[index + 1]) > period_index:
                    data["工期"] = rows[index + 1][period_index].strip()
                    break

        certificate_match = re.search(
            r"(?:相关)?证书名称及编号\s*[：:]\s*([^\n；;]{2,240})",
            text,
        )
        if not certificate_match:
            return
        value = certificate_match.group(1).strip()
        code_match = re.search(
            r"(?:、|,|，|；|;)\s*"
            r"([A-Za-z\u4e00-\u9fff]{0,5}\d[A-Za-z0-9._/\-]{5,})\s*$",
            value,
        )
        if not code_match:
            return
        code = code_match.group(1)
        name = value[: code_match.start()].rstrip("、,，；; ")
        if name:
            data["项目经理证书名称"] = name
        data["项目经理证书编号"] = code

    @classmethod
    def _correction_content_local(cls, text: str) -> str:
        """保留实际变更/终止事项，排除标题、媒介和主体联系方式模板。"""

        source = str(text or "").strip()
        if not source:
            return ""
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        if not lines:
            return ""

        start = -1
        reason_re = re.compile(
            r"(?:招标|采购|项目)?(?:暂停/)?(?:终止|废标|流标|撤销)原因\s*[：:]?"
        )
        for index, line in enumerate(lines):
            if reason_re.search(line):
                start = index
                break
        if start < 0:
            for index, line in enumerate(lines):
                if re.fullmatch(r"(?:一|1)\s*[、.．）)]?\s*内容\s*[：:]?", line):
                    start = index + 1
                    break
        if start < 0:
            for index, line in enumerate(lines):
                if re.search(r"(?:需变更|现变更|更正内容|变更内容|流标|废标)", line):
                    start = index
                    break
        if start < 0:
            start = 1 if len(lines) > 1 else 0

        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if re.match(
                r"^(?:[二三四五六七八九十]|[2-9])\s*[、.．）)]\s*"
                r"(?:监督部门|联系方式|其他公告内容|发布公告的媒介)",
                line,
            ) or re.match(r"^(?:监督部门|联系方式)\s*[：:]?$", line):
                end = index
                break
        selected = "\n".join(lines[start:end]).strip()
        return selected or source

    @classmethod
    def _merge_tender_sections_local(
        cls,
        data: dict[str, Any],
        notice_type: str,
        text: str,
    ) -> None:
        """兼容本站工程招标和企业采购两套正文标题。

        公共解析器只识别“投标人资格要求”等常见标题。本站还大量使用
        “投标人资质要求”“申请人的资格要求”和“采购需求”；如果不把这些
        标题作为章节边界，招标范围会一直吞到正文末尾，资格要求则会漏取。
        """

        scope_key = (
            "项目概况与招标范围"
            if notice_type == "资格预审公告"
            else "招标内容与范围"
        )
        qualification_starts = (
            "投标人资质要求", "投标人资格要求", "供应商资格要求",
            "投标人的资格要求", "申请人的资格要求", "申请人资格要求",
            "供应商资格条件", "参与询比的供应商应具备的资格条件",
            "报价人资格", "响应人资格",
        )
        # 完整“项目概况/内容与范围”章节中经常包含横向标段表，表头本身
        # 也会出现“招标范围、供货地点、合同履行期限”。这类章节只能在
        # 下一业务章节处结束，不能把表头误当边界。
        scope = cls._section(
            text,
            (
                "项目概况与招标范围", "项目概况和招标范围",
                "项目概况与采购范围", "招标内容与招标范围",
                "采购范围及相关要求", "招标内容与范围",
            ),
            qualification_starts,
        )
        if not scope:
            # 没有完整章节标题时，按“采购内容/需求”等字段级标题提取，
            # 此时地点、期限和金额是可靠边界。
            scope = cls._section(
                text,
                ("采购内容及范围", "采购需求", "采购范围", "采购内容", "招标范围"),
                qualification_starts + (
                    "合同履行期限", "服务期限", "服务期",
                    "计划工期", "交货期", "供货期", "本次采购工程费",
                    "本次招标工程费", "投资预算", "预算金额", "最高限价",
                    "项目地点", "工程地点", "实施地点", "服务地点",
                    "招标文件的获取", "采购文件的获取",
                ),
            )
        qualification = cls._section(
            text,
            qualification_starts,
            (
                "招标文件的获取", "采购文件的获取", "询比采购文件的获取",
                "谈判采购文件的获取", "磋商采购文件的获取",
                "询价采购文件的获取", "资格预审文件的获取",
                "投标文件的递交", "响应文件的递交", "报价文件的递交",
                "获取询比采购文件", "获取采购文件", "获取招标文件",
                "招标文件的下载", "招标文件下载", "采购文件的下载",
                "采购文件下载", "招标文件获取", "采购文件获取",
                "谈判文件的获取", "谈判文件获取",
                "询比文件的获取", "询比文件获取", "询比文件发售",
                "采购文件发售", "招标文件发售",
            ),
        )
        if scope:
            data[scope_key] = scope
        if qualification:
            data["申请人资格要求/投标人资格要求"] = qualification

    @classmethod
    def _merge_party_fields(
        cls, data: dict[str, Any], notice_type: str, text: str
    ) -> None:
        """按HTML段落边界解析联系块，禁止采购人与代理机构字段串位。"""

        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        starts: list[tuple[int, str, str]] = []
        owner_pattern = re.compile(
            r"^(?:招\s*标\s*人|采\s*购\s*人|采\s*购\s*单位)"
            r"(?!\s*或)(?:名称)?\s*[：:]\s*(.+)$"
        )
        agency_pattern = re.compile(
            r"^(?:招\s*标\s*代理(?:机构)?|采\s*购\s*代理(?:机构)?|"
            r"招标代理机构|采购代理机构|代理机构)(?:名称)?\s*[：:]\s*(.+)$"
        )
        for index, line in enumerate(lines):
            owner_match = owner_pattern.match(line)
            agency_match = agency_pattern.match(line)
            if owner_match:
                starts.append((index, "owner", owner_match.group(1).strip()))
            elif agency_match:
                starts.append((index, "agency", agency_match.group(1).strip()))
        # 部分企业采购模板在“联系方式”下简写为“名称/代理机构”，只有在该
        # 章节内才赋予主体语义，避免把正文其他“名称”字段误当采购人。
        contact_heading = max(
            (index for index, line in enumerate(lines) if re.fullmatch(r"\d*\s*联系方式", line)),
            default=-1,
        )
        if contact_heading >= 0:
            for index in range(contact_heading + 1, len(lines)):
                line = lines[index]
                owner_short = re.match(r"^名称\s*[：:]\s*(.+)$", line)
                agency_short = re.match(
                    r"^(?:代理机构|招标代理|采购代理)\s*[：:]\s*(.+)$", line
                )
                if owner_short:
                    starts.append((index, "owner", owner_short.group(1).strip()))
                elif agency_short:
                    starts.append((index, "agency", agency_short.group(1).strip()))
        starts.sort(key=lambda item: item[0])
        parsed: dict[str, dict[str, str]] = {}
        for position, (start, kind, name) in enumerate(starts):
            end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
            block = lines[start + 1 : end]
            fields = {
                "name": name,
                "address": "",
                "contact": "",
                "phone": "",
                "email": "",
            }
            for line in block:
                if re.match(r"^(?:监督部门|提出异议|招标人或其|采购人或其)", line):
                    break
                for key, label in (
                    ("address", r"(?:联系)?地\s*址"),
                    ("contact", r"(?:项目)?联\s*系\s*人"),
                    ("phone", r"(?:联系电话|联系方式|电\s*话|手\s*机)"),
                    ("email", r"(?:电子邮件|电子邮箱|邮\s*箱)"),
                ):
                    matched = re.match(rf"^{label}\s*[：:]\s*(.+)$", line)
                    if matched and not fields[key]:
                        fields[key] = matched.group(1).strip()
            # 联系方式区最后一次出现的显式主体块优先。即使招标人块只给名称
            # 而没有联系人，也要记录这个“明确为空”，否则公共解析器可能把
            # 紧随其后的代理机构联系人错误回填给招标人。
            if fields["name"]:
                parsed[kind] = fields

        owner_name_key = (
            "招标人/采购人名称"
            if notice_type in {"招标公告", "资格预审公告"}
            else "招标人/采购人"
        )
        if "owner" in parsed:
            owner = parsed["owner"]
            owner_method = "；".join(
                dict.fromkeys(filter(None, (owner["phone"], owner["email"])))
            )
            for key, value in (
                (owner_name_key, owner["name"]),
                ("招标人地址", owner["address"]),
                ("招标人联系人", owner["contact"]),
                ("招标人联系方式", owner_method),
            ):
                if key in data:
                    data[key] = value
        if "agency" in parsed:
            agency = parsed["agency"]
            agency_method = "；".join(
                dict.fromkeys(filter(None, (agency["phone"], agency["email"])))
            )
            for key, value in (
                ("招标代理机构", agency["name"]),
                ("招标代理机构地址", agency["address"]),
                ("招标代理机构联系人", agency["contact"]),
                ("招标代理机构联系方式", agency_method),
            ):
                if key in data:
                    data[key] = value

    @staticmethod
    def _correction_nature(title: str, fallback: str) -> str:
        for value in (
            "重新招标公告", "终止公告", "撤销公告", "废标公告", "流标公告",
            "延期公告", "澄清公告", "变更公告", "更正公告",
        ):
            if value[:-2] in str(title or ""):
                return value
        # 字段标准只允许固定枚举；“暂停/补充/控制价/其他公告”保留在标题、
        # 正文和源站分类中，公共类型统一为“其他”，不得创造新枚举值。
        return "其他"

    @staticmethod
    def _table_rows(raw_html: str) -> list[list[str]]:
        soup = BeautifulSoup(raw_html or "", "html.parser")
        result: list[list[str]] = []
        for row in soup.select("table tr"):
            cells = [
                re.sub(r"\s+", " ", cell.get_text("", strip=True)).strip()
                for cell in row.select(":scope > th, :scope > td")
            ]
            if cells:
                result.append(cells)
        return result

    @classmethod
    def _candidate_details_local(
        cls, raw_html: str, text: str
    ) -> list[dict[str, str]]:
        rows = cls._table_rows(raw_html)
        result: list[dict[str, str]] = []
        for index, row in enumerate(rows):
            normalized = [re.sub(r"\s+", "", cell) for cell in row]
            name_index = next(
                (
                    pos for pos, value in enumerate(normalized)
                    if re.fullmatch(
                        r"(?:(?:中标|成交)?候选(?:人|单位|供应商|服务商)|"
                        r"供应商|投标人|报价人|响应人)(?:名称)?",
                        value,
                    )
                ),
                -1,
            )
            price_index = next(
                (
                    pos for pos, value in enumerate(normalized)
                    if re.search(r"(?:投标|响应|成交)?(?:报价|价格|金额)", value)
                ),
                -1,
            )
            if name_index < 0:
                continue
            for values in rows[index + 1 :]:
                required_index = max(name_index, price_index)
                if len(values) <= required_index:
                    break
                name = values[name_index].strip().lstrip("|丨").strip()
                price = values[price_index].strip() if price_index >= 0 else ""
                if not name or re.fullmatch(
                    r"(?:(?:中标|成交)?候选(?:人|单位|供应商|服务商)|"
                    r"供应商|投标人|报价人|响应人)(?:名称)?",
                    re.sub(r"\s+", "", name),
                ):
                    break
                detail = {
                    "标段": "", "候选人名称": name, "候选人报价": price
                }
                if detail not in result:
                    result.append(detail)
            if result:
                return result

        marker = re.compile(
            r"第\s*[一二三四五六七八九十\d]+\s*(?:名|(?:中标|成交)?候选"
            r"(?:中标人|成交供应商|成交服务商|人|供应商|服务商))"
            r"\s*[：:]\s*([^\n|]{2,200})"
        )
        matches = list(marker.finditer(text))
        for position, match in enumerate(matches):
            end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
            block = text[match.end() : end]
            price_match = re.search(
                r"(?:投标|响应|成交)?(?:报价|价格|金额)\s*[：:]\s*"
                r"([^\n|；;]{1,100})",
                block,
            )
            name = match.group(1).strip(" ：:；;")
            price = price_match.group(1).strip(" ：:；;") if price_match else ""
            if name:
                result.append({"标段": "", "候选人名称": name, "候选人报价": price})
        return result

    @classmethod
    def _award_details_local(
        cls, raw_html: str, text: str
    ) -> list[dict[str, str]]:
        rows = cls._table_rows(raw_html)
        # 纵向键值表：中标人名称|甲公司；中标价格|100万元。
        vertical: dict[str, str] = {}
        for row in rows:
            if len(row) == 2:
                vertical[re.sub(r"\s+", "", row[0])] = row[1].strip()
        vertical_name = next(
            (
                value for key, value in vertical.items()
                if re.fullmatch(
                    r"(?:中标|成交)(?:人|单位|供应商|服务商)(?:名称)?"
                    r"(?:（[^）]*）|\([^)]*\))?",
                    key,
                )
            ),
            "",
        )
        vertical_price = next(
            (
                value for key, value in vertical.items()
                if re.fullmatch(
                    r"(?:中标|成交)(?:报价|价格|价|金额)"
                    r"(?:（[^）]*）|\([^)]*\))?",
                    key,
                )
            ),
            "",
        )
        if vertical_name:
            return [{"标段": "", "中标人名称": vertical_name, "中标价": vertical_price}]

        # 横向表头：成交人名称|成交价格|工期；下一行为对应值。
        for index, row in enumerate(rows):
            normalized = [re.sub(r"\s+", "", cell) for cell in row]
            name_index = next(
                (
                    pos for pos, value in enumerate(normalized)
                    if re.fullmatch(r"(?:中标|成交)(?:人|单位|供应商|服务商)(?:名称)?", value)
                ),
                -1,
            )
            price_index = next(
                (
                    pos for pos, value in enumerate(normalized)
                    if re.fullmatch(r"(?:中标|成交)(?:报价|价格|价|金额)", value)
                ),
                -1,
            )
            if name_index >= 0 and index + 1 < len(rows):
                values = rows[index + 1]
                if len(values) > name_index:
                    return [{
                        "标段": "",
                        "中标人名称": values[name_index].strip(),
                        "中标价": (
                            values[price_index].strip()
                            if price_index >= 0 and len(values) > price_index
                            else ""
                        ),
                    }]

        name_match = re.search(
            r"(?:中\s*标|成\s*交)\s*(?:人|单位|供应商|服务商)(?:名称)?"
            r"\s*[：:]\s*"
            r"([^\n|]{2,200})",
            text,
        )
        price_match = re.search(
            r"(?:中\s*标|成\s*交)\s*(?:报价|价格|价|金额)"
            r"(?:（[^）]*）|\([^)]*\))?\s*[：:]\s*"
            r"([^\n|；;]{1,100})",
            text,
        )
        if not name_match:
            return []
        return [{
            "标段": "",
            "中标人名称": name_match.group(1).strip(" ：:；;"),
            "中标价": price_match.group(1).strip(" ：:；;") if price_match else "",
        }]

    @staticmethod
    def _time(value: Any) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = int(value)
            if number > 10_000_000_000:
                number //= 1000
            return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
        return str(value).replace("T", " ").removesuffix("Z").split(".", 1)[0].strip()

    @classmethod
    def _range(cls, start: Any, end: Any) -> str:
        values = [cls._time(value) for value in (start, end) if value not in (None, "")]
        return " 至 ".join(value for value in values if value)

    @staticmethod
    def _attachments(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            values: Sequence[Any] = [value]
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            values = value
        else:
            values = []
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in values:
            if not isinstance(item, Mapping):
                continue
            file_url = str(
                item.get("fileFullPath")
                or item.get("filePath")
                or item.get("downloadUrl")
                or item.get("url")
                or ""
            ).strip()
            if not file_url:
                continue
            file_url = urljoin(config.BASE_URL, file_url)
            file_name = str(
                item.get("fileName")
                or item.get("name")
                or file_url.rsplit("/", 1)[-1]
                or "附件"
            ).strip()
            identity = (file_name, file_url)
            if identity in seen:
                continue
            seen.add(identity)
            result.append({
                "source_file_id": str(
                    item.get("id") or item.get("fileId") or item.get("resourceId") or ""
                ).strip(),
                "file_name": file_name,
                "file_url": file_url,
                "file_hash": str(item.get("fileMd5") or item.get("md5") or "").strip(),
                "file_size_bytes": item.get("fileSize") or item.get("size"),
                "file_type": str(item.get("fileType") or "").strip(),
                "parse_status": "PENDING",
            })
        return result
