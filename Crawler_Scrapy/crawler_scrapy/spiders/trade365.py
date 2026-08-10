"""中招联合（山西）公开招标公告爬虫。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.trade365 import config
from crawler_scrapy.sites.trade365.parser import (
    Trade365Parser,
    classify_category,
    parse_list_records,
    parse_page_info,
)
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class Trade365Spider(BaseNoticeSpider):
    name = "trade365"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["shanxi.365trade.com.cn"]
    parser_version = Trade365Parser.parser_version
    extraction_model_name = "trade365-public-html-rule-parser"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 1.0,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": False,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": False,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines[
            "crawler_scrapy.sites.trade365.exporter.Trade365MultiFormatPipeline"
        ] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

        mode = str(settings.get("CRAWLER_OUTBOUND_MODE", "direct")).strip().lower()
        if mode not in {"direct", "static"}:
            raise ValueError(
                f"不支持的 CRAWLER_OUTBOUND_MODE={mode!r}；可选 direct/static"
            )
        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        static_middleware = (
            "crawler_scrapy.transport.proxy_middleware.StaticProxyMiddleware"
        )
        middlewares[static_middleware] = 610 if mode == "static" else None
        middlewares[
            "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
        ] = 650
        settings.set("DOWNLOADER_MIDDLEWARES", middlewares, priority="spider")
        settings.set("STATIC_PROXY_ENABLED", mode == "static", priority="spider")
        settings.set("HTTPPROXY_ENABLED", mode == "static", priority="spider")

    def __init__(
        self,
        categories: str | None = None,
        project_types: str | None = None,
        max_records: int | str = 200,
        page_size: int | str = 11,
        max_pages: int | str = 1000,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.feeds = self._select_feeds(categories, project_types)
        self.max_records = self._positive_int(max_records, 200)
        # 源站是固定 11 条/页；保留参数仅用于统一运行器兼容和日志溯源。
        self.page_size = self._positive_int(page_size, 11)
        self.max_pages = self._positive_int(max_pages, 1000)
        selected_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._time_window(
            selected_days, start_date, end_date
        )
        self._counts = {feed: 0 for feed in self.feeds}
        self._seen: set[str] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _select_feeds(
        categories: str | None, project_types: str | None
    ) -> tuple[str, ...]:
        cats = (
            {part.strip() for part in categories.split(",") if part.strip()}
            if categories else {"tender", "change", "candidate", "award"}
        )
        types = (
            {part.strip() for part in project_types.split(",") if part.strip()}
            if project_types else set(config.PROJECT_TYPES)
        )
        invalid = (cats - set(config.CATEGORY_SECTIONS)) | (
            types - set(config.PROJECT_TYPES)
        )
        if invalid:
            raise ValueError(f"不支持的栏目/项目类型：{sorted(invalid)}")
        return tuple(
            feed for feed in config.DEFAULT_FEEDS
            if feed.split(".", 1)[0] in cats and feed.split(".", 1)[1] in types
        )

    @staticmethod
    def _boundary(value: str | None, end: bool) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = coerce_datetime(raw)
        if not parsed:
            raise ValueError(f"无法解析日期：{raw}")
        return datetime.combine(parsed.date(), time.max) if end and len(raw) == 10 else parsed

    @classmethod
    def _time_window(cls, days, start_date, end_date):
        start, end = cls._boundary(start_date, False), cls._boundary(end_date, True)
        if start is None and days not in (None, ""):
            count = int(days)
            if count <= 0:
                raise ValueError("days/lookback_days 必须大于0")
            start = (end or datetime.now()) - timedelta(days=count)
        if start and end and start > end:
            raise ValueError("start_date不能晚于end_date")
        return start, end

    @staticmethod
    def _headers(referer: str | None = None) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": referer or f"{config.WEB_BASE_URL}/zbgg/index.jhtml",
        }

    def start_requests(self):
        for feed in self.feeds:
            yield self._list_request(feed, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _list_request(self, feed: str, page: int) -> Request:
        return Request(
            config.list_url(feed, page),
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"feed": feed, "page": page},
            dont_filter=True,
        )

    @staticmethod
    def _published(record: Mapping[str, Any]) -> datetime | None:
        return coerce_datetime(record.get("publish_time"))

    def _inside_window(self, record: Mapping[str, Any]) -> bool:
        value = self._published(record)
        return not value or (
            (not self.window_start or value >= self.window_start)
            and (not self.window_end or value <= self.window_end)
        )

    @staticmethod
    def _matches_feed(source_category: str, title: str) -> bool:
        actual, _ = classify_category(source_category, title)
        if source_category == "change":
            return True
        return actual == source_category

    def parse_list(self, response: Response, feed: str, page: int):
        source_category, project_type = feed.split(".", 1)
        records = parse_list_records(response.body)
        reached_before_window = False
        for parsed in records:
            record = {
                "id": parsed.notice_id,
                "title": parsed.title,
                "publish_time": parsed.publish_time,
                "detail_url": parsed.detail_url,
                "project_type": parsed.project_type,
                "source_feed": feed,
                "source_page": page,
            }
            published = self._published(record)
            if self.window_start and published and published < self.window_start:
                reached_before_window = True
            if not self._inside_window(record) or not self._matches_feed(
                source_category, parsed.title
            ):
                continue
            if (
                not parsed.notice_id
                or parsed.notice_id in self._seen
                or self._counts[feed] >= self.max_records
            ):
                continue
            self._seen.add(parsed.notice_id)
            actual_category, notice_type = classify_category(
                source_category, parsed.title
            )
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=parsed.notice_id,
                list_record=record,
                detail_url=parsed.detail_url,
                notice_type=notice_type,
                title=parsed.title,
                publish_time=published,
            )
            if not should_fetch:
                continue
            self._counts[feed] += 1
            yield Request(
                parsed.detail_url,
                headers=self._headers(response.url),
                callback=self.parse_detail,
                cb_kwargs={
                    "feed": feed,
                    "list_record": record,
                    "list_fingerprint": fingerprint,
                    "predicted_category": actual_category,
                },
            )

        _, _, total_pages = parse_page_info(response.body)
        if (
            records
            and not reached_before_window
            and page < min(total_pages or page, self.max_pages)
            and self._counts[feed] < self.max_records
        ):
            yield self._list_request(feed, page + 1)

    def parse_detail(
        self,
        response: Response,
        feed: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
        predicted_category: str,
    ):
        parsed = Trade365Parser.parse(feed, response.body, list_record=list_record)
        if parsed.category != predicted_category:
            self.crawler.stats.inc_value("trade365/detail_category_corrected")
        project_type = feed.split(".", 1)[1]
        subtype = f"{parsed.category}.{project_type}"
        notice_id = str(list_record.get("id") or "")
        yield self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_subtype=subtype,
            notice_id=notice_id,
            title=parsed.title,
            publish_time=parsed.publish_time,
            detail_url=response.url,
            data=parsed.data,
            raw_data={
                "list": dict(list_record),
                "sourceFeed": feed,
                "actualCategory": parsed.category,
            },
            raw_html=response.body,
            raw_text=parsed.raw_text,
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            field_meta={
                "site_parser": self.extraction_model_name,
                "source_feed": feed,
                "source_category": feed.split(".", 1)[0],
                "actual_category": parsed.category,
                "project_type": config.PROJECT_TYPES[project_type][0],
                "validation_warnings": list(parsed.validation_warnings),
            },
            response_metadata=self.build_response_metadata(
                response,
                request_kind="detail_html",
                context={"feed": feed, "noticeId": notice_id},
            ),
            attachments=parsed.attachments,
        )
