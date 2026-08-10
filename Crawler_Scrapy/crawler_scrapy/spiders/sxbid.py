"""山西省招标投标公共服务平台八类公开公告爬虫。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping

from scrapy import FormRequest, Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.sxbid import config
from crawler_scrapy.sites.sxbid.parser import (
    ParsedPage,
    SxbidParser,
    extract_pdf_text,
    parse_detail_page,
    parse_list_records,
    parse_page_info,
)
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class SxbidSpider(BaseNoticeSpider):
    name = "sxbid"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.sxbid.com.cn", "sxbid.com.cn"]
    parser_version = SxbidParser.parser_version
    extraction_model_name = "sxbid-public-html-pdf-rule-parser"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 3.0,
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
            "crawler_scrapy.sites.sxbid.exporter.SxbidMultiFormatPipeline"
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
        max_records: int | str = 200,
        page_size: int | str = 100,
        max_pages: int | str = 10_000,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.categories = self._select_categories(categories)
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, 100), 100)
        self.max_pages = self._positive_int(max_pages, 10_000)
        selected_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._time_window(
            selected_days, start_date, end_date
        )
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
        requested = (
            {part.strip() for part in value.split(",") if part.strip()}
            if value else set(config.DEFAULT_CATEGORIES)
        )
        invalid = requested - set(config.CATEGORIES)
        if invalid:
            raise ValueError(f"不支持的公告栏目：{sorted(invalid)}")
        return tuple(
            category for category in config.DEFAULT_CATEGORIES
            if category in requested
        )

    @staticmethod
    def _boundary(value: str | None, end: bool) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = coerce_datetime(raw)
        if not parsed:
            raise ValueError(f"无法解析日期：{raw}")
        if end and len(raw) == 10:
            return datetime.combine(parsed.date(), time.max)
        return parsed

    @classmethod
    def _time_window(cls, days, start_date, end_date):
        start = cls._boundary(start_date, False)
        end = cls._boundary(end_date, True)
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
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Referer": referer or config.list_url("plan"),
        }

    def start_requests(self):
        for category in self.categories:
            yield self._list_request(category, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _list_request(self, category: str, page: int) -> Request:
        url = config.list_url(category)
        if page <= 1:
            return Request(
                url,
                headers=self._headers(),
                callback=self.parse_list,
                cb_kwargs={"category": category, "page": page},
                dont_filter=True,
            )
        return FormRequest(
            url,
            formdata=config.list_form(page, self.page_size),
            headers=self._headers(url),
            callback=self.parse_list,
            cb_kwargs={"category": category, "page": page},
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

    def parse_list(self, response: Response, category: str, page: int):
        records = parse_list_records(response.body)
        reached_before_window = False
        notice_type = config.CATEGORIES[category]["label"]
        for parsed in records:
            record = {
                "id": parsed.notice_id,
                "path_type": parsed.path_type,
                "title": parsed.title,
                "publish_time": parsed.publish_time,
                "detail_url": parsed.detail_url,
                "region": parsed.region,
                "project_type": parsed.project_type,
                "source_category": category,
                "source_page": page,
            }
            published = self._published(record)
            if self.window_start and published and published < self.window_start:
                reached_before_window = True
            if not self._inside_window(record):
                continue
            if (
                not parsed.notice_id
                or parsed.notice_id in self._seen
                or self._counts[category] >= self.max_records
            ):
                continue
            self._seen.add(parsed.notice_id)
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
            self._counts[category] += 1
            yield Request(
                parsed.detail_url,
                headers=self._headers(response.url),
                callback=self.parse_detail,
                cb_kwargs={
                    "category": category,
                    "list_record": record,
                    "list_fingerprint": fingerprint,
                },
            )

        total_pages, _ = parse_page_info(response.body)
        if (
            records
            and not reached_before_window
            and page < min(total_pages or page, self.max_pages)
            and self._counts[category] < self.max_records
        ):
            yield self._list_request(category, page + 1)

    def parse_detail(
        self,
        response: Response,
        category: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
    ):
        page = parse_detail_page(response.body, list_record=list_record)
        detail_metadata = self.build_response_metadata(
            response,
            request_kind="detail_html",
            context={"category": category, "noticeId": list_record.get("id", "")},
        )
        if page.body_pdf_url:
            yield Request(
                page.body_pdf_url,
                headers=self._headers(response.url),
                callback=self.parse_body_pdf,
                errback=self.parse_body_pdf_error,
                cb_kwargs={
                    "category": category,
                    "list_record": dict(list_record),
                    "list_fingerprint": list_fingerprint,
                    "page": page,
                    "detail_body": bytes(response.body),
                    "detail_metadata": detail_metadata,
                },
            )
            return
        yield self._build_item(
            category=category,
            list_record=list_record,
            list_fingerprint=list_fingerprint,
            page=page,
            detail_body=response.body,
            detail_metadata=detail_metadata,
            pdf_text="",
            pdf_metadata=None,
        )

    def parse_body_pdf(
        self,
        response: Response,
        category: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
        page: ParsedPage,
        detail_body: bytes,
        detail_metadata: Mapping[str, Any],
    ):
        # 定标候选人历史公告多为超宽表格，-layout 会打乱跨页列顺序；
        # 其他六类正文仍使用 -layout，以保留段落和普通表格结构。
        pdf_text_mode = "raw" if category == "final_candidate" else "layout"
        pdf_text = extract_pdf_text(response.body, mode=pdf_text_mode)
        yield self._build_item(
            category=category,
            list_record=list_record,
            list_fingerprint=list_fingerprint,
            page=page,
            detail_body=detail_body,
            detail_metadata=detail_metadata,
            pdf_text=pdf_text,
            pdf_metadata=self.build_response_metadata(
                response,
                request_kind="notice_body_pdf",
                context={
                    "category": category,
                    "noticeId": list_record.get("id", ""),
                    "textExtractionMode": pdf_text_mode,
                },
            ),
        )

    def parse_body_pdf_error(self, failure):
        values = failure.request.cb_kwargs
        self.crawler.stats.inc_value("sxbid/body_pdf_failed")
        yield self._build_item(
            category=values["category"],
            list_record=values["list_record"],
            list_fingerprint=values["list_fingerprint"],
            page=values["page"],
            detail_body=values["detail_body"],
            detail_metadata=values["detail_metadata"],
            pdf_text="",
            pdf_metadata={"error": str(failure.value)},
        )

    def _build_item(
        self,
        *,
        category: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
        page: ParsedPage,
        detail_body: bytes,
        detail_metadata: Mapping[str, Any],
        pdf_text: str,
        pdf_metadata: Mapping[str, Any] | None,
    ):
        parsed = SxbidParser.parse(
            category,
            page,
            list_record=list_record,
            pdf_text=pdf_text,
        )
        notice_id = str(list_record.get("id") or "")
        return self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_subtype=category,
            notice_id=notice_id,
            title=parsed.title,
            publish_time=parsed.publish_time,
            detail_url=str(list_record.get("detail_url") or ""),
            data=parsed.data,
            raw_data={
                "list": dict(list_record),
                "detailHeaders": page.headers,
                "sourceName": page.source_name,
                "projectChainId": page.project_chain_id,
                "bodyPdfUrl": page.body_pdf_url,
            },
            raw_html=detail_body,
            raw_text=parsed.raw_text,
            parse_status="PARTIAL" if parsed.validation_warnings else "PARSED",
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            field_meta={
                "site_parser": self.extraction_model_name,
                "source_category": category,
                "source_name": page.source_name,
                "project_type": list_record.get("project_type", ""),
                "region": list_record.get("region", ""),
                "body_format": "pdf" if page.body_pdf_url else "html",
                "validation_warnings": parsed.validation_warnings,
            },
            response_metadata={
                "requestKind": "detail_html_and_body",
                "detailPage": dict(detail_metadata),
                "bodyDocument": dict(pdf_metadata or {}),
            },
            attachments=parsed.attachments,
        )
