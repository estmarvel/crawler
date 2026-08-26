"""旺采网六类公开招标公告 Spider。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.wc5ibid import config
from crawler_scrapy.sites.wc5ibid.parser import Wc5ibidParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class Wc5ibidSpider(BaseNoticeSpider):
    name = "wc5ibid"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.5ibid.net", "5ibid.net"]
    parser_version = "wc5ibid-v1-gbk-site-html"
    extraction_model_name = "wc5ibid-site-rule-parser"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.6,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines["crawler_scrapy.sites.wc5ibid.exporter.Wc5ibidMultiFormatPipeline"] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

    def __init__(
        self,
        categories: str | None = None,
        max_records: int | str = 200,
        max_pages: int | str = 100,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.categories = self._parse_categories(categories)
        self.max_records = self._positive_int(max_records, 200)
        self.max_pages = self._positive_int(max_pages, 100)
        requested_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._parse_window(requested_days, start_date, end_date)
        if self.window_start is None:
            self.window_start = datetime.now() - timedelta(days=365)
        if self.window_end is None:
            self.window_end = datetime.now()
        self._scheduled: dict[str, int] = defaultdict(int)
        self._seen: set[tuple[str, str]] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _parse_categories(value: str | None) -> tuple[str, ...]:
        selected = tuple(x.strip() for x in str(value or "").split(",") if x.strip()) or config.DEFAULT_CATEGORIES
        invalid = [x for x in selected if x not in config.CATEGORIES]
        if invalid:
            raise ValueError(f"不支持的 categories: {','.join(invalid)}")
        return selected

    @staticmethod
    def _boundary(value: str | None, *, end: bool) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = coerce_datetime(raw)
        if parsed is None:
            raise ValueError(f"无法解析日期 {raw!r}")
        return datetime.combine(parsed.date(), time.max) if end and len(raw) == 10 else parsed

    @classmethod
    def _parse_window(cls, days, start_date, end_date):
        start, end = cls._boundary(start_date, end=False), cls._boundary(end_date, end=True)
        if start is None and days not in (None, ""):
            start = (end or datetime.now()) - timedelta(days=int(days))
        if start and end and start > end:
            raise ValueError("start_date 不能晚于 end_date")
        return start, end

    def start_requests(self):
        for category in self.categories:
            yield self._list_request(category, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _list_request(self, category: str, page: int) -> Request:
        return Request(
            config.list_url(category, page),
            callback=self.parse_list,
            cb_kwargs={"category": category, "page": page},
            dont_filter=True,
        )

    @staticmethod
    def _list_records(response: Response) -> list[dict[str, Any]]:
        records = []
        for item in response.css("li.select-search-item"):
            href = item.css("a::attr(href)").get("").strip()
            if "Detail/" not in href:
                continue
            title = " ".join(item.css(".select-search-title ::text").getall()).strip()
            title = " ".join(title.split())
            desc = [" ".join(x.css("::text").getall()).strip() for x in item.css(".select-item-desc span")]
            date = next((x for x in reversed(desc) if coerce_datetime(x)), "")
            project_node = item.xpath("preceding-sibling::div[contains(@class,'item-title-cc')][1]")
            project_text = " ".join(project_node.css("::text").getall()).strip() if project_node else ""
            project_no = project_text.split("：", 1)[-1].strip() if "：" in project_text else ""
            records.append({
                "href": href,
                "title": title,
                "date": date,
                "project_no": project_no,
                "owner": desc[0] if len(desc) > 0 else "",
                "industry": desc[1] if len(desc) > 1 else "",
                "region": desc[2] if len(desc) > 2 else "",
            })
        return records

    @staticmethod
    def _notice_id(category: str, record: Mapping[str, Any]) -> str:
        path = urlparse(str(record.get("href") or "")).path
        parts = [x for x in path.split("/") if x]
        detail_at = next((i for i, x in enumerate(parts) if x.endswith("Detail")), -1)
        candidate = "-".join(parts[detail_at + 1:]).removesuffix(".html") if detail_at >= 0 else ""
        return candidate or hashlib.sha1(f"{category}|{path}".encode()).hexdigest()

    def parse_list(self, response: Response, category: str, page: int):
        records = self._list_records(response)
        reached_older = False
        for raw in records:
            published = coerce_datetime(raw.get("date"))
            if published and published < self.window_start:
                reached_older = True
                continue
            if published and published > self.window_end:
                continue
            if self._scheduled[category] >= self.max_records:
                break
            notice_id = self._notice_id(category, raw)
            identity = (category, notice_id)
            if identity in self._seen:
                continue
            self._seen.add(identity)
            detail_url = urljoin(config.BASE_URL, raw["href"])
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=notice_id,
                notice_type=config.CATEGORIES[category]["label"],
                list_record=raw,
                detail_url=detail_url,
                title=str(raw.get("title") or ""),
                publish_time=published,
            )
            if not should_fetch:
                continue
            self._scheduled[category] += 1
            yield Request(
                detail_url,
                callback=self.parse_detail,
                cb_kwargs={"category": category, "list_record": raw, "list_fingerprint": fingerprint},
            )

        if (
            records and not reached_older and page < self.max_pages
            and self._scheduled[category] < self.max_records
        ):
            yield self._list_request(category, page + 1)

    def parse_detail(self, response: Response, category: str, list_record: Mapping[str, Any], list_fingerprint: str):
        notice_type, data, attachments, raw_html, raw_text, title, publish_time = Wc5ibidParser.parse(
            category, response.text, list_record
        )
        if list_record.get("project_no"):
            if notice_type in {"招标公告", "资格预审公告"} and not data.get("项目编号/招标编号"):
                data["项目编号/招标编号"] = list_record["project_no"]
            elif notice_type == "中标候选人公示" and not data.get("招标编号/项目编号"):
                data["招标编号/项目编号"] = list_record["project_no"]
            elif notice_type in {"中标结果公示", "更正结果公示"} and not data.get("依据文号"):
                data["依据文号"] = list_record["project_no"]
        yield self.build_notice_item(
            notice_type=notice_type,
            notice_subtype=f"wc5ibid|{config.CATEGORIES[category]['label']}",
            notice_id=self._notice_id(category, list_record),
            title=title or str(list_record.get("title") or ""),
            publish_time=publish_time,
            detail_url=response.url,
            data=data,
            raw_data={"category": category, "list": dict(list_record)},
            raw_html=raw_html,
            raw_text=raw_text,
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            attachments=attachments,
        )

