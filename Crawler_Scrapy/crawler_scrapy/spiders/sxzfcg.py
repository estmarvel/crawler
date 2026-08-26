"""Shanxi government procurement public announcement crawler."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.sxzfcg import config
from crawler_scrapy.sites.sxzfcg.parser import SxzfcgParser, parse_list_records
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class SxzfcgSpider(BaseNoticeSpider):
    name = "sxzfcg"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.ccgp-shanxi.gov.cn"]
    parser_version = SxzfcgParser.parser_version
    extraction_model_name = "sxzfcg-zcy-public-json-html-rule-parser"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "COOKIES_ENABLED": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 2.5,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": False,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": False,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
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
        max_records: int | str = 1_000_000,
        max_pages: int | str = 10_000,
        page_size: int | str = 20,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        keyword: str = "",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.categories = self._select_categories(categories)
        self.max_records = self._positive_int(max_records, 1_000_000)
        self.max_pages = self._positive_int(max_pages, 10_000)
        self.page_size = min(self._positive_int(page_size, 20), 100)
        selected_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._time_window(
            selected_days, start_date, end_date
        )
        self.keyword = str(keyword or "").strip()
        self._counts = {category: 0 for category in self.categories}
        self._seen: set[str] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _select_categories(value: str | None) -> tuple[str, ...]:
        selected = tuple(
            part.strip() for part in str(value or "").split(",") if part.strip()
        ) or tuple(config.DEFAULT_CATEGORIES)
        invalid = [part for part in selected if part not in config.CATEGORIES]
        if invalid:
            raise ValueError(f"不支持的山西政府采购公告类别：{','.join(invalid)}")
        return selected

    @staticmethod
    def _boundary(value: str | None, *, end_of_day: bool) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = coerce_datetime(raw)
        if parsed is None:
            raise ValueError(f"无法解析日期时间：{raw}")
        if end_of_day and len(raw) == 10:
            return datetime.combine(parsed.date(), time.max)
        return parsed

    @classmethod
    def _time_window(cls, days, start_date, end_date):
        start = cls._boundary(start_date, end_of_day=False)
        end = cls._boundary(end_date, end_of_day=True)
        if start is None and days not in (None, ""):
            count = int(days)
            if count <= 0:
                raise ValueError("days/lookback_days 必须大于0")
            start = (end or datetime.now()) - timedelta(days=count)
        if start and end and start > end:
            raise ValueError("start_date不能晚于end_date")
        return start, end

    @staticmethod
    def _headers(*, referer: str = "") -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": config.WEB_BASE_URL,
            "Referer": referer or f"{config.WEB_BASE_URL}/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }

    def start_requests(self):
        for category in self.categories:
            yield self._list_request(category, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _list_request(self, category: str, page: int) -> Request:
        payload = config.list_payload(
            category,
            page,
            self.page_size,
            keyword=self.keyword,
        )
        return Request(
            config.LIST_URL,
            method="POST",
            body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"category": category, "page": page, "payload": payload},
            dont_filter=True,
        )

    @staticmethod
    def _published(value: str) -> datetime | None:
        return coerce_datetime(value)

    def _inside_window(self, value: str) -> bool:
        published = self._published(value)
        return not published or (
            (not self.window_start or published >= self.window_start)
            and (not self.window_end or published <= self.window_end)
        )

    def parse_list(self, response: Response, category: str, page: int, payload: Mapping[str, Any]):
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"山西政府采购列表接口返回非 JSON：{response.url}") from exc
        records = parse_list_records(data, category)
        reached_before_window = False
        for record in records:
            published = self._published(record.publish_time)
            if self.window_start and published and published < self.window_start:
                reached_before_window = True
            if not self._inside_window(record.publish_time):
                continue
            if self._counts[category] >= self.max_records:
                break
            if record.article_id in self._seen:
                continue
            self._seen.add(record.article_id)
            definition = config.CATEGORIES[category]
            list_record = {
                "article_id": record.article_id,
                "title": record.title,
                "publish_time": record.publish_time,
                "source_category": category,
                "source_code": definition["code"],
                "source_label": definition["label"],
                "source_type": record.source_type,
                "purchaser": record.purchaser,
                "purchase_method": record.purchase_method,
                "district_name": record.district_name,
                "project_code": record.project_code,
                "project_name": record.project_name,
                "list_payload": dict(payload),
            }
            detail_url = config.detail_page_url(record.article_id)
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=record.article_id,
                list_record=list_record,
                detail_url=detail_url,
                notice_type=definition["schema"],
                title=record.title,
                publish_time=record.publish_time,
            )
            if not should_fetch:
                continue
            self._counts[category] += 1
            yield Request(
                config.detail_api_url(record.article_id),
                headers=self._headers(referer=detail_url),
                callback=self.parse_detail,
                cb_kwargs={
                    "category": category,
                    "list_record": list_record,
                    "list_fingerprint": fingerprint,
                },
                dont_filter=True,
            )

        if (
            records
            and not reached_before_window
            and self._counts[category] < self.max_records
            and page < self.max_pages
        ):
            yield self._list_request(category, page + 1)

    def parse_detail(
        self,
        response: Response,
        category: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
    ):
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"山西政府采购详情接口返回非 JSON：{response.url}") from exc
        parsed = SxzfcgParser.parse(category, data, list_record=list_record)
        notice_id = str(list_record.get("article_id") or "")
        detail_page_url = config.detail_page_url(notice_id)
        response_meta = self.build_response_metadata(
            response,
            request_kind="detail_api",
            context={
                "category": category,
                "articleId": notice_id,
                "detailPageUrl": detail_page_url,
            },
        )
        yield self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_subtype=category,
            notice_id=notice_id,
            title=parsed.title,
            publish_time=parsed.publish_time,
            detail_url=detail_page_url,
            data=parsed.data,
            raw_data={
                "list": dict(list_record),
                "detail": parsed.structured,
                "sourceCategory": category,
                "validationWarnings": parsed.validation_warnings,
            },
            raw_html=parsed.raw_html,
            raw_text=parsed.raw_text,
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            field_meta={
                "site_parser": self.extraction_model_name,
                "source_category": category,
                "source_code": config.CATEGORIES[category]["code"],
                "validation_warnings": parsed.validation_warnings,
            },
            response_metadata=response_meta,
            attachments=parsed.attachments,
        )
