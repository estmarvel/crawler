"""国信 e 采（山西）公开公告爬虫。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timedelta
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.gxebidding import config
from crawler_scrapy.sites.gxebidding.parser import (
    DetailDocument,
    GxebiddingParser,
    extract_pdf_text,
    parse_detail_document,
    parse_list_records,
    parse_page_info,
)
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class GxebiddingSpider(BaseNoticeSpider):
    name = "gxebidding"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["gx.e-bidding.org"]
    parser_version = GxebiddingParser.parser_version
    extraction_model_name = "gxebidding-public-html-pdf-rule-parser"

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
        channels: str | None = None,
        max_records: int | str = 1_000_000,
        max_pages: int | str = 10_000,
        page_size: int | str = 15,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        keyword: str = "",
        parse_pdf: str | bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.categories = self._select(
            categories, config.CATEGORIES, config.DEFAULT_CATEGORIES, "公告类别"
        )
        self.channels = self._select(
            channels, config.CHANNELS, config.DEFAULT_CHANNELS, "业务频道"
        )
        self.max_records = self._positive_int(max_records, 1_000_000)
        self.max_pages = self._positive_int(max_pages, 10_000)
        # 源站列表固定每页约15条；保留参数仅与统一运行器兼容。
        self.page_size = self._positive_int(page_size, 15)
        selected_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._time_window(
            selected_days, start_date, end_date
        )
        self.keyword = str(keyword or "").strip()
        self.parse_pdf = str(parse_pdf).strip().lower() not in {
            "0", "false", "no", "off"
        }
        self._counts = {
            (channel, category): 0
            for channel in self.channels
            for category in self.categories
        }

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _select(value, definitions, defaults, label) -> tuple[str, ...]:
        selected = tuple(
            part.strip() for part in str(value or "").split(",") if part.strip()
        ) or tuple(defaults)
        invalid = [part for part in selected if part not in definitions]
        if invalid:
            raise ValueError(f"不支持的国信e采{label}：{','.join(invalid)}")
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
    def _headers(*, pdf: bool = False, referer: str = "") -> dict[str, str]:
        return {
            "Accept": "application/pdf,*/*" if pdf else "text/html,application/xhtml+xml,*/*",
            "Referer": referer or config.WEB_BASE_URL + "/",
        }

    def start_requests(self):
        for channel in self.channels:
            for category in self.categories:
                yield self._list_request(channel, category, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _list_request(self, channel: str, category: str, page: int) -> Request:
        url = config.list_url(channel, category, page, keyword=self.keyword)
        return Request(
            url,
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"channel": channel, "category": category, "page": page},
            dont_filter=True,
        )

    @staticmethod
    def _record_time(record: Mapping[str, Any]) -> datetime | None:
        return coerce_datetime(record.get("publish_time"))

    def _inside_window(self, record: Mapping[str, Any]) -> bool:
        published = self._record_time(record)
        if not published:
            return True
        return (not self.window_start or published >= self.window_start) and (
            not self.window_end or published <= self.window_end
        )

    def parse_list(self, response: Response, channel: str, category: str, page: int):
        records = parse_list_records(response.body)
        total_pages, _ = parse_page_info(response.body)
        key = (channel, category)
        reached_before_window = False
        for parsed_record in records:
            record = asdict(parsed_record)
            published = self._record_time(record)
            if self.window_start and published and published < self.window_start:
                reached_before_window = True
            if not self._inside_window(record):
                continue
            if self._counts[key] >= self.max_records:
                break
            expected_family = str(config.CATEGORIES[category]["path_family"])
            if parsed_record.path_family != expected_family:
                self.crawler.stats.inc_value("gxebidding/list_path_mismatch")
                self.logger.warning(
                    "国信e采列表类型不匹配：channel=%s category=%s id=%s path=%s",
                    channel, category, parsed_record.cms_id, parsed_record.path_family,
                )
                continue
            notice_id = config.source_notice_id(
                parsed_record.path_family, parsed_record.cms_id
            )
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=notice_id,
                notice_type=str(config.CATEGORIES[category]["schema"]),
                list_record=record,
                detail_url=parsed_record.detail_url,
                title=parsed_record.title,
                publish_time=published,
            )
            if not should_fetch:
                continue
            self._counts[key] += 1
            yield Request(
                parsed_record.detail_url,
                headers=self._headers(referer=response.url),
                callback=self.parse_detail,
                cb_kwargs={
                    "channel": channel,
                    "category": category,
                    "list_record": record,
                    "list_fingerprint": fingerprint,
                },
            )

        if (
            records
            and not reached_before_window
            and self._counts[key] < self.max_records
            and page < self.max_pages
            and (not total_pages or page < total_pages)
        ):
            yield self._list_request(channel, category, page + 1)

    def parse_detail(
        self,
        response: Response,
        channel: str,
        category: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
    ):
        detail = parse_detail_document(response.body)
        context = {
            "channel": channel,
            "category": category,
            "list_record": dict(list_record),
            "list_fingerprint": list_fingerprint,
            "detail": detail,
            "detail_html": response.body,
            "detail_metadata": self.build_response_metadata(
                response,
                request_kind="detail_html_pdf_shell",
                context={
                    "channel": channel,
                    "category": category,
                    "cmsId": list_record.get("cms_id"),
                    "pdfFileId": detail.file_id,
                    "pdfFileType": detail.file_type,
                },
            ),
        }
        if self.parse_pdf and detail.pdf_url:
            yield Request(
                detail.pdf_url,
                headers=self._headers(pdf=True, referer=response.url),
                callback=self.parse_pdf_detail,
                errback=self.pdf_failed,
                cb_kwargs={"context": context},
                dont_filter=True,
            )
            return
        yield self._build_item(context)

    def parse_pdf_detail(self, response: Response, context: Mapping[str, Any]):
        pdf_text = ""
        table_text = ""
        if response.body.startswith(b"%PDF-"):
            pdf_text = extract_pdf_text(response.body, timeout=180, mode="layout")
            table_text = extract_pdf_text(response.body, timeout=180, mode="raw")
        else:
            self.crawler.stats.inc_value("gxebidding/pdf_invalid_signature")
        if not pdf_text:
            self.crawler.stats.inc_value("gxebidding/pdf_without_text_layer")
        metadata = self.build_response_metadata(
            response,
            request_kind="notice_pdf",
            context={
                "sourceId": context.get("list_record", {}).get("cms_id"),
                "textLength": len(pdf_text),
            },
        )
        yield self._build_item(
            context,
            pdf_text=pdf_text,
            table_text=table_text,
            pdf_metadata=metadata,
        )

    def pdf_failed(self, failure):
        self.crawler.stats.inc_value("gxebidding/pdf_download_failed")
        context = failure.request.cb_kwargs.get("context") or {}
        self.logger.warning("国信e采PDF读取失败，保留附件等待独立下载：%s", failure)
        yield self._build_item(
            context, pdf_metadata={"error": str(failure.value)}
        )

    def _build_item(
        self,
        context: Mapping[str, Any],
        *,
        pdf_text: str = "",
        table_text: str = "",
        pdf_metadata: Mapping[str, Any] | None = None,
    ):
        channel = str(context["channel"])
        category = str(context["category"])
        list_record = dict(context["list_record"])
        detail = context["detail"]
        if not isinstance(detail, DetailDocument):
            detail = DetailDocument(**dict(detail))
        parsed = GxebiddingParser.parse(
            channel,
            category,
            list_record,
            detail,
            pdf_text=pdf_text,
            table_text=table_text,
        )
        notice_id = config.source_notice_id(
            str(list_record["path_family"]), str(list_record["cms_id"])
        )
        complete = bool(
            parsed.title and parsed.data.get("项目名称") and parsed.raw_text
        )
        return self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_subtype=f"{channel}.{category}",
            notice_id=notice_id,
            title=parsed.title,
            publish_time=parsed.publish_time,
            detail_url=str(list_record["detail_url"]),
            data=parsed.data,
            raw_data={
                "sourceChannel": channel,
                "sourceCategory": category,
                "list": list_record,
                "detail": asdict(detail),
            },
            raw_html=context.get("detail_html"),
            raw_text=parsed.raw_text,
            parse_status="PARSED" if complete else "PARTIAL",
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=str(context.get("list_fingerprint") or ""),
            field_meta={
                "site_parser": self.extraction_model_name,
                "source_channel": channel,
                "source_channel_label": config.CHANNELS[channel]["label"],
                "source_category": category,
                "source_category_label": config.source_label(channel, category),
                "source_file_type": detail.file_type,
                "body_format": "pdf" if parsed.raw_text else "pdf_pending",
                "validation_warnings": parsed.validation_warnings,
            },
            response_metadata={
                "requestKind": "detail_html_and_optional_pdf",
                "detailHtml": dict(context.get("detail_metadata") or {}),
                "bodyPdf": dict(pdf_metadata or {}),
            },
            attachments=parsed.attachments,
        )
