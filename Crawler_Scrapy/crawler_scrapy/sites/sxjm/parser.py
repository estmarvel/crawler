"""山西焦煤接口解密与本站专用公告字段解析。"""

from __future__ import annotations

import base64
import json
import re
from html import unescape
from typing import Any, Mapping
from urllib.parse import urljoin

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from lxml import html as lxml_html

from crawler_scrapy.schemas.notice_fields import create_empty_notice_data
from crawler_scrapy.sites.sxjm import config


class SxjmResponseError(ValueError):
    """接口返回失败或加密载荷不合法。"""


def decrypt_envelope(payload: Mapping[str, Any]) -> Any:
    """解开网站前端使用的 AES-128-CBC 响应载荷。"""

    if payload.get("errcode") not in (0, "0", None):
        raise SxjmResponseError(str(payload.get("errmsg") or "接口返回失败"))
    encrypted = payload.get("result")
    if not isinstance(encrypted, str) or not encrypted.strip():
        raise SxjmResponseError("接口响应缺少加密 result")
    try:
        ciphertext = base64.b64decode(encrypted)
        plaintext = unpad(
            AES.new(config.AES_KEY, AES.MODE_CBC, config.AES_IV).decrypt(ciphertext),
            AES.block_size,
        )
        return json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SxjmResponseError("山西焦煤接口响应解密失败") from exc


def clean_html_keep_lines(value: Any) -> str:
    """按 DOM 文本节点保留段落和表格单元格边界。"""

    source = str(value or "").strip()
    if not source:
        return ""
    try:
        root = lxml_html.fragment_fromstring(source, create_parent="div")
        parts: list[str] = []
        # Word/TinyMCE 正文会把一个中文标签拆进多个 span；按段落拼接 span，
        # 表格按单元格保留边界。使用 XPath union 按 DOM 顺序遍历，避免旧逻辑
        # 把全文所有表格搬到正文开头，破坏“标段标题—结果表”的对应关系。
        nodes = root.xpath(
            ".//tr[not(.//tr)] | "
            ".//p[not(ancestor::tr)] | "
            ".//li[not(ancestor::tr)] | "
            ".//h1[not(ancestor::tr)] | .//h2[not(ancestor::tr)] | "
            ".//h3[not(ancestor::tr)] | .//h4[not(ancestor::tr)] | "
            ".//h5[not(ancestor::tr)] | .//h6[not(ancestor::tr)]"
        )
        for node in nodes:
            if str(node.tag).lower() == "tr":
                cells = node.xpath("./th|./td")
                parts.extend("".join(cell.itertext()) for cell in cells)
            else:
                parts.append("".join(node.itertext()))
        if not parts:
            parts = ["".join(root.itertext())]
    except (ValueError, TypeError):
        parts = re.sub(r"<[^>]+>", "\n", source).splitlines()
    lines: list[str] = []
    for part in parts:
        text = re.sub(r"[\u00a0\u2002\u2003\u3000\t ]+", " ", unescape(str(part)))
        for line in text.replace("\r", "\n").split("\n"):
            normalized = line.strip()
            if normalized:
                lines.append(normalized)
    return "\n".join(lines)


class SxjmParser:
    parser_version = "sxjm-v12-unique-identifiers"

    """完全按山西焦煤详情接口和正文模板解析公告。"""

    PROJECT_TYPES = {"10": "货物", "20": "工程", "30": "服务"}

    @classmethod
    def parse(
        cls, channel: str, section: str, detail: Mapping[str, Any]
    ) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
        # 返回真实源站栏目，而不是把 cggg/cjhxr/cjgg 改名成
        # zbgg/hxr/zbjg。字段结构可以复用，公告性质必须保持可区分。
        subtype = section
        notice_type = config.schema_notice_type(section)
        data = create_empty_notice_data(
            notice_type, include_parser_diagnostics=True
        )
        text = cls.raw_text(detail)
        cls._fill_common(channel, section, data, detail, text)
        if section == "zbjh":
            cls._fill_plan(data, detail, text)
        elif section in {"zbgg", "cggg", "zzgg"}:
            cls._fill_tender(data, detail, text)
        elif section in {"hxr", "cjhxr"}:
            cls._fill_candidates(data, detail, text)
        else:
            cls._fill_award(data, detail, text)
        attachments = cls.attachments(detail)
        return subtype, notice_type, data, attachments

    @classmethod
    def _fill_common(
        cls,
        channel: str,
        section: str,
        data: dict[str, Any],
        detail: Mapping[str, Any],
        text: str,
    ) -> None:
        title = cls._string(detail.get("title") or detail.get("project_name"))
        publish_time = cls._valid_time(
            detail.get("publish_time_format"), detail.get("created_at_format")
        )
        common = {
            "项目性质": config.channel_label(channel),
            "源站公告性质": cls.source_notice_nature(
                channel,
                section,
                detail.get("_crawler_announcement_type")
                or detail.get("announcement_type"),
                title,
            ),
            "项目名称": cls._project_name(detail, title),
            "项目编号": cls._project_identifier(detail, text),
            "招标编号": cls._tender_identifier(detail, text),
            "所属行业": cls._string(detail.get("industry_category")),
            "组织形式": cls._organization(detail, text),
            "发布日期": publish_time,
            "发布网站": config.PLATFORM_NAME,
        }
        for field, value in common.items():
            if field in data:
                data[field] = value

    @classmethod
    def _fill_plan(
        cls, data: dict[str, Any], detail: Mapping[str, Any], text: str
    ) -> None:
        values = {
            "招标方式": detail.get("tender_mode") or cls._label(text, "招标方式"),
            "项目名称": cls._label(text, "项目名称") or cls._project_name(
                detail, cls._string(detail.get("project_name") or detail.get("title"))
            ),
            "项目类型": detail.get("type_project") or cls._label(text, "项目类型"),
            "项目总投资": detail.get("contribution_scale") or cls._label(text, "项目总投资"),
            "招标内容": detail.get("tender_content") or cls._label(text, "招标内容"),
            "招标人名称": detail.get("legal_person") or cls._label(text, "招标人名称", "招标人"),
            "行政监督部门": detail.get("supervise_dept_name") or cls._label(text, "行政监督部门"),
            "建设地点": detail.get("project_address") or cls._label(text, "建设地点"),
            "建设内容及规模": detail.get("project_scale") or cls._label(text, "建设内容及规模"),
            "招标公告（资格预审公告）预计发布时间": (
                detail.get("notice_plan_send_time")
                or cls._label(text, "招标公告（资格预审公告）预计发布时间", "招标公告预计发布时间")
            ),
        }
        for field, value in values.items():
            data[field] = cls._string(value).strip(" ;；")

    @classmethod
    def _fill_tender(
        cls, data: dict[str, Any], detail: Mapping[str, Any], text: str
    ) -> None:
        project_type = cls.PROJECT_TYPES.get(cls._string(detail.get("project_type")), "")
        opening = cls._string(detail.get("bid_opening_date_format"))
        acquisition = cls._range(
            detail.get("sale_begin_time_format"), detail.get("sale_end_time_format")
        )
        scope = cls._section(
            text,
            ("采购范围及相关要求", "采购范围", "招标范围", "项目概况与招标范围"),
            (
                "供应商资格要求", "供应商资质要求", "投标人资格要求",
                "投标人资质要求", "申请人资格要求", "采购文件的获取", "招标文件的获取",
            ),
        )
        qualification = cls._section(
            text,
            ("供应商资格要求", "供应商资质要求", "投标人资格要求", "申请人资格要求"),
            (
                "采购文件的获取", "招标文件的获取", "获取时间", "获取方式",
                "响应文件的递交", "投标文件的递交",
            ),
        )
        values = {
            "开标时间": opening,
            "项目编号/招标编号": cls._project_number(detail, text),
            "项目类型/行业分类": project_type or detail.get("industry_category"),
            "项目总投资/估算金额": cls._label(text, "项目总投资", "估算金额", "项目估算"),
            "招标金额": cls._label(text, "招标金额", "最高限价", "最高投标限价", "采购预算"),
            "资金来源": cls._funding(text),
            "项目地点": cls._label(
                text, "项目地点", "建设地点", "服务地点", "交货地点", "实施地点"
            ) or detail.get("region"),
            "招标人/采购人名称": cls._purchaser(detail, text),
            "项目规模": cls._label(text, "项目规模", "建设规模", "采购项目概况", "项目概况"),
            "工期/服务期/供货日期": cls._label(
                text, "合同履行期限", "履约期限", "工期", "计划工期",
                "项目服务期限", "服务期限", "服务周期", "服务期",
                "供货期限", "供货期", "交货期限", "交货期"
            ),
            "质量要求": cls._label(
                text, "质量要求或服务标准", "质量要求", "质量标准", "服务标准", "其他要求"
            ),
            "招标内容与范围": scope,
            "申请人资格要求/投标人资格要求": qualification,
            "预审文件获取时间": acquisition,
            "获取方式": cls._section(
                text,
                ("采购文件的获取", "招标文件的获取", "文件的获取"),
                ("响应文件的递交", "投标文件的递交", "递交响应文件", "开启方式和地点", "开标时间和地点"),
            ) or cls._label(text, "获取方式", "获取方法"),
            "递交截止时间": opening or cls._label(text, "递交截止时间", "响应文件递交截止时间"),
            "递交方法": cls._label(text, "递交方法", "递交方式", "递交地址", "递交地点"),
            "开启时间": opening,
            "开启方式": cls._opening_method(text),
            "开启地点": cls._opening_place(text),
            "评审办法": cls._label(text, "评审办法", "评标办法"),
            "投标保证金方式": cls._label(
                text, "投标保证金方式", "投标保证金", "保证金"
            ) or cls._section(
                text,
                ("提交投标保证金的形式",),
                ("提出异议的渠道和方式", "提出异议"),
            ),
        }
        for field, value in values.items():
            if field in data:
                data[field] = cls._string(value)
        cls._fill_contacts(data, detail, text)

    @classmethod
    def _fill_candidates(
        cls, data: dict[str, Any], detail: Mapping[str, Any], text: str
    ) -> None:
        details = cls._table_results(
            text, candidate=True, source_html=cls.raw_html(detail)
        )
        data["开标时间"] = cls._string(detail.get("bid_opening_date_format"))
        data["公示时间"] = cls._range(
            detail.get("sale_begin_time_format"), detail.get("sale_end_time_format")
        ) or cls._label(text, "公示时间", "公示期")
        data["招标编号/项目编号"] = cls._project_number(detail, text)
        data["中标候选人明细"] = details
        data["中标候选人名称"] = [item["候选人名称"] for item in details]
        prices = [item["候选人报价"] for item in details]
        data["中标候选人报价"] = (
            prices if any(value not in (None, "") for value in prices) else []
        )
        cls._fill_contacts(data, detail, text)

    @classmethod
    def _fill_award(
        cls, data: dict[str, Any], detail: Mapping[str, Any], text: str
    ) -> None:
        details = cls._table_results(
            text, candidate=False, source_html=cls.raw_html(detail)
        )
        data["招标方式"] = cls._procurement_method(detail, text)
        data["中标结果明细"] = details
        data["中标人名称"] = [item["中标人名称"] for item in details]
        prices = [item["中标价"] for item in details]
        data["中标价"] = (
            prices if any(value not in (None, "") for value in prices) else []
        )
        data["联合体成员"] = cls._string_list(
            cls._label(text, "联合体成员", "联合体成员名称")
        )
        data["工期"] = cls._label(text, "工期", "服务期", "供货期", "交货期")
        data["项目经理"] = cls._result_project_manager(text)
        data["项目经理证书名称"] = cls._label(
            text, "项目经理证书名称", "项目负责人证书名称", "证书名称"
        )
        data["项目经理证书编号"] = cls._label(
            text, "项目经理证书编号", "项目负责人证书编号", "证书编号"
        )
        data["依据文件"] = cls._label(text, "依据文件")
        data["依据文号"] = cls._label(text, "依据文号")
        cls._fill_contacts(data, detail, text)

    @classmethod
    def _fill_contacts(
        cls, data: dict[str, Any], detail: Mapping[str, Any], text: str
    ) -> None:
        contacts = cls._contacts(text)
        bidder_name = contacts["bidder"].get("name") or cls._purchaser(detail, text)
        agent_name = contacts["agent"].get("name") or cls._string(detail.get("tendering_agency"))
        values = {
            "招标人/采购人名称": bidder_name,
            "招标人/采购人": bidder_name,
            "招标人地址": contacts["bidder"].get("address", ""),
            "招标人联系人": contacts["bidder"].get("contact", ""),
            "招标人联系方式": contacts["bidder"].get("phone", ""),
            "招标代理机构": agent_name,
            "招标代理机构地址": contacts["agent"].get("address", ""),
            "招标代理机构联系人": contacts["agent"].get("contact", ""),
            "招标代理机构联系方式": contacts["agent"].get("phone", ""),
        }
        for field, value in values.items():
            if field in data:
                data[field] = value

    @classmethod
    def _contacts(cls, text: str) -> dict[str, dict[str, str]]:
        result = {"bidder": {}, "agent": {}}
        current = ""
        for raw in text.splitlines():
            line = re.sub(r"\s+", "", raw).strip("：:；;")
            if ("或其" in line and "代理机构" in line) or "签章" in line or "签名" in line:
                continue
            # 部分终止公告把招标人、地址、联系人、电话、代理机构全部写在同一段。
            # 先按本站联系方式标签切开，再使用状态机归属字段。
            segments = re.split(
                r"(?=(?:招标人|采购人|采购单位|招标单位|招标代理机构|"
                r"采购代理机构|代理机构|地址|联系人|联系电话|电话)[：:])",
                line,
            )
            for segment in segments:
                segment = segment.strip("：:；;")
                entity = re.search(
                    r"(?:^|联系方式)(招标人|采购人|采购单位|招标单位)[：:]?(.*)$",
                    segment,
                )
                agent = re.match(
                    r"^(招标代理机构|采购代理机构|代理机构)[：:]?(.*)$", segment
                )
                if entity:
                    current = "bidder"
                    if entity.group(2):
                        result[current]["name"] = entity.group(2).strip(" 。；;")
                    continue
                if agent:
                    current = "agent"
                    if agent.group(2):
                        result[current]["name"] = agent.group(2).strip(" 。；;")
                    continue
                if not current:
                    continue
                patterns = {
                    "address": r"^地址[：:]?(.*)$",
                    "contact": r"^联系人[：:]?(.*)$",
                    "phone": r"^(?:联系电话|电话)[：:]?(.*)$",
                }
                for key, pattern in patterns.items():
                    match = re.match(pattern, segment)
                    if match and match.group(1):
                        result[current][key] = match.group(1).strip(" 。；;")
                        break
        return result

    @classmethod
    def _table_results(
        cls, text: str, *, candidate: bool, source_html: str = ""
    ) -> list[dict[str, Any]]:
        html_results = cls._html_table_results(
            source_html, text=text, candidate=candidate
        )
        if html_results:
            return html_results
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
        name_words = (
            "候选人名称", "候选供应商名称", "入围候选供应商名称",
            "入围供应商名称",
        ) if candidate else (
            "中标人名称", "成交人名称", "成交(入围)人名称", "成交（入围）人名称",
            "成交单位", "成交供应商名称", "供应商名称"
        )
        section = ""
        details: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        in_table = False
        for index, line in enumerate(lines):
            if "标段名称" in line:
                section = re.split(r"[：:]", line, maxsplit=1)[-1].strip()
            if any(word in line for word in name_words):
                in_table = True
                continue
            if in_table and re.match(r"^[一二三四五六七八九十]+[、.]", line):
                in_table = False
            if not in_table or not re.fullmatch(r"\d{1,3}", line) or index + 1 >= len(lines):
                continue
            name = lines[index + 1].strip()
            if not cls._company_like(name) or (section, name) in seen:
                continue
            price = ""
            if index + 2 < len(lines) and cls._amount_like(lines[index + 2]):
                possible = lines[index + 2].strip()
                next_is_company = index + 3 < len(lines) and cls._company_like(lines[index + 3])
                # 无报价列的表格通常是“1 公司A 2 公司B”，不能把下一排名当报价。
                if not (re.fullmatch(r"\d{1,3}", possible) and next_is_company):
                    price = possible
            seen.add((section, name))
            if candidate:
                details.append({"标段": section, "候选人名称": name, "候选人报价": price})
            else:
                details.append({"标段": section, "中标人名称": name, "中标价": price})
        return details

    @classmethod
    def _html_table_results(
        cls, source_html: str, *, text: str, candidate: bool
    ) -> list[dict[str, Any]]:
        """按本站详情正文中的原始表格列提取候选人/成交人及报价。"""

        if not source_html.strip():
            return []
        try:
            root = lxml_html.fragment_fromstring(source_html, create_parent="div")
        except (ValueError, TypeError):
            return []
        name_words = (
            "候选人名称", "候选供应商名称", "入围候选供应商名称",
            "入围供应商名称",
        ) if candidate else (
            "中标人名称", "成交人名称", "成交(入围)人名称", "成交（入围）人名称",
            "成交单位", "成交供应商名称", "供应商名称"
        )
        price_words = (
            "报价", "投标价", "响应报价", "成交价", "中标价", "成交金额", "中标金额"
        )
        section_names: list[str] = []
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if "标段名称" in line or re.match(r"^标段\s*\d+\s*[：:]", line):
                section_names.append(
                    re.split(r"[：:]", line, maxsplit=1)[-1].strip()
                )
                continue
            if re.match(
                r"^\d{3}\s*第?[一二三四五六七八九十百\d]+标段\s*[：:]",
                line,
            ):
                section_names.append(line)
        groups: list[list[tuple[str, str]]] = []
        # 富文本编辑器常生成多层嵌套 table，只处理最内层实际数据表，避免重复。
        for table in root.xpath(".//table[not(.//table)]"):
            rows = []
            for row in table.xpath(".//tr"):
                cells = [
                    re.sub(r"\s+", " ", "".join(cell.itertext())).strip()
                    for cell in row.xpath("./th|./td")
                ]
                if cells:
                    rows.append(cells)
            header_index = next(
                (
                    index
                    for index, row in enumerate(rows)
                    if any(any(word in cell for word in name_words) for cell in row)
                ),
                -1,
            )
            if header_index < 0:
                continue
            header = rows[header_index]
            name_index = next(
                i for i, cell in enumerate(header) if any(word in cell for word in name_words)
            )
            price_index = next(
                (i for i, cell in enumerate(header) if any(word in cell for word in price_words)),
                -1,
            )
            group: list[tuple[str, str]] = []
            for row in rows[header_index + 1 :]:
                if name_index >= len(row):
                    continue
                name = row[name_index].strip()
                if not cls._company_like(name):
                    continue
                price = row[price_index].strip() if 0 <= price_index < len(row) else ""
                group.append((name, price))
            if group:
                groups.append(group)
        # 个别依法项目详情同时保留桌面/移动端的相同结果表。只有在表格组数
        # 多于正文标段数时才按整组去重，避免删除同一供应商在不同标段的合法结果。
        if len(groups) > len(section_names):
            unique_groups: list[list[tuple[str, str]]] = []
            seen_groups: set[tuple[tuple[str, str], ...]] = set()
            for group in groups:
                signature = tuple(group)
                if signature in seen_groups:
                    continue
                seen_groups.add(signature)
                unique_groups.append(group)
            # 响应式简表可能省略报价列；候选人名单相同时保留报价更完整的一组。
            best_by_names: dict[tuple[str, ...], list[tuple[str, str]]] = {}
            order: list[tuple[str, ...]] = []
            for group in unique_groups:
                names = tuple(name for name, _ in group)
                if names not in best_by_names:
                    order.append(names)
                    best_by_names[names] = group
                    continue
                old_score = sum(bool(price) for _, price in best_by_names[names])
                new_score = sum(bool(price) for _, price in group)
                if new_score > old_score:
                    best_by_names[names] = group
            groups = [best_by_names[names] for names in order]
        details: list[dict[str, Any]] = []
        for group_index, group in enumerate(groups):
            section = section_names[group_index] if group_index < len(section_names) else ""
            for name, price in group:
                if candidate:
                    details.append({"标段": section, "候选人名称": name, "候选人报价": price})
                else:
                    details.append({"标段": section, "中标人名称": name, "中标价": price})
        return details

    @staticmethod
    def _company_like(value: str) -> bool:
        if not value or re.search(
            r"^(?:[一二三四五六七八九十]+[、.]|排序|序号|响应|合格|无|"
            r"工期|质量|项目负责人|姓名|相关证书|其他公示内容|联系方式)",
            value,
        ):
            return False
        if re.fullmatch(r"[\d.,，。%元万元]+", value):
            return False
        return not re.search(
            r"(?:项目名称|供应商名称|候选人名称|中标人名称|成交人名称|"
            r"中标结果公示|成交结果公告|成交公告|结果发布)[）)]?$",
            value,
        )

    @staticmethod
    def _amount_like(value: str) -> bool:
        compact = value.replace(",", "").replace("，", "").strip()
        return bool(re.fullmatch(r"(?:人民币)?[¥￥]?\s*\d+(?:\.\d+)?(?:元|万元|%|人民币)?", compact))

    @classmethod
    def _label(cls, text: str, *labels: str) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        for index, line in enumerate(lines):
            normalized = re.sub(r"^[一二三四五六七八九十百\d.、（）()\s]+", "", line)
            for label in labels:
                # 本站富文本常把标签写成“工 期、联 系 人”等形式。
                # 允许标签每个字符之间出现空格，但不删除字段值中的空格。
                label_pattern = r"\s*".join(re.escape(char) for char in label)
                match = re.match(rf"{label_pattern}\s*[：:]\s*(.*)$", normalized)
                if not match:
                    continue
                value = match.group(1).strip(" ：:。；;")
                if value:
                    return value
                for following in lines[index + 1 : index + 3]:
                    candidate = following.strip(" ：:。；;")
                    if not candidate:
                        continue
                    compact = re.sub(r"\s+", "", following)
                    # 空标签后紧跟另一个字段标签时不能把后者当成当前字段值，
                    # 例如源站空模板“采购人：\n地址：\n联系人：”。
                    if re.fullmatch(
                        r"(?:招标人|采购人|采购单位|招标单位|招标代理机构|"
                        r"采购代理机构|代理机构|地址|联系人|联系电话|电话)[:：]?",
                        compact,
                    ):
                        break
                    return candidate
        return ""

    @staticmethod
    def source_notice_nature(
        channel: str,
        section: str,
        announcement_type: Any,
        title: str,
    ) -> str:
        """保留源栏目，同时识别被源站放错栏目或归入“其他”的公告性质。"""

        compact = re.sub(r"\s+", "", title)
        for keyword, nature in (
            ("撤销（终止）", "撤销（终止）公告"),
            ("撤销(终止)", "撤销（终止）公告"),
            ("终止", "终止公告"),
            ("撤销", "撤销公告"),
            ("延期", "延期公告"),
            ("变更", "变更公告"),
            ("更正", "更正公告"),
            ("资格预审", "资格预审公告"),
            ("补充", "补充公告"),
            ("澄清", "澄清公告"),
        ):
            if keyword in compact:
                return nature
        # 依法项目 type=8 是“招标（预审）及其他公告”的聚合类型；普通招标、
        # 采购以及变更等优先按标题细分，无法细分时保留接口聚合类型名称。
        if str(announcement_type or "") == "8":
            if "招标公告" in compact:
                return "招标公告"
            if "采购公告" in compact:
                return "采购公告"
            return config.announcement_type_label("8")
        return config.section_label(channel, section)

    @classmethod
    def _opening_method(cls, text: str) -> str:
        explicit = cls._label(text, "开启方式", "开标方式")
        if explicit:
            return explicit
        if re.search(r"在线等待(?:谈判)?通知|开标大厅在线等待|平台线上", text):
            return "线上开启"
        return ""

    @classmethod
    def _opening_place(cls, text: str) -> str:
        explicit = cls._label(text, "开启地点", "开标地点", "谈判地点")
        if explicit:
            return explicit
        if "山西焦煤电子招采平台" in text and re.search(
            r"在线等待(?:谈判)?通知|开标大厅在线等待|"
            r"(?:开标方式|开启方式)[^\n]*山西焦煤电子招采平台[^\n]*线上|平台线上",
            text,
        ):
            return "山西焦煤电子招采平台"
        return ""

    @classmethod
    def _result_project_manager(cls, text: str) -> str:
        """提取中标项目经理，排除代理机构公告签署人的落款。"""

        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line or "签名" in line or "签章" in line or "代理机构" in line:
                continue
            value = cls._label(line, "项目经理", "项目负责人")
            if value:
                return value
        return ""

    @staticmethod
    def _section(text: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        start = next((i for i, line in enumerate(lines) if any(x in line for x in starts)), -1)
        if start < 0:
            return ""
        selected: list[str] = []
        start_line = lines[start]
        for label in starts:
            match = re.search(rf"{re.escape(label)}\s*[：:]\s*(.*)$", start_line)
            if match and match.group(1).strip():
                selected.append(match.group(1).strip())
                break
        for line in lines[start + 1 :]:
            if selected and any(x in line for x in ends):
                break
            if line:
                selected.append(line)
        return "\n".join(selected).strip()

    @classmethod
    def _project_number(cls, detail: Mapping[str, Any], text: str) -> str:
        body = cls._label(text, "项目编号", "招标项目编号", "招标编号", "采购编号")
        return body or cls._string(detail.get("tender_number") or detail.get("code"))

    @classmethod
    def _project_identifier(cls, detail: Mapping[str, Any], text: str) -> str:
        """提取项目主标识，不把仅标注为“招标编号”的值误写为项目编号。"""

        body = cls._label(
            text,
            "项目编号",
            "招标项目编号",
            "投资项目统一代码",
            "项目代码",
        )
        return cls._identifier_token(body) or cls._identifier_token(
            detail.get("invest_project_code")
        )

    @classmethod
    def _tender_identifier(cls, detail: Mapping[str, Any], text: str) -> str:
        """提取招标/采购编号；接口 tender_number 只作为正文缺失时的回退。"""

        body = cls._label(text, "招标编号", "采购编号")
        return cls._identifier_token(body) or cls._identifier_token(
            detail.get("tender_number")
        )

    @staticmethod
    def _identifier_token(value: Any) -> str:
        match = re.search(
            r"[A-Za-z0-9][A-Za-z0-9._/()（）\[\]【】-]{2,190}",
            str(value or "").strip(),
        )
        if not match:
            return ""
        candidate = match.group(0).strip("._/-")
        # SXJM 的 tender_number 偶尔只是机构/业务前缀（如 SJZBXS、fxkygyhw），
        # 不具备公告级唯一性，不能用于数据库项目关联。完整编号至少包含数字。
        if not re.search(r"\d", candidate):
            return ""
        if not all(
            candidate.count(opening) == candidate.count(closing)
            for opening, closing in (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】"))
        ):
            return ""
        return candidate

    @classmethod
    def _project_name(cls, detail: Mapping[str, Any], title: str) -> str:
        value = cls._string(detail.get("project_name")) or title
        return re.sub(
            r"(?:招标计划(?:变更)?公告|二次招标公告|招标公告|采购(?:二次)?公告|项目公告|"
            r"中标候选人公示(?:更正)?|成交候选人公示|中标结果公示|成交结果公告|"
            r"成交公告|结果发布|招标终止公告|采购终止公告|终止公告)(?:（.*?）)?$",
            "",
            value,
        ).strip()

    @classmethod
    def _purchaser(cls, detail: Mapping[str, Any], text: str) -> str:
        contacts = cls._contacts(text)
        return contacts["bidder"].get("name") or cls._label(text, "招标人", "采购人") or (
            cls._string(detail.get("tenderer"))
            if any(x in cls._string(detail.get("tenderer")) for x in ("公司", "集团", "煤矿", "单位"))
            else ""
        )

    @classmethod
    def _funding(cls, text: str) -> str:
        value = cls._label(text, "资金来源", "项目资金来源")
        if value:
            return value
        match = re.search(r"资金来源(?:为|是|[：:])\s*([^，。；;\n]+)", text)
        return match.group(1).strip() if match else ""

    @classmethod
    def _organization(cls, detail: Mapping[str, Any], text: str) -> str:
        method = cls._string(detail.get("tendering_method"))
        body = cls._label(text, "组织形式")
        if body:
            return body
        if "自行" in method:
            return "自行招标"
        if "委托" in method:
            return "委托招标"
        agency = cls._string(detail.get("tendering_agency"))
        return "委托招标" if any(x in agency for x in ("公司", "中心", "机构")) else ""

    @classmethod
    def _procurement_method(cls, detail: Mapping[str, Any], text: str) -> str:
        return cls._label(text, "招标方式", "采购方式", "采购方法") or cls._string(
            detail.get("tendering_method")
        )

    @staticmethod
    def _range(start: Any, end: Any) -> str:
        first, second = str(start or "").strip(), str(end or "").strip()
        return " 至 ".join(x for x in (first, second) if x)

    @staticmethod
    def _valid_time(primary: Any, fallback: Any) -> str:
        value = str(primary or "").strip()
        return str(fallback or "").strip() if value.startswith("1970-") or not value else value

    @staticmethod
    def _string(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [x.strip() for x in re.split(r"[、,，;；]", str(value or "")) if x.strip()]

    @staticmethod
    def attachments(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for document in detail.get("document") or []:
            if not isinstance(document, Mapping):
                continue
            path = str(document.get("path") or "").strip()
            result.append(
                {
                    "source_file_id": str(document.get("id") or "").strip(),
                    "file_name": str(document.get("original_name") or "").strip(),
                    "file_url": urljoin(f"{config.WEB_BASE_URL}/", path) if path else "",
                    "file_type": str(document.get("mime_type") or "").strip(),
                    "parse_status": "PENDING",
                }
            )
        return result

    @staticmethod
    def raw_html(detail: Mapping[str, Any]) -> str:
        return str(detail.get("content") or "")

    @classmethod
    def raw_text(cls, detail: Mapping[str, Any]) -> str:
        return clean_html_keep_lines(cls.raw_html(detail))
