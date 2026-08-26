"""云买卖详情 HTML 与结构化 note 字段解析。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape
from typing import Any, Mapping

from bs4 import BeautifulSoup

from crawler_scrapy.sites.bitbid.parser import clean_html
from crawler_scrapy.sites.wtjypt.parser import WtjyptParser
from crawler_scrapy.sites.eqbidding import config


class EqbiddingParser(WtjyptParser):
    @staticmethod
    def _time(value: Any) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = int(value)
            if number > 10_000_000_000:
                number //= 1000
            return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
        return str(value).strip()

    @staticmethod
    def _nested_note(value: Any) -> dict[str, Any]:
        if not value:
            return {}
        try:
            root = json.loads(value) if isinstance(value, str) else dict(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        result: dict[str, Any] = {}
        for key in ("tender", "notice"):
            part = root.get(key)
            try:
                part = json.loads(part) if isinstance(part, str) else part
            except (TypeError, ValueError, json.JSONDecodeError):
                part = None
            if isinstance(part, Mapping):
                result.update(part)
        return result

    @classmethod
    def parse(cls, category: str, payload: Mapping[str, Any], list_record: Mapping[str, Any]):
        source = dict(list_record)
        source.update(payload)
        raw_html = str(source.get("notice_content") or "")
        raw_text = clean_html(raw_html)
        # other_content 常是 notice_content 的无换行副本。正文存在时再次拼接会把
        # “采购人/代理机构”两个联系块粘成一行，造成地址越界污染。
        if not raw_html and source.get("other_content"):
            other = unescape(str(source["other_content"]))
            if other.strip() and other.strip() not in raw_text:
                raw_text = f"{raw_text}\n{other}".strip()
        title = str(source.get("notice_title") or source.get("project_name") or "").strip()
        published = cls._time(source.get("notice_release_time") or source.get("created"))
        nested = cls._nested_note(source.get("note"))
        detail = {"title": title, "publish_time": published, "module": "bidding",
                  "category": category, "project_type": "all", "source_method": ""}

        if category == "tender":
            notice_type = "资格预审公告" if "资格预审" in title else "招标公告"
            if re.search(r"变更|更正|延期|撤销|终止|废标|流标|补充|控制价", title + str(source.get("notice_nature") or "")):
                notice_type = "更正结果公示"
                data = cls._correction(detail, raw_text)
                data["公共类型"] = str(source.get("notice_nature") or "变更公告")
                data["依据文号"] = str(source.get("project_item_code") or data.get("依据文号") or "")
            else:
                data = cls._tender(detail, raw_text, prequalification=notice_type == "资格预审公告")
                cls._merge_tender(data, source, nested)
        elif category == "candidate":
            notice_type = "中标候选人公示"
            data = cls._candidate(detail, raw_text, raw_html)
            details = cls._ranked_candidate_details(raw_text)
            if details:
                data["中标候选人名称"] = [x["候选人名称"] for x in details]
                data["中标候选人报价"] = [x["候选人报价"] for x in details]
                data["中标候选人明细"] = details
            cls._merge_result(data, source, nested, candidate=True)
        else:
            notice_type = "中标结果公示"
            data = cls._award(detail, raw_text, raw_html)
            cls._merge_result(data, source, nested, candidate=False)

        cls._fill_site_specific_fields(data, raw_text, notice_type)
        if category == "award":
            cls._refine_award(data, raw_text, raw_html)
        cls._refine_parties(data, raw_text)
        data["发布网站"] = config.PLATFORM_NAME
        data["源站公告性质"] = str(source.get("notice_type") or config.CATEGORIES[category][0])
        return notice_type, data, raw_html, raw_text, title, published, []

    @classmethod
    def _merge_tender(cls, data: dict[str, Any], src: Mapping[str, Any], nested: Mapping[str, Any]) -> None:
        data["项目性质"] = "招标信息"
        data["项目名称"] = src.get("project_name") or data.get("项目名称")
        data["项目编号/招标编号"] = src.get("project_item_code") or data.get("项目编号/招标编号")
        data["项目地点"] = src.get("region_name") or nested.get("delivery_place") or data.get("项目地点")
        data["招标人/采购人名称"] = src.get("org_name") or data.get("招标人/采购人名称")
        data["组织形式"] = data.get("组织形式") or "委托招标"
        data["预审文件获取时间"] = cls._range(nested.get("apply_date_begin"), nested.get("apply_date_end")) or data.get("预审文件获取时间")
        data["获取方式"] = nested.get("bid_file_obtain_way") or data.get("获取方式")
        data["递交截止时间"] = cls._time(nested.get("bid_deadline")) or data.get("递交截止时间")
        data["递交方法"] = nested.get("bid_send_form") or data.get("递交方法")
        opening = cls._time(nested.get("bid_open_date"))
        data["开标时间"] = opening or data.get("开标时间")
        data["开启时间"] = opening or data.get("开启时间")
        data["开启地点"] = nested.get("bid_open_place") or nested.get("bid_place") or data.get("开启地点")
        data["开启方式"] = src.get("open_type") or nested.get("bid_open_type") or data.get("开启方式")
        data["工期/服务期/供货日期"] = nested.get("delivery_time_limit") or data.get("工期/服务期/供货日期")
        data["质量要求"] = nested.get("quality") or data.get("质量要求")
        condition = clean_html(unescape(str(src.get("notice_condition") or "")))
        if condition:
            data["申请人资格要求/投标人资格要求"] = condition

    @classmethod
    def _merge_result(cls, data: dict[str, Any], src: Mapping[str, Any], nested: Mapping[str, Any], *, candidate: bool) -> None:
        data["项目性质"] = "招标信息"
        data["项目名称"] = src.get("project_name") or data.get("项目名称")
        number_key = "招标编号/项目编号" if candidate else "依据文号"
        data[number_key] = src.get("project_item_code") or data.get(number_key)
        data["招标人/采购人"] = src.get("org_name") or data.get("招标人/采购人")
        data["组织形式"] = data.get("组织形式") or "委托招标"
        data["开标时间"] = cls._time(nested.get("bid_open_date")) or data.get("开标时间")
        if candidate:
            data["公示时间"] = cls._range(src.get("notice_release_time"), src.get("notice_end_time")) or data.get("公示时间")

    @classmethod
    def _range(cls, start: Any, end: Any) -> str:
        values = [cls._time(v) for v in (start, end) if v not in (None, "")]
        return " 至 ".join(x for x in values if x)

    @classmethod
    def _refine_award(cls, data: dict[str, Any], text: str, raw_html: str) -> None:
        """适配该站自制模板中的“成交供应商/中选单位”等结果标签。"""
        names: list[str] = []
        prices: list[str] = []
        soup = BeautifulSoup(raw_html, "html.parser")
        for table in soup.select("table"):
            rows = [[cell.get_text(" ", strip=True) for cell in row.select("th,td")] for row in table.select("tr")]
            if not rows:
                continue
            header = "|".join(rows[0]).replace(" ", "")
            if not re.search(r"(?:中标|成交|中选).*(?:人|单位|供应商|服务商).*?(?:报价|金额|价格|价)", header):
                continue
            for row in rows[1:]:
                if len(row) >= 3:
                    name, price = cls._clean_value(row[-2]), cls._clean_value(row[-1])
                    if name and name not in names:
                        names.append(name)
                    if price and price not in prices:
                        prices.append(price)
        for value in re.findall(
            r"(?:中标人|中标单位|成交供应商|成交服务商|成交单位|中选单位|供应商)(?:名称)?\s*[：:]\s*([^\n\t]+)",
            text,
        ):
            value = re.split(r"\s*(?:中标价格|中标价|成交金额|成交价|响应报价|投标报价|报价)\s*[：:]?", value, maxsplit=1)[0]
            cleaned = cls._clean_value(value)
            if cleaned and cleaned not in names:
                names.append(cleaned)
        for value in re.findall(
            r"(?:中标|成交|响应|投标)(?:（成交）)?(?:价格|价|金额|报价)(?:\([^)]*\)|（[^）]*）)?\s*[：:]\s*([^\n\t]+)",
            text,
        ):
            cleaned = cls._clean_value(value)
            if cleaned and cleaned not in prices:
                prices.append(cleaned)
        if names:
            data["中标人名称"] = names
        if prices:
            data["中标价"] = prices

    @classmethod
    def _refine_parties(cls, data: dict[str, Any], text: str) -> None:
        """避开正文末尾“采购人或代理机构负责人”签章句造成的伪联系块。"""
        specs = (
            (r"(?:招标人|采购人)(?!或)(?:信息|名称)?", r"(?:招标代理机构|采购代理机构(?:机构)?|采购代理|监督部门)",
             "招标人/采购人", "招标人地址", "招标人联系人", "招标人联系方式"),
            (r"(?:招标代理机构|采购代理机构(?:机构)?|采购代理)(?:信息|名称)?", r"(?:监督部门|项目联系方式|异议|$)",
             "招标代理机构", "招标代理机构地址", "招标代理机构联系人", "招标代理机构联系方式"),
        )
        for start, stop, name_key, address_key, contact_key, phone_key in specs:
            matches = list(re.finditer(rf"(?m)^\s*{start}\s*[：:]\s*([^\n]+)(.*?)(?=^\s*{stop}\s*[：:]|\Z)", text, re.S))
            valid = [m for m in matches if len(cls._clean_value(m.group(1))) <= 100]
            if not valid:
                continue
            match = valid[0]
            name, block = cls._clean_value(match.group(1)), match.group(2)
            address = cls._label_value(block, r"地\s*址")
            contact = cls._label_value(block, r"联\s*系\s*人")
            phone = cls._label_value(block, r"(?:联系电话|联系方式|电\s*话)")
            if name:
                data[name_key] = name
                if name_key == "招标人/采购人" and "招标人/采购人名称" in data:
                    data["招标人/采购人名称"] = name
            if address:
                data[address_key] = address
            if contact:
                data[contact_key] = contact
            if phone:
                data[phone_key] = phone
