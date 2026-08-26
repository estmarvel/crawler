"""吕梁交易中心 CMS + 内嵌 PDF 的站点专用解析器。"""

from __future__ import annotations

from html import escape
import re
from typing import Any, Mapping

from crawler_scrapy.schemas.notice_fields import coerce_datetime, create_empty_notice_data
from crawler_scrapy.sites.llggzy import config
from crawler_scrapy.sites.sxzwfw.parser import ParsedNotice, SxzwfwParser


class LlggzyParser:
    VERSION = "llggzy-v2-real-pdf-field-rules"
    SECTION = {
        "plan": "zbjh", "tender": "zbgg_zys", "transfer": "zbgg_zys",
        "candidate": "hxr", "award": "gs", "contract": "qt",
        "clarification": "bg", "control_price": "bg", "failure": "qt", "withdrawal": "bg",
    }

    @classmethod
    def parse(cls, feed: str, title: str, published: str, body_text: str,
              detail_url: str, attachments: list[dict[str, Any]], source_html: str = "") -> tuple[ParsedNotice, str]:
        info = config.feed_info(feed)
        body_text = cls._normalize_pdf_text(body_text)
        audit_html = cls._audit_html(title, published, body_text, detail_url, source_html)
        parsed = SxzwfwParser.parse(
            cls.SECTION[info["category"]], audit_html,
            {"title": title, "publish_time": published}, detail_url,
        )
        wanted = info["notice_type"]
        if parsed.notice_type != wanted:
            replacement = create_empty_notice_data(wanted, include_parser_diagnostics=True)
            replacement.update({key: value for key, value in parsed.data.items()
                                if key in replacement and value not in (None, "", [])})
            parsed.data = replacement
            parsed.notice_type = wanted
        parsed.subtype = info["subtype"]
        parsed.title = title
        parsed.publish_time = published
        parsed.raw_text = body_text
        parsed.attachments = attachments
        cls._enrich(parsed.data, body_text, info, published, title)
        return parsed, audit_html

    @staticmethod
    def _normalize_pdf_text(text: str) -> str:
        value = str(text or "").replace("\x00", "").replace("\u3000", " ").replace("\xa0", " ")
        # PDF 转文字常在公司名称中间硬换行，先合并后才能稳定识别候选人、代理机构等主体。
        value = re.sub(r"有\s*\n\s*限公司", "有限公司", value)
        value = re.sub(r"(有限|股份|集团)\s*\n\s*公司", r"\1公司", value)
        value = re.sub(r"(?<=[\u4e00-\u9fff])\n(?=有限公司)", "", value)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _audit_html(title: str, published: str, text: str, detail_url: str, source_html: str) -> str:
        return (
            '<!doctype html><html><head><meta charset="utf-8"><title>' + escape(title) +
            '</title></head><body data-source="llggzy-cms-embedded-pdf" '
            f'data-detail-url="{escape(detail_url)}"><h1 class="detail-h1">{escape(title)}</h1>'
            f'<div class="detail-infor">发布时间：{escape(published)}</div>'
            f'<pre class="cs_xq_content">{escape(text)}</pre>'
            f'<template id="source-detail-html">{escape(source_html)}</template></body></html>'
        )

    @classmethod
    def _enrich(cls, data: dict[str, Any], text: str, info: Mapping[str, str],
                published: str, title: str) -> None:
        data["发布日期"] = published
        data["发布网站"] = config.PLATFORM_NAME
        if "项目性质" in data:
            data["项目性质"] = data.get("项目性质") or "招标信息"
        if "所属行业" in data:
            data["所属行业"] = data.get("所属行业") or info["module_label"]
        if "组织形式" in data:
            data["组织形式"] = data.get("组织形式") or ("委托招标" if re.search(r"招标代理(?:机构)?", text) else "自行招标")
        if "项目名称" in data:
            data["项目名称"] = data.get("项目名称") or cls._project_name(title)
        category = info["category"]
        number = cls._value(text, r"招标项目编号|项目编号|招标编号|项目代码|宗地编号")
        for key in ("项目编号", "招标编号", "依据文号"):
            if key in data and number:
                data[key] = data.get(key) or number
        if category in {"tender", "transfer"}:
            if "项目类型/行业分类" in data:
                data["项目类型/行业分类"] = data.get("项目类型/行业分类") or info["module_label"]
            if "开标时间" in data and not data.get("开标时间"):
                data["开标时间"] = cls._datetime_value(text, r"开标时间|投标截止时间|递交截止时间")
            if "开启时间" in data:
                data["开启时间"] = data.get("开启时间") or data.get("开标时间")
        if category == "plan":
            mapping = {
                "项目编号": ("项目代码",), "项目名称": ("项目名称",), "建设内容及规模": ("建设内容及规模",),
                "建设地点": ("建设地点",), "项目总投资": ("项目总投资",), "招标内容": ("招标内容",),
                "项目类型": ("项目类型",), "招标方式": ("招标方式",), "招标人名称": ("招标人名称",),
                "行政监督部门": ("行政监督部门",),
                "招标公告（资格预审公告）预计发布时间": ("招标公告预计发布时间", "资格预审公告预计发布时间"),
            }
            for key, labels in mapping.items():
                value = next((cls._numbered_value(text, label) for label in labels if cls._numbered_value(text, label)), "")
                if key in data and value:
                    data[key] = value
            if "招标编号" in data:
                data["招标编号"] = data.get("招标编号") or data.get("项目编号")
        if category == "candidate":
            cls._candidate_fields(data, text)
        if category == "award":
            cls._award_fields(data, text)
        if category == "contract":
            cls._contract_fields(data, text, title)
        if category in {"clarification", "control_price", "failure", "withdrawal"}:
            data["公共类型"] = info["category_label"]
            if "公告内容" in data:
                data["公告内容"] = text
            changed = re.findall(
                r"(?:现(?:变更|修改|延期)为|变更后|延期至)\s*[：:]?\s*"
                r"((?:20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)[^\n，。；;]{0,24}?(?:\d{1,2}[时:]\d{1,2}(?:分)?))",
                text,
            )
            if changed and "开标时间" in data:
                data["开标时间"] = changed[-1].strip()
            elif "开标时间" in data:
                times = cls._datetime_values(text, r"开标时间|投标截止时间|递交截止时间")
                if times:
                    data["开标时间"] = times[-1]
            if category == "control_price" and "依据文件" in data:
                data["依据文件"] = cls._value(text, r"招标控制价|最高投标限价|最高限价|控制总价")
            cls._supervision_fields(data, text)

    @staticmethod
    def _project_name(title: str) -> str:
        return re.sub(r"(?:招标计划|招标公告|变更公告|澄清答疑|中标候选人公示|中标公示|中标公告|成交公示|异常公告|废标公告|流拍公示|撤销公告|招标控制价|合同公开).*$", "", title).strip() or title

    @staticmethod
    def _value(text: str, label: str) -> str:
        match = re.search(rf"(?:{label})\s*[：:]\s*([^\n；;]+)", text)
        return match.group(1).strip(" ：:，,。；;（）()") if match else ""

    @classmethod
    def _datetime_value(cls, text: str, label: str) -> str:
        values = cls._datetime_values(text, label)
        return values[0] if values else ""

    @staticmethod
    def _datetime_values(text: str, label: str) -> list[str]:
        pattern = re.compile(
            rf"(?:{label})\s*[：:]\s*"
            r"((?:20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?)"
            r"(?:\s*\d{1,2}\s*(?:时|:)\s*\d{1,2}\s*(?:分)?)?)"
        )
        result = []
        for value in pattern.findall(text):
            value = re.sub(r"\s+", " ", value).strip()
            parsed = coerce_datetime(re.sub(r"\s+", "", value))
            result.append(parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else value)
        return result

    @staticmethod
    def _numbered_value(text: str, label: str) -> str:
        match = re.search(
            rf"(?m)^[一二三四五六七八九十]+、\s*{label}\s*(?:[（(][^）)\n]+[）)])?\s*[：:]\s*(.*?)"
            rf"(?=\n[一二三四五六七八九十]+、|\Z)", text, re.S,
        )
        return re.sub(r"\s+", " ", match.group(1)).strip(" ，,。；;") if match else ""

    @classmethod
    def _candidate_fields(cls, data: dict[str, Any], text: str) -> None:
        public = cls._value(text, r"公示期限|公示时间")
        if not public:
            start = cls._value(text, r"公示开始时间|公示开始日期")
            end = cls._value(text, r"公示结束时间|公示结束日期")
            public = " 至 ".join(value for value in (start, end) if value)
        if public:
            data["公示时间"] = public
        pairs: list[tuple[str, str]] = []
        ordinal = re.compile(
            r"第[一二三四五六七八九十\d]+中标候选人\s*[：:]\s*([^\n；;]+).*?"
            r"投标报价\s*[：:]\s*([\d,.]+\s*(?:元|万元)?)", re.S,
        )
        pairs.extend((name.strip(), price.strip()) for name, price in ordinal.findall(text))
        row_pattern = re.compile(r"(?m)^\s*\d+\s+([^\n]{2,100}?(?:公司|中心|院|所))\s+([\d,.]+)\s*(?:元)?(?:\s|$)")
        pairs.extend((name.strip(), price.strip() + "元") for name, price in row_pattern.findall(text))
        if not pairs:
            transport_rows = re.findall(r"(?m)^([^\n]{2,100}?(?:公司|中心|院|所))\s+([\d,.]{4,})\s+[123]\s*$", text)
            pairs.extend((name.strip(), price.strip() + "元") for name, price in transport_rows)
        unique = []
        seen = set()
        for name, price in pairs:
            if name not in seen:
                seen.add(name); unique.append((name, price))
        if unique:
            data["中标候选人名称"] = [name for name, _ in unique]
            data["中标候选人报价"] = [price for _, price in unique]

    @classmethod
    def _award_fields(cls, data: dict[str, Any], text: str) -> None:
        names = re.findall(r"(?:中标人|中标单位|成交人|受让方)\s*[：:]\s*([^\n；;]+)", text)
        names.extend(re.findall(r"竞得人\s*[：:]?\s*([^\n；;]+)", text))
        prices = re.findall(r"(?:中标价(?:格)?|中标金额|成交价|总地价)(?:\s*[（(]元[）)])?\s*[：:]?\s*([\d,.]+\s*(?:元|万元)?)", text)
        rows = re.findall(r"(?m)^\s*\d+\s+([^\n]{2,100}?(?:公司|中心|院|所))\s+([\d,.]+)\s*(?:元)?(?:\s|$)", text)
        if rows:
            names.extend(name.strip() for name, _ in rows)
            prices.extend(price.strip() + "元" for _, price in rows)
        if names:
            data["中标人名称"] = list(dict.fromkeys(value.strip() for value in names))
        if prices:
            data["中标价"] = list(dict.fromkeys(value.strip() for value in prices))
        if "工期" in data:
            data["工期"] = data.get("工期") or cls._value(text, r"工期|服务期限|交货期")
        if "项目经理" in data:
            data["项目经理"] = data.get("项目经理") or cls._value(text, r"项目经理|总监理工程师")
            data["项目经理证书名称"] = data.get("项目经理证书名称") or cls._value(text, r"证书名称")
            data["项目经理证书编号"] = data.get("项目经理证书编号") or cls._value(text, r"证书编号|注册编号")

    @classmethod
    def _contract_fields(cls, data: dict[str, Any], text: str, title: str) -> None:
        data["合同主要内容"] = text
        data["合同名称"] = data.get("合同名称") or cls._value(text, r"合同名称")
        if not data.get("合同名称"):
            match = re.search(r"签订[《“]([^》”]+)[》”]", text)
            data["合同名称"] = re.sub(r"\s+", "", match.group(1)) if match else title
        owner = re.search(r"招标人\s*([^\n（(]{2,100}?)(?=\s*[（(]|\s*与中标人)", text)
        winner = re.search(r"中标人\s*([^\n（(]{2,100}?)(?=\s*[（(]|\s*签订)", text)
        amount = re.search(r"合同金额(?:为)?\s*([\d,.]+\s*(?:元|万元))", text)
        signed = re.search(r"(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)[，,]?\s*招标人", text)
        if owner: data["招标人名称"] = owner.group(1).strip()
        if winner: data["中标人名称"] = winner.group(1).strip()
        if amount: data["合同金额"] = amount.group(1).strip()
        if signed: data["合同签署时间"] = re.sub(r"\s+", "", signed.group(1))

    @classmethod
    def _supervision_fields(cls, data: dict[str, Any], text: str) -> None:
        block = re.search(r"(?:监督部门|监督单位)(.*?)(?=\n(?:十|十一|十二|联系方式|招标人)\b|\Z)", text, re.S)
        value = block.group(1) if block else ""
        if "监督部门地址" in data:
            data["监督部门地址"] = data.get("监督部门地址") or cls._value(value, r"地址")
            data["监督部门联系人"] = data.get("监督部门联系人") or cls._value(value, r"联系人")
            data["监督部门联系方式"] = data.get("监督部门联系方式") or cls._value(value, r"联系电话|联系方式|电话")
