"""旺采网六类公告详情的站点专用解析器。"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from crawler_scrapy.sites.bitbid.parser import clean_html
from crawler_scrapy.sites.sxxindian.parser import SxxindianParser
from crawler_scrapy.sites.wc5ibid import config


class Wc5ibidParser(SxxindianParser):
    @classmethod
    def parse(
        cls, category: str, html_text: str, list_record: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]], str, str, str, str]:
        soup = BeautifulSoup(html_text, "html.parser")
        title_node = soup.select_one(".ggxq-info-title")
        title = title_node.get_text(" ", strip=True) if title_node else str(list_record.get("title") or "")
        article = soup.select_one(".zbnr-content")
        if article:
            # Some malformed legacy pages leave the footer nested inside the article.
            for footer in article.select(".wc-r9, footer"):
                footer.decompose()
        raw_html = article.decode_contents() if article else ""
        article_text = clean_html(raw_html)
        article_text = cls._strip_site_footer(article_text)
        params = cls._params(soup)
        timeline = cls._timeline(soup)
        param_text = "\n".join(f"{k}：{v}" for k, v in params.items() if v and v != "-")
        text = "\n".join(x for x in (param_text, article_text) if x)
        publish_time = cls._publish_time_5ibid(soup, list_record)
        notice_type = cls._notice_type_5ibid(category, title, article_text)
        detail = {
            "title": title,
            "publish_time": publish_time,
            "module": "bidding",
            "category": "tender",
            "project_type": "all",
            "source_method": cls.procurement_method(title, title, article_text),
        }

        if notice_type in {"招标公告", "资格预审公告"}:
            # 复用已经过真实样本校验的中文公告段落规则，再用页面结构化参数优先补全。
            adapted = dict(detail, module="purchase", category="tender")
            data = cls._tender(adapted, text, prequalification=notice_type == "资格预审公告")
            data["项目性质"] = "招标信息"
            data["源站公告性质"] = config.CATEGORIES[category]["label"]
            cls._merge_tender_params(data, params)
        elif notice_type == "中标候选人公示":
            data = cls._candidate(dict(detail, category="candidate"), text, raw_html)
            data["源站公告性质"] = config.CATEGORIES[category]["label"]
            cls._merge_common_params(data, params, candidate=True)
        elif notice_type == "中标结果公示":
            data = cls._award(dict(detail, category="award"), text, raw_html)
            data["源站公告性质"] = config.CATEGORIES[category]["label"]
            cls._merge_common_params(data, params, candidate=False)
        else:
            adapted = dict(detail, module="purchase", category="change")
            data = cls._correction(adapted, text)
            data["公共类型"] = config.CATEGORIES[category]["label"]
            data["项目名称"] = params.get("招标项目名称") or params.get("项目名称") or cls._project_name(title)
            data["所属行业"] = params.get("所属行业", "")
            data["组织形式"] = params.get("招标组织形式", "")
            data["依据文号"] = params.get("招标项目编号") or cls._number(text)

        cls._sanitize_owner_address(data, params, article_text)

        if timeline.get("开标时间"):
            data["开标时间"] = timeline["开标时间"]

        data["发布网站"] = config.PLATFORM_NAME
        attachments = cls.attachments_5ibid(soup)
        subtype = notice_type
        return notice_type, data, attachments, raw_html, text, title, publish_time

    @staticmethod
    def _notice_type_5ibid(category: str, title: str, text: str) -> str:
        if category == "zbhxgs":
            return "中标候选人公示"
        if category == "zbjg":
            return "中标结果公示"
        if category in {"kzj", "bggg", "fbgg"}:
            return "更正结果公示"
        source = f"{title}\n{text[:500]}"
        return "资格预审公告" if "资格预审" in source else "招标公告"

    @staticmethod
    def _publish_time_5ibid(soup: BeautifulSoup, record: Mapping[str, Any]) -> str:
        node = soup.select_one(".fb-time")
        if node:
            match = re.search(r"发布时间\s*[：:]\s*(.+)", node.get_text(" ", strip=True))
            if match:
                return match.group(1).strip()
        return str(record.get("date") or "")

    @staticmethod
    def _params(soup: BeautifulSoup) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in soup.select(".ggxq-params-wrap .row-line"):
            name_node = row.select_one(".ggxq-param-name")
            if not name_node:
                continue
            name = name_node.get_text(" ", strip=True).strip("：: ")
            name_node.extract()
            value = row.get_text(" ", strip=True).strip("：: ")
            if name and value and value != "-":
                result[name] = value
        return result

    @staticmethod
    def _timeline(soup: BeautifulSoup) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in soup.select(".ht-row"):
            title_node = row.select_one(".ht-desc-title")
            if not title_node:
                continue
            title = title_node.get_text(" ", strip=True).strip("：: ")
            date_node = row.select_one(".ht-time-hour")
            time_node = row.select_one(".ht-time-date")
            shown = " ".join(
                x for x in (
                    date_node.get_text(" ", strip=True) if date_node else "",
                    time_node.get_text(" ", strip=True) if time_node else "",
                ) if x
            )
            desc_node = row.select_one(".ht-desc-con")
            desc = desc_node.get_text(" ", strip=True) if desc_node else ""
            full_year = re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", desc)
            value = full_year.group(0) if full_year else shown
            short_year = re.fullmatch(r"(\d{2})/(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}:\d{2}(?::\d{2})?))?", value)
            if short_year:
                year, month, day, clock = short_year.groups()
                value = f"20{year}/{month}/{day}" + (f" {clock}" if clock else "")
            if title and value:
                result[title] = value
        return result

    @staticmethod
    def _strip_site_footer(text: str) -> str:
        markers = ("\n关于我们\n", "\n联系我们\n", "\n公司地址： 中国江苏省南京市江宁区")
        positions = [text.find(marker) for marker in markers if text.find(marker) >= 0]
        return text[:min(positions)].rstrip() if positions else text

    @staticmethod
    def _sanitize_owner_address(
        data: dict[str, Any], params: Mapping[str, str], article_text: str
    ) -> None:
        """Never treat the project location as the owner's postal address."""
        explicit = (
            params.get("招标人地址")
            or params.get("采购人地址")
            or params.get("业主单位地址")
        )
        if explicit:
            data["招标人地址"] = explicit
            return
        project_address = params.get("招标项目地址") or params.get("项目地址")
        owner_address = str(data.get("招标人地址") or "").strip()
        body_has_owner_address = bool(
            re.search(r"(?:招标人|采购人|业主单位)(?:联系)?地址\s*[：:]", article_text)
        )
        if project_address and owner_address == project_address and not body_has_owner_address:
            data["招标人地址"] = ""

    @staticmethod
    def _merge_tender_params(data: dict[str, Any], params: Mapping[str, str]) -> None:
        mapping = {
            "项目名称": ("招标项目名称", "项目名称"),
            "项目编号/招标编号": ("招标项目编号", "项目编号"),
            "所属行业": ("所属行业",),
            "项目类型/行业分类": ("所属行业",),
            "项目地点": ("招标项目地址", "项目地址"),
            "组织形式": ("招标组织形式",),
            "招标代理机构": ("招标代理机构名称",),
            "递交截止时间": ("投标截止时间",),
            "招标人/采购人名称": ("业主单位", "招标人"),
        }
        for field, keys in mapping.items():
            value = next((params.get(k, "") for k in keys if params.get(k)), "")
            if value:
                data[field] = value

    @staticmethod
    def _merge_common_params(data: dict[str, Any], params: Mapping[str, str], *, candidate: bool) -> None:
        data["项目名称"] = params.get("招标项目名称") or params.get("项目名称") or data.get("项目名称", "")
        data["所属行业"] = params.get("所属行业") or params.get("标段分类") or data.get("所属行业", "")
        data["组织形式"] = params.get("招标组织形式") or data.get("组织形式", "")
        number_key = "招标编号/项目编号" if candidate else "依据文号"
        data[number_key] = params.get("招标项目编号") or data.get(number_key, "")
        data["招标人/采购人"] = params.get("业主单位") or data.get("招标人/采购人", "")
        data["招标代理机构"] = params.get("招标代理机构名称") or data.get("招标代理机构", "")
        if candidate and params.get("公示结束时间"):
            data["公示时间"] = data.get("公示时间") or f"至 {params['公示结束时间']}"

    @staticmethod
    def attachments_5ibid(soup: BeautifulSoup) -> list[dict[str, Any]]:
        result, seen = [], set()
        for anchor in soup.select(".xgfj-wrap a[href], .ggxq-content-wrap a[href]"):
            href = str(anchor.get("href") or "").strip()
            path = urlparse(href).path.lower()
            if not href or not (
                "showfilecontent" in path
                or re.search(r"\.(?:pdf|docx?|xlsx?|pptx?|wps|ofd|zip|rar|7z)(?:$|\?)", href, re.I)
            ):
                continue
            url = urljoin(config.BASE_URL, href)
            if url in seen:
                continue
            name = anchor.get("title") or anchor.get_text(" ", strip=True) or PurePosixPath(path).name or "附件"
            result.append({"file_name": str(name), "file_url": url, "source": "detail_attachment"})
            seen.add(url)
        return result
