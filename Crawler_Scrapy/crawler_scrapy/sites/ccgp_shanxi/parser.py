"""山西政府采购网详情 HTML 的站点专用解析器。"""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime
from html import unescape
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from crawler_scrapy.ai.html_extractor import html_to_text
from crawler_scrapy.sites.ccgp_shanxi import config


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or "")).replace("\xa0", " ")).strip(" ：:")


class CcgpShanxiParser:
    """规则优先解析；接口明确字段始终覆盖正文模糊匹配结果。"""

    @classmethod
    def parse(
        cls, feed: str, detail: Mapping[str, Any], list_record: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]], str, str]:
        notice_type, subtype, _ = config.FEEDS[feed]
        raw_html = str(detail.get("content") or "")
        raw_text = html_to_text(raw_html)
        if notice_type == "采购意向公开":
            data = cls._intention(detail, list_record, raw_html, raw_text)
        elif notice_type == "采购公告":
            data = cls._notice(detail, list_record, raw_html, raw_text)
        elif notice_type == "采购结果公告":
            data = cls._result(detail, list_record, raw_html, raw_text)
        elif notice_type == "采购终止公告":
            data = cls._termination(detail, list_record, raw_html, raw_text, subtype)
        elif notice_type == "采购变更公告":
            data = cls._change(detail, list_record, raw_html, raw_text, subtype)
        elif notice_type == "采购合同公告":
            data = cls._contract(detail, list_record, raw_html, raw_text)
        elif notice_type == "采购合同变更公告":
            data = cls._contract_change(detail, raw_text)
        elif notice_type == "履约验收公告":
            data = cls._acceptance(detail, list_record, raw_html, raw_text)
        elif notice_type == "采购意见征询":
            data = cls._opinion(detail, list_record, raw_html, raw_text, subtype)
        elif notice_type == "中小企业预留执行情况":
            data = cls._sme(detail, raw_html, raw_text)
        else:
            data = cls._history(detail, list_record, raw_text, subtype)
        data["发布日期"] = cls._datetime(detail.get("publishDate") or list_record.get("publishDate"))
        data["发布网站"] = config.PLATFORM_NAME
        return notice_type, data, cls.attachments(detail, raw_html), raw_html, raw_text

    @staticmethod
    def _lines(text: str) -> list[str]:
        return [_clean(line) for line in str(text or "").splitlines() if _clean(line)]

    @classmethod
    def _label(cls, text: str, *labels: str) -> str:
        lines = cls._lines(text)
        normalized = [re.sub(r"\s+", "", x).rstrip("：:") for x in lines]
        for label in labels:
            key = re.sub(r"\s+", "", label).rstrip("：:")
            for index, line in enumerate(normalized):
                if line == key and index + 1 < len(lines):
                    return lines[index + 1]
                position = line.find(key)
                if position >= 0:
                    original = lines[index]
                    colon_positions = [
                        item for item in (original.find("："), original.find(":"))
                        if item >= 0
                    ]
                    colon = min(colon_positions) if colon_positions else -1
                    if colon >= 0 and colon + 1 < len(original):
                        return _clean(original[colon + 1 :])
            match = re.search(
                rf"(?im)^\s*(?:[一二三四五六七八九十\d]+[、.．]\s*)?{re.escape(label)}\s*[：:]\s*([^\n]+)", text
            )
            if match:
                return _clean(match.group(1))
        return ""

    @classmethod
    def _section(cls, text: str, starts: Iterable[str], ends: Iterable[str]) -> str:
        start_pattern = "|".join(re.escape(x) for x in starts)
        end_pattern = "|".join(re.escape(x) for x in ends)
        match = re.search(
            rf"(?is)(?:^|\n)\s*(?:{start_pattern})[^\n]*\n?\s*(.*?)(?=(?:\n\s*(?:{end_pattern})[^\n]{{0,60}}(?:\n|$))|\Z)",
            text,
        )
        return "\n".join(cls._lines(match.group(1))) if match else ""

    @staticmethod
    def _bool(value: Any) -> bool:
        text = _clean(value)
        return text in {"是", "接受", "允许", "专门面向中小企业", "true", "1"}

    @staticmethod
    def _tables(raw_html: str) -> list[list[list[str]]]:
        soup = BeautifulSoup(raw_html or "", "html.parser")
        result: list[list[list[str]]] = []
        for table in soup.find_all("table"):
            rows: list[list[str]] = []
            for tr in table.find_all("tr"):
                cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
                if any(cells):
                    rows.append(cells)
            if rows:
                result.append(rows)
        return result

    @classmethod
    def _table_objects(cls, raw_html: str, required: Iterable[str]) -> list[dict[str, str]]:
        required_keys = tuple(re.sub(r"\s+", "", x) for x in required)
        for rows in cls._tables(raw_html):
            for header_index, header in enumerate(rows):
                normalized = [re.sub(r"\s+", "", x) for x in header]
                if not all(any(key in cell for cell in normalized) for key in required_keys):
                    continue
                objects: list[dict[str, str]] = []
                for row in rows[header_index + 1 :]:
                    if not any(row):
                        continue
                    padded = row + [""] * (len(header) - len(row))
                    objects.append({_clean(header[i]): padded[i] for i in range(len(header))})
                if objects:
                    return objects
        return []

    @staticmethod
    def _pick(row: Mapping[str, Any], *names: str) -> str:
        for name in names:
            for key, value in row.items():
                if re.sub(r"\s+", "", name) in re.sub(r"\s+", "", str(key)):
                    clean = _clean(value)
                    if clean:
                        return clean
        return ""

    @staticmethod
    def _datetime(value: Any) -> Any:
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value) / 1000)
            except (OSError, OverflowError, ValueError):
                return None
        return value

    @classmethod
    def _contacts(cls, text: str) -> dict[str, str]:
        result = {
            "采购人名称": "", "采购人地址": "", "采购人联系人": "", "采购人联系方式": "",
            "采购代理机构": "", "采购代理机构地址": "", "采购代理机构联系人": "",
            "采购代理机构联系方式": "", "项目联系人": "", "项目联系电话": "",
        }
        owner = cls._section(text, ("1.采购人信息", "采购人信息", "采购人（甲方）"),
                             ("2.采购代理机构信息", "采购代理机构信息", "供应商（乙方）", "3.项目联系方式", "3.项目联系人"))
        agency = cls._section(text, ("2.采购代理机构信息", "采购代理机构信息"),
                              ("3.项目联系方式", "3.项目联系人", "项目联系方式", "项目联系人"))
        project = cls._section(text, ("3.项目联系方式", "3.项目联系人", "项目联系方式"),
                               ("附件信息", "六、附件", "七、附件"))
        result["采购人名称"] = cls._label(owner, "名称", "名 称", "采购人（甲方）")
        result["采购人地址"] = cls._label(owner, "地址", "地 址")
        result["采购人联系人"] = cls._label(owner, "联系人", "联 系 人")
        result["采购人联系方式"] = cls._label(owner, "联系方式", "联系电话", "电 话")
        result["采购代理机构"] = cls._label(agency, "名称", "名 称")
        result["采购代理机构地址"] = cls._label(agency, "地址", "地 址")
        result["采购代理机构联系人"] = cls._label(agency, "联系人", "联 系 人", "项目联系人")
        result["采购代理机构联系方式"] = cls._label(agency, "联系方式", "联系电话", "电 话")
        result["项目联系人"] = cls._label(project, "项目联系人", "联系人") or result["采购代理机构联系人"]
        result["项目联系电话"] = cls._label(project, "电话", "电 话", "联系电话", "联系方式") or result["采购代理机构联系方式"]
        result["项目联系人"] = result["项目联系人"] or cls._label(text, "项目联系人")
        result["项目联系电话"] = result["项目联系电话"] or cls._label(text, "电 话", "项目联系电话")
        return result

    @classmethod
    def _common(cls, d: Mapping[str, Any], row: Mapping[str, Any], text: str) -> dict[str, Any]:
        return {
            "项目性质": "政府采购",
            "项目名称": _clean(d.get("projectName") or row.get("projectName")) or cls._label(text, "项目名称", "原公告的采购项目名称"),
            "项目编号": _clean(d.get("projectCode") or row.get("projectCode")) or cls._label(text, "项目编号", "原公告的采购项目编号"),
            "采购品目类型": _clean(row.get("gpCatalogName")),
            "所属行业": _clean(d.get("rankCategoryName") or row.get("rankCategoryName")),
            "采购组织形式": cls._label(text, "采购组织形式", "组织形式"),
            "采购方式": _clean(row.get("procurementMethod")) or cls._label(text, "采购方式"),
        }

    @classmethod
    def _intention(cls, d, row, html, text):
        records = cls._table_objects(html, ("采购项目名称", "采购需求概况", "预算金额"))
        details = [{
            "采购项目名称": cls._pick(x, "采购项目名称"),
            "采购需求概况": cls._pick(x, "采购需求概况"),
            "预算金额": cls._pick(x, "预算金额"),
            "预计采购时间": cls._pick(x, "预计采购时间"),
            "是否专门面向中小企业采购": cls._pick(x, "是否专门面向中小企业"),
            "备注": cls._pick(x, "备注"),
        } for x in records]
        first = details[0] if details else {}
        return {
            "项目性质": "政府采购意向", "采购单位": _clean(d.get("author") or row.get("purchaseName")),
            "采购项目名称": first.get("采购项目名称", ""), "采购品目": "",
            "采购需求概况": first.get("采购需求概况", ""), "预算金额": first.get("预算金额", ""),
            "预计采购时间": first.get("预计采购时间", ""),
            "是否专门面向中小企业采购": cls._bool(first.get("是否专门面向中小企业采购")),
            "备注": first.get("备注", ""), "意向明细": details,
        }

    @classmethod
    def _notice(cls, d, row, html, text):
        data = cls._common(d, row, text)
        packages = cls._table_objects(html, ("标项名称", "预算金额"))
        data.update({
            "采购品目编码": "", "预算金额": row.get("budgetPrice") or cls._label(text, "预算金额（元）", "预算金额"),
            "最高限价": cls._label(text, "最高限价（元）", "最高限价"),
            "资金来源": cls._label(text, "资金来源"),
            "项目实施地点": cls._label(text, "项目实施地点", "交付地点", "服务地点", "施工地点"),
            "采购需求": cls._section(text, ("采购需求",), ("合同履约期限", "本项目", "二、申请人的资格要求")),
            "合同履行期限": cls._label(text, "合同履约期限", "服务期限", "施工工期", "交付期限"),
            "质量要求": cls._label(text, "质量要求", "服务标准", "验收标准"),
            "是否接受联合体": cls._bool(cls._label(text, "本项目")) or bool(re.search(r"本项目\s*[（(]?\s*是\s*[）)]?\s*接受联合体", text)),
            "是否允许进口产品": bool(re.search(r"允许.{0,8}进口产品|采购进口产品", text)),
            "是否专门面向中小企业": bool(re.search(r"专门面向中小企业", text)),
            "供应商资格要求": cls._label(text, "满足《中华人民共和国政府采购法》第二十二条规定") or ("满足《中华人民共和国政府采购法》第二十二条规定" if "政府采购法" in text else ""),
            "政府采购政策资格要求": cls._label(text, "落实政府采购政策需满足的资格要求"),
            "特定资格要求": cls._label(text, "本项目的特定资格要求"),
            "采购文件获取开始时间": cls._first_date_time(cls._section(text, ("三、获取",), ("四、提交", "四、响应文件"))),
            "采购文件获取结束时间": cls._date_range_end(cls._section(text, ("三、获取",), ("四、提交", "四、响应文件"))),
            "采购文件获取地点": cls._label(cls._section(text, ("三、获取",), ("四、提交", "四、响应文件")), "地点"),
            "采购文件获取方式": cls._label(cls._section(text, ("三、获取",), ("四、提交", "四、响应文件")), "方式"),
            "采购文件售价": cls._label(text, "售价（元）", "售价"),
            "响应文件提交截止时间": cls._label(text, "提交投标文件截止时间", "响应文件提交截止时间", "提交响应文件截止时间"),
            "响应文件提交地点": cls._label(text, "投标地点（网址）", "响应文件提交地点", "提交地点"),
            "响应文件提交方式": cls._label(text, "投标地点（网址）", "响应文件提交方式"),
            "开启时间": cls._label(text, "开标时间", "开启时间") or row.get("bidOpeningTime"),
            "开启地点": cls._label(text, "开标地点", "开启地点"), "开启方式": cls._label(text, "开启方式"),
            "公告期限": cls._section(text, ("五、公告期限", "四、公告期限"), ("六、其他补充事宜", "五、其他补充事宜")),
            "代理费支付方式": cls._label(text, "代理费支付方式"),
            "代理费收费标准": cls._label(text, "代理费收费标准"), "代理费金额": cls._label(text, "代理费收费金额（元）"),
            "采购包/标项明细": packages or cls._package_blocks(text), "其他补充事宜": cls._section(text, ("六、其他补充事宜", "五、其他补充事宜"), ("七、对本次采购", "六、对本次采购")),
        })
        data.update(cls._contacts(text))
        data["采购人名称"] = data["采购人名称"] or _clean(row.get("purchaseName"))
        return data

    @staticmethod
    def _date_range_end(value: str) -> str:
        dates = re.findall(r"20\d{2}年\d{1,2}月\d{1,2}日(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", value or "")
        return dates[-1] if len(dates) > 1 else ""

    @staticmethod
    def _first_date_time(value: str) -> str:
        match = re.search(r"20\d{2}年\d{1,2}月\d{1,2}日(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", value or "")
        return match.group(0) if match else ""

    @classmethod
    def _package_blocks(cls, text: str) -> list[dict[str, str]]:
        section = cls._section(text, ("采购需求",), ("合同履约期限", "本项目", "二、申请人的资格要求"))
        if not section:
            return []
        names = re.findall(r"标项名称\s*[：:]\s*([^\n]+)", section)
        budgets = re.findall(r"预算金额（元）\s*[：:]\s*([^\n]+)", section)
        descriptions = re.findall(r"简要规格描述或项目基本概况介绍、用途\s*[：:]\s*([^\n]+)", section)
        return [
            {
                "标项名称": name,
                "预算金额": budgets[i] if i < len(budgets) else "",
                "简要规格描述或项目基本概况介绍、用途": descriptions[i] if i < len(descriptions) else "",
            }
            for i, name in enumerate(names)
        ]

    @classmethod
    def _result(cls, d, row, html, text):
        data = cls._common(d, row, text)
        suppliers = cls._table_objects(html, ("供应商名称", "中标"))
        supplier_details = [{
            "供应商名称": cls._pick(x, "供应商名称"), "供应商地址": cls._pick(x, "供应商地址"),
            "供应商统一社会信用代码": cls._pick(x, "统一社会信用代码"),
            "中标/成交金额": cls._pick(x, "中标（成交）金额", "中标金额", "成交金额", "投标总报价"),
            "评审总得分": cls._pick(x, "评审总得分"),
        } for x in suppliers]
        main = supplier_details[0] if supplier_details else {}
        goods = cls._table_objects(html, ("标的名称", "数量", "单价"))
        services = cls._table_objects(html, ("服务名称", "服务范围"))
        works = cls._table_objects(html, ("施工范围", "施工工期"))
        failed = cls._table_objects(html, ("标项名称", "废标理由"))
        data.update({
            "中标/成交供应商名称": main.get("供应商名称", "") or _clean(row.get("supplierName")),
            "供应商地址": main.get("供应商地址", ""), "供应商统一社会信用代码": main.get("供应商统一社会信用代码", ""),
            "中标/成交金额": main.get("中标/成交金额", "") or row.get("totalContractAmount"),
            "评审总得分": main.get("评审总得分", ""), "联合体成员": [],
            "供应商明细": supplier_details, "主要标的信息": goods or services or works, "废标明细": failed,
            "评审专家名单": cls._list_value(cls._section(text, ("五、评审专家", "四、评审专家"), ("六、代理服务", "五、代理服务"))),
            "采购人代表": cls._purchase_representative(text),
            "代理服务收费标准": cls._label(text, "代理服务收费标准"),
            "代理服务收费金额": cls._label(text, "代理服务收费金额（元）", "代理服务收费金额"),
            "公告期限": cls._section(text, ("七、公告期限", "六、公告期限"), ("八、其他补充事宜", "七、其他补充事宜")),
            "其他补充事宜": cls._section(text, ("八、其他补充事宜", "七、其他补充事宜"), ("九、对本次公告", "八、对本次公告")),
        })
        data.update(cls._contacts(text))
        return data

    @staticmethod
    def _list_value(value: str) -> list[str]:
        text = re.sub(r"^[：:\s]+", "", value or "").split("\n", 1)[0]
        return [x.strip() for x in re.split(r"[，,、；;]", text) if x.strip()]

    @staticmethod
    def _purchase_representative(text: str) -> str:
        match = re.search(r"([^，,；;\n（）()]+)[（(][^）)]*采购人代表[）)]", text)
        return _clean(match.group(1)) if match else ""

    @classmethod
    def _termination(cls, d, row, html, text, subtype):
        common = cls._common(d, row, text)
        data = {
            "项目名称": common["项目名称"], "项目编号": common["项目编号"], "采购方式": common["采购方式"],
            "终止/废标类型": subtype, "终止/废标原因": cls._label(text, "终止原因", "废标理由", "废标原因") or cls._section(text, ("二、项目终止的原因", "二、废标原因"), ("三、其他补充事项", "三、其他补充事宜")),
            "废标明细": cls._table_objects(html, ("标项名称", "废标理由")), "首次公告日期": cls._label(text, "首次公告日期"),
            "公告期限": cls._section(text, ("公告期限",), ("其他补充事宜", "对本次公告")),
            "其他补充事宜": cls._section(text, ("其他补充事宜",), ("对本次公告", "联系方式")),
        }
        data.update(cls._contacts(text))
        return data

    @classmethod
    def _change(cls, d, row, html, text, subtype):
        corrections = cls._table_objects(html, ("更正项", "更正前", "更正后"))
        data = {
            "原采购项目编号": _clean(d.get("projectCode")) or cls._label(text, "原公告的采购项目编号"),
            "原采购项目名称": _clean(d.get("projectName")) or cls._label(text, "原公告的采购项目名称"),
            "首次公告日期": cls._label(text, "首次公告日期"), "变更类型": subtype,
            "更正事项": cls._label(text, "更正事项"), "更正日期": cls._label(text, "更正日期"),
            "更正明细": corrections, "恢复采购时间": cls._label(text, "恢复采购时间"),
            "其他补充事宜": cls._section(text, ("三、其他补充事宜",), ("四、对本次采购",)),
        }
        data.update(cls._contacts(text))
        return data

    @classmethod
    def _contract(cls, d, row, html, text):
        contacts = cls._contacts(text)
        owner_block = cls._section(text, ("采购人（甲方）",), ("供应商（乙方）",))
        supplier_block = cls._section(text, ("供应商（乙方）",), ("六、合同主体信息", "六、合同主要信息"))
        items = cls._table_objects(html, ("主要标的名称", "数量", "单价")) or cls._contract_item_blocks(text)
        is_change = "合同变更" in " ".join(map(str, d.get("categoryNames") or [])) or "合同基本信息" in text
        return {
            "合同编号": cls._label(text, "原公告的采购合同编号", "一、合同编号", "合同编号"),
            "合同名称": cls._label(text, "原公告的采购合同名称", "二、合同名称", "合同名称"),
            "项目编号": _clean(d.get("projectCode")) or cls._label(text, "三、项目编号", "项目编号"),
            "项目名称": _clean(d.get("projectName")) or cls._label(text, "四、项目名称", "项目名称"),
            "采购人名称": cls._label(text, "采购人（甲方）") or contacts["采购人名称"],
            "采购人地址": cls._label(owner_block, "地址", "地 址"), "采购人联系方式": cls._label(owner_block, "联系方式"),
            "供应商名称": cls._label(text, "供应商（乙方）"), "供应商地址": cls._label(supplier_block, "地址", "地 址"),
            "供应商联系方式": cls._label(supplier_block, "联系方式"), "合同金额": cls._label(text, "合同金额（元）", "合同金额"),
            "采购方式": cls._label(text, "采购方式"), "履约期限": cls._label(text, "履约期限、地点等简要信息", "履约期限"),
            "履约地点": cls._label(text, "履约地点"), "履约方式": cls._label(text, "履约方式"),
            "合同标的明细": items, "合同签订日期": cls._label(text, "七、合同签订日期", "合同签订日期"),
            "合同公告日期": cls._label(text, "八、合同公告日期", "合同公告日期"),
            "合同变更原因": cls._label(text, "合同变更原因", "变更原因", "变更内容") if is_change else "",
            "其他补充事宜": cls._label(text, "九、其他补充事宜", "其他补充事宜"),
        }

    @classmethod
    def _contract_item_blocks(cls, text: str) -> list[dict[str, str]]:
        section = cls._section(text, ("1.主要标的信息", "主要标的信息"), ("2.合同金额", "合同金额"))
        if not section:
            return []
        starts = list(re.finditer(r"主要标的名称\s*[：:]\s*([^\n]+)", section))
        result: list[dict[str, str]] = []
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(section)
            block = section[match.start():end]
            result.append({
                "主要标的名称": _clean(match.group(1)),
                "数量": cls._label(block, "数量"), "单价": cls._label(block, "单价（元）", "单价"),
                "规格型号（或服务要求）": cls._section(block, ("规格型号（或服务要求）",), ("主要标的名称",)),
            })
        return result

    @classmethod
    def _contract_change(cls, d: Mapping[str, Any], text: str) -> dict[str, Any]:
        return {
            "原合同编号": cls._label(text, "原公告的采购合同编号"),
            "原合同名称": cls._label(text, "原公告的采购合同名称"),
            "项目编号": _clean(d.get("projectCode")), "项目名称": _clean(d.get("projectName")),
            "首次公告日期": cls._label(text, "首次公告日期"),
            "变更事项": cls._label(text, "变更事项"), "变更内容": cls._label(text, "变更内容"),
            "变更日期": cls._label(text, "变更日期"),
            "项目联系人": cls._label(text, "项目联系人"),
            "项目联系电话": cls._label(text, "电话", "电 话"),
            "其他补充事宜": cls._section(text, ("三、其他补充事宜",), ("四、凡对本次公告",)),
        }

    @classmethod
    def _acceptance(cls, d, row, html, text):
        owner = cls._section(text, ("采购人（甲方）",), ("供应商（乙方）",))
        supplier = cls._section(text, ("供应商（乙方）",), ("六、合同主要信息",))
        opinion = cls._label(text, "九、验收意见", "验收意见")
        return {
            "合同编号": cls._label(text, "一、合同编号", "合同编号"), "合同名称": cls._label(text, "二、合同名称", "合同名称"),
            "项目编号": _clean(d.get("projectCode")) or cls._label(text, "三、项目编号", "项目编号"),
            "项目名称": _clean(d.get("projectName")) or cls._label(text, "四、项目名称", "项目名称"),
            "采购人名称": cls._label(text, "采购人（甲方）"), "采购人地址": cls._label(owner, "地址", "地 址"),
            "采购人联系方式": cls._label(owner, "联系方式", "联系电话", "联 系 方 式"),
            "供应商名称": cls._label(text, "供应商（乙方）"),
            "供应商地址": cls._label(supplier, "地址", "地 址"), "供应商联系方式": cls._label(supplier, "联系方式", "联系电话", "联 系 方 式"),
            "合同主要内容": cls._section(text, ("六、合同主要信息",), ("七、验收日期",)),
            "服务内容": cls._label(text, "服务内容"), "服务要求": cls._label(text, "服务要求"),
            "服务期限": cls._label(text, "服务期限"), "服务地点": cls._label(text, "服务地点"),
            "验收日期": cls._label(text, "七、验收日期", "验收日期"), "验收方式": cls._label(text, "验收方式"),
            "验收组成员": cls._list_value(cls._label(text, "验收组成员（应当邀请服务对象参与）", "验收组成员")),
            "服务对象代表": cls._service_representatives(text), "验收意见": opinion,
            "验收结论": opinion, "其他补充事宜": cls._label(text, "十、其他补充事宜", "其他补充事宜"),
        }

    @staticmethod
    def _service_representatives(text: str) -> list[str]:
        line = CcgpShanxiParser._label(text, "验收组成员（应当邀请服务对象参与）", "验收组成员")
        return [_clean(re.sub(r"[（(]服务对象[）)]", "", x)) for x in re.split(r"[，,、；;]", line) if "服务对象" in x]

    @classmethod
    def _opinion(cls, d, row, html, text, subtype):
        proposed = cls._table_objects(html, ("标的名称", "数量", "预算金额"))
        return {
            "意见征询类型": subtype, "项目名称": _clean(d.get("projectName")) or cls._label(text, "项目名称"),
            "项目编号": _clean(d.get("projectCode")) or cls._label(text, "意见征询编号", "项目编号"),
            "采购人名称": cls._label(text, "采购人") or _clean(d.get("author")),
            "采购需求概况": cls._label(text, "拟采购的货物或服务的说明", "公示简要情况说明"),
            "拟采购标的明细": proposed, "预算金额": cls._label(text, "拟采购的货物或服务的预算总金额（元）", "预算金额(元)", "预算金额"),
            "采用单一来源原因": cls._label(text, "采用单一来源采购方式的原因及说明"),
            "拟定供应商名称": cls._label(cls._section(text, ("二、拟定供应商信息",), ("三、公示期限",)), "名称"),
            "拟定供应商地址": cls._label(cls._section(text, ("二、拟定供应商信息",), ("三、公示期限",)), "地址"),
            "征求意见范围": cls._label(text, "征求意见范围"), "意见递交开始时间": cls._label(text, "意见递交开始时间"),
            "意见递交截止时间": cls._label(text, "意见递交时间", "意见反馈截止时间"),
            "意见递交方式": cls._label(text, "意见递交方式"), "意见接收机构": cls._label(text, "意见接收机构"),
            "联系人": cls._label(text, "联系人", "联 系 人"), "联系电话": cls._label(text, "联系电话"),
            "联系邮箱": cls._label(text, "联系邮箱"), "财政部门": cls._label(text, "财政部门"),
            "财政部门联系人": cls._label(cls._section(text, ("2.财政部门",), ("3.采购代理机构",)), "联系人", "联 系 人"),
            "财政部门联系方式": cls._label(cls._section(text, ("2.财政部门",), ("3.采购代理机构",)), "联系电话"),
            "公示开始时间": cls._first_date(cls._section(text, ("三、公示期限",), ("四、其他补充事宜",))),
            "公示结束时间": cls._last_date(cls._section(text, ("三、公示期限",), ("四、其他补充事宜",))),
            "专业人员论证意见": cls._section(text, ("专业人员论证意见",), ("附件信息",)),
            "其他补充事宜": cls._section(text, ("四、其他补充事宜",), ("五、联系方式",)),
        }

    @staticmethod
    def _first_date(value: str) -> str:
        dates = re.findall(r"20\d{2}年\d{1,2}月\d{1,2}日", value or "")
        return dates[0] if dates else ""

    @staticmethod
    def _last_date(value: str) -> str:
        dates = re.findall(r"20\d{2}年\d{1,2}月\d{1,2}日", value or "")
        return dates[-1] if dates else ""

    @classmethod
    def _sme(cls, d, html, text):
        details = cls._table_objects(html, ("项目名称", "预留选项", "采购金额"))
        return {
            "部门/单位名称": cls._label(text, "部门（单位）名称", "部门/单位名称") or _clean(d.get("author")),
            "统计年度": cls._label(text, "统计年度") or cls._year(text),
            "面向中小企业采购总金额": cls._regex_value(text, r"面向中小企业采购共计\s*([\d.]+\s*万元)"),
            "面向小微企业采购金额": cls._regex_value(text, r"面向小微企业采购\s*([\d.]+\s*万元)"),
            "小微企业采购占比": cls._regex_value(text, r"占\s*([\d.]+\s*%)"), "预留项目明细": details,
        }

    @staticmethod
    def _year(text: str) -> str:
        match = re.search(r"(20\d{2})\s*年面向中小企业", text)
        return match.group(1) if match else ""

    @staticmethod
    def _regex_value(text: str, pattern: str) -> str:
        match = re.search(pattern, text, re.S)
        return _clean(match.group(1)) if match else ""

    @classmethod
    def _history(cls, d, row, text, subtype):
        return {
            "原始公告类型": subtype, "项目名称": _clean(d.get("projectName") or row.get("projectName")) or cls._label(text, "项目名称"),
            "项目编号": _clean(d.get("projectCode")) or cls._label(text, "项目编号"),
            "采购人名称": _clean(row.get("purchaseName")) or cls._label(text, "采购人"),
            "采购代理机构": _clean(d.get("author")) or cls._label(text, "采购代理机构"), "公告内容": text,
        }

    @classmethod
    def attachments(cls, detail: Mapping[str, Any], raw_html: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        vo = detail.get("attachmentVO") or {}
        if isinstance(vo, Mapping):
            domain = str(vo.get("domain") or "")
            for item in vo.get("attachments") or []:
                if not isinstance(item, Mapping) or item.get("isShow") is False:
                    continue
                file_id = str(item.get("fileId") or "").strip()
                url = urljoin(domain, file_id)
                if not url or url in seen:
                    continue
                seen.add(url)
                result.append(cls._attachment(file_id, item.get("name"), url))
        soup = BeautifulSoup(raw_html or "", "html.parser")
        for anchor in soup.find_all("a", href=True):
            url = urljoin(config.WEB_BASE_URL, str(anchor.get("href") or "").strip())
            if not url or url in seen or not cls._looks_like_file(url):
                continue
            seen.add(url)
            result.append(cls._attachment("", anchor.get_text(" ", strip=True), url))
        return result

    @staticmethod
    def _looks_like_file(url: str) -> bool:
        return bool(re.search(r"\.(?:pdf|docx?|xlsx?|zip|rar)(?:\?|$)", url, re.I))

    @staticmethod
    def _attachment(file_id: str, name: Any, url: str) -> dict[str, Any]:
        filename = _clean(name) or urlparse(url).path.rsplit("/", 1)[-1]
        mime, _ = mimetypes.guess_type(filename)
        return {
            "source_file_id": file_id or None, "file_name": filename,
            "file_url": url, "file_type": mime or "application/octet-stream",
            "parse_status": "PENDING",
        }
