"""太原交易中心详情 API 的站点专用解析。"""

from __future__ import annotations

from html import escape
import re
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from crawler_scrapy.schemas.notice_fields import create_empty_notice_data
from crawler_scrapy.sites.sxzwfw.parser import SxzwfwParser
from crawler_scrapy.sites.tyggzy import config
from crawler_scrapy.sites.wc5ibid.parser import clean_html
from crawler_scrapy.sites.wtjypt.parser import WtjyptParser


class TyggzyParser:
    VERSION = "tyggzy-v2-site-field-rules"
    SECTION = {
        "tender": "zbgg_zys", "clarification": "bg", "control_price": "bg",
        "candidate": "hxr", "award": "gs", "other": "qt",
        "contract": "qt", "manager_change": "qt",
    }

    @classmethod
    def parse(cls, feed: str, payload: Mapping[str, Any], record: Mapping[str, Any]):
        _, category = feed.split(".", 1)
        title = str(payload.get("title") or record.get("title") or "").strip()
        published = cls._timestamp(payload.get("publicshTime") or record.get("bulletinIssueTime") or "").strip()
        content = str(payload.get("content") or payload.get("bulletincontent") or "")
        if category == "contract" and isinstance(payload.get("data"), Mapping):
            data = payload["data"]
            title = str(data.get("projectName") or title)
            published = cls._timestamp(data.get("submitTime") or published)
            content = cls._mapping_html(data)
        if category == "manager_change":
            notice = payload.get("gcjsGongGao") if isinstance(payload.get("gcjsGongGao"), Mapping) else payload
            title = str(notice.get("title") or title)
            published = cls._timestamp(notice.get("publicshTime") or notice.get("publishTime") or published)
            content = str(notice.get("content") or content)
        raw_html = cls._snapshot_html(title, published, content)
        if category == "contract":
            parsed = cls._contract(payload, title, published, clean_html(content))
            parsed.attachments = cls._attachments(payload, content)
            return parsed, raw_html
        parsed = SxzwfwParser.parse(
            cls.SECTION[category], raw_html,
            {"title": title, "publish_time": published}, config.detail_page(feed, str(record.get("guid") or "")),
        )
        # 源栏目比标题更稳定；明确栏目强制使用对应统一 Schema。
        if category in {"clarification", "control_price", "manager_change"}:
            parsed.notice_type = "更正结果公示"
            parsed.subtype = {"clarification": "cqxg", "control_price": "kzj", "manager_change": "jlbg"}[category]
            parsed.data = cls._correction_data(parsed.data, parsed.raw_text, category, published)
        cls._enrich(parsed.data, parsed.raw_text, parsed.notice_type)
        parsed.data["发布网站"] = config.PLATFORM_NAME
        attachments = cls._attachments(payload, content)
        parsed.attachments = attachments
        return parsed, raw_html

    @staticmethod
    def _snapshot_html(title: str, published: str, content: str) -> str:
        return (f'<article class="tyggzy-api-snapshot" data-source="apiJyxxDetail">'
                f'<h1 class="cs_title_P1">{escape(title)}</h1>'
                f'<div class="cs_title_P3">发布日期：{escape(published)}</div>'
                f'<div class="cs_xq_content">{content}</div></article>')

    @staticmethod
    def _mapping_html(data: Mapping[str, Any]) -> str:
        rows = "".join(f"<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>" for k, v in data.items() if v not in (None, ""))
        return f"<table>{rows}</table>"

    @staticmethod
    def _timestamp(value: Any) -> str:
        if value in (None, ""):
            return ""
        digits = str(value)
        if len(digits) == 14 and digits.isdigit():
            return datetime.strptime(digits, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, (int, float)) or digits.isdigit():
            number = int(value)
            if number > 10_000_000_000:
                number //= 1000
            return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
        return str(value)

    @staticmethod
    def _attachments(payload: Mapping[str, Any], content: str = "") -> list[dict[str, str]]:
        sources = []
        for key in ("attachmentCodeList", "fuJianList", "fujianList"):
            value = payload.get(key)
            if isinstance(value, list):
                sources.extend(value)
        result = []
        seen = set()
        for item in sources:
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("url") or item.get("fileUrl") or "").strip()
            file_id = str(item.get("attachmentCode") or item.get("guid") or "").strip()
            if not url and file_id:
                url = f"{config.BASE_URL}/component-file-web/downloadFile?guid={file_id}"
            if not url or url in seen:
                continue
            seen.add(url)
            result.append({
                "file_name": str(item.get("attachmentFileName") or item.get("attachmentName") or item.get("fileName") or "附件"),
                "file_url": url, "source_file_id": file_id, "source": "detail_api",
            })
        for anchor in BeautifulSoup(content or "", "html.parser").select("a[href]"):
            url = str(anchor.get("href") or "").strip()
            url = urljoin(f"{config.BASE_URL}/", url)
            parsed_url = urlparse(url)
            if parsed_url.scheme in {"http", "https"} and parsed_url.hostname and url not in seen:
                seen.add(url)
                result.append({"file_name": anchor.get_text(" ", strip=True) or "附件", "file_url": url,
                               "source_file_id": "", "source": "detail_html"})
        return result

    @classmethod
    def _contract(cls, payload: Mapping[str, Any], title: str, published: str, raw_text: str):
        from crawler_scrapy.sites.sxzwfw.parser import ParsedNotice
        source = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        data = create_empty_notice_data("合同与履约", include_parser_diagnostics=True)
        data.update({
            "项目名称": source.get("projectName") or title,
            "项目编号": source.get("tenderProjectCode") or "",
            "招标编号": source.get("tenderProjectCode") or "",
            "合同名称": source.get("bidSectionName") or source.get("projectName") or title,
            "招标人名称": source.get("constructionUnitName") or "",
            "中标人名称": source.get("winningBidderName") or "",
            "合同金额": source.get("contractAmount") or "",
            "合同期限": source.get("contractDuration") or "",
            "合同签署时间": source.get("contractSignTime") or "",
            "合同主要内容": raw_text,
            "发布日期": published,
            "发布网站": config.PLATFORM_NAME,
        })
        return ParsedNotice("htly", "合同与履约", title, published, "合同公示", raw_text, data, [], None)

    @classmethod
    def _correction_data(cls, old: Mapping[str, Any], text: str, category: str, published: str) -> dict[str, Any]:
        data = create_empty_notice_data("更正结果公示", include_parser_diagnostics=True)
        data.update({key: value for key, value in old.items() if key in data and value not in (None, "")})
        data["公共类型"] = {"clarification": "澄清修改", "control_price": "控制价公示", "manager_change": "项目经理(总监)变更"}[category]
        data["公告内容"] = text
        data["发布日期"] = published
        number = cls._value(text, r"(?:项目|招标项目|工程|招标)编号|工程编码")
        data["项目编号"] = number
        data["招标编号"] = number
        data["依据文号"] = number
        if category == "control_price":
            data["依据文件"] = cls._value(text, r"招标控制(?:价)?总价|最高投标限价")
        if category == "clarification":
            changed_open = re.findall(
                r"(?:现统一变更为|现变更为)\s*[：:]\s*"
                r"((?:20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)[^\n，。；;]{0,20}?(?:\d{1,2}\s*[时:]\s*\d{1,2}(?:分)?))",
                text,
            )
            if changed_open:
                data["开标时间"] = changed_open[-1].strip()
        return data

    @classmethod
    def _enrich(cls, data: dict[str, Any], text: str, notice_type: str) -> None:
        data["项目性质"] = data.get("项目性质") or "招标信息"
        if "组织形式" in data:
            data["组织形式"] = data.get("组织形式") or ("委托招标" if "招标代理" in text else "自行招标")
        normalized = text
        replacements = (
            (r"建\s*设\s*单\s*位\s*[（(]\s*招\s*标\s*人\s*[）)]", "招标人"),
            (r"建\s*设\s*单\s*位", "招标人"), (r"招\s*标\s*人", "招标人"),
            (r"招\s*标\s*代\s*理\s*机\s*构", "招标代理机构"),
            (r"地\s*址", "地址"), (r"联\s*系\s*人", "联系人"),
            (r"电\s*话", "电话"),
        )
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized)
        WtjyptParser._fill_party_block(data, normalized, owner=True)
        WtjyptParser._fill_party_block(data, normalized, owner=False)
        cls._fill_ty_party(data, normalized, owner=True)
        cls._fill_ty_party(data, normalized, owner=False)
        if notice_type == "中标候选人公示":
            names = re.findall(r"第[一二三四五六七八九十\d]+名\s*[：:]\s*([^\n，,；;]+)", text)
            prices = re.findall(r"投标报价(?:\s*[（(][^）)]*[）)])?\s*[：:]\s*([^\n，,；;]+)", text)
            if names:
                data["中标候选人名称"] = list(dict.fromkeys(x.strip() for x in names))
            if prices:
                data["中标候选人报价"] = list(dict.fromkeys(x.strip() for x in prices))
            public_time = cls._value(text, r"公示(?:日期|时间|期)")
            if public_time:
                data["公示时间"] = public_time
        if notice_type == "中标结果公示":
            names = re.findall(r"中标(?:单位|人)\s*[：:]\s*([^\n，,；;]+)", text)
            prices = re.findall(r"中标(?:价|金额)(?:\s*[（(][^）)]*[）)])?\s*[：:]\s*([^\n，,；;]+)", text)
            if names:
                data["中标人名称"] = list(dict.fromkeys(x.strip() for x in names))
            if prices:
                data["中标价"] = list(dict.fromkeys(x.strip() for x in prices))

    @classmethod
    def _fill_ty_party(cls, data: dict[str, Any], text: str, *, owner: bool) -> None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        start_re = re.compile(r"^(?:招标人|建设单位(?:（招标人）)?)\s*[：:]") if owner else re.compile(r"^招标代理机构\s*[：:]")
        stop_re = re.compile(r"^招标代理机构\s*[：:]") if owner else re.compile(r"^(?:监督部门|监督单位|招标方式|开标时间|中标候选单位|中标单位)\s*[：:]")
        starts = [index for index, line in enumerate(lines) if start_re.search(line)]
        if not starts:
            return
        begin = starts[-1]
        block = []
        for line in lines[begin:]:
            if block and stop_re.search(line):
                break
            block.append(line)
        name = re.sub(r"^[^：:]+[：:]\s*", "", block[0]).strip()
        address = cls._block_value(block, r"地址")
        contact = cls._block_value(block, r"联系人|项目负责人")
        phone = cls._block_value(block, r"联系电话|联系方式|电话")
        if owner:
            for key in ("招标人/采购人名称", "招标人/采购人", "招标人名称"):
                if key in data and name:
                    data[key] = data.get(key) or name
            keys = ("招标人地址", "招标人联系人", "招标人联系方式")
        else:
            if "招标代理机构" in data and name:
                data["招标代理机构"] = data.get("招标代理机构") or name
            keys = ("招标代理机构地址", "招标代理机构联系人", "招标代理机构联系方式")
        for key, value in zip(keys, (address, contact, phone)):
            if key in data and value:
                # 本站常把联系人、电话排在同一文本行；站点分块结果比通用规则更精确。
                data[key] = value

    @classmethod
    def _block_value(cls, lines: list[str], label: str) -> str:
        for line in lines[1:]:
            match = re.search(rf"(?:{label})\s*[：:]\s*(.+?)(?=\s+(?:联系电话|联系方式|电话|联系人|项目负责人|地址)\s*[：:]|$)", line)
            if match:
                return match.group(1).strip(" ：:；;")
        return ""

    @staticmethod
    def _value(text: str, label: str) -> str:
        match = re.search(rf"(?:{label})\s*[：:]\s*([^\n；;]+)", text)
        return match.group(1).strip(" \t\r：:，,。；;") if match else ""
