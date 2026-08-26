"""润世和电子招投标交易平台公开公告爬虫。"""

from __future__ import annotations

import hashlib
from datetime import datetime, time, timedelta
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import JsonRequest, Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.runshihua import config
from crawler_scrapy.sites.runshihua.parser import RunshihuaParser, extract_pdf_text
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class RunshihuaSpider(BaseNoticeSpider):
    name = "runshihua"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["ec.runshihua.com", "file.runshihua.com"]
    parser_version = RunshihuaParser.parser_version
    extraction_model_name = "runshihua-public-api-html-pdf-rule-parser"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
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
        max_records: int | str = 200,
        page_size: int | str = 100,
        max_pages: int | str = 10_000,
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
        self.categories = self._select_categories(categories)
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, 100), 400)
        self.max_pages = self._positive_int(max_pages, 10_000)
        selected_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._time_window(
            selected_days, start_date, end_date
        )
        self.keyword = str(keyword or "").strip()
        self.parse_pdf = str(parse_pdf).strip().lower() not in {
            "0", "false", "no", "off"
        }
        self._counts = {category: 0 for category in self.categories}
        self._seen: set[tuple[str, str]] = set()
        self.families = tuple(
            family for family in config.FAMILIES
            if any(
                config.CATEGORIES[category]["family"] == family
                for category in self.categories
            )
        )

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _select_categories(value: str | None) -> tuple[str, ...]:
        if not value:
            return config.DEFAULT_CATEGORIES
        requested = tuple(part.strip() for part in value.split(",") if part.strip())
        invalid = [category for category in requested if category not in config.CATEGORIES]
        if invalid:
            raise ValueError(f"不支持的润世和公告类别：{','.join(invalid)}")
        return requested or config.DEFAULT_CATEGORIES

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
    def _headers(*, pdf: bool = False) -> dict[str, str]:
        return {
            "Accept": "application/pdf,*/*" if pdf else "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": config.WEB_BASE_URL,
            "Referer": config.WEB_HOME_URL,
        }

    def start_requests(self):
        for family in self.families:
            yield self._list_request(family, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _list_request(self, family: str, page: int) -> JsonRequest:
        return JsonRequest(
            config.endpoint(family, "list"),
            data=config.list_payload(family, page, self.page_size, self.keyword),
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"family": family, "page": page},
            dont_filter=True,
        )

    @staticmethod
    def _record_time(family: str, record: Mapping[str, Any]) -> datetime | None:
        keys = {
            "notice": ("returnDate", "releaseDate", "createDate"),
            "candidate": ("startDate", "createDate", "endDate"),
            "other": ("createDate", "noticeDate", "releaseDate"),
        }[family]
        for key in keys:
            parsed = coerce_datetime(record.get(key))
            if parsed:
                return parsed
        return None

    def _inside_window(self, family: str, record: Mapping[str, Any]) -> bool:
        published = self._record_time(family, record)
        if not published:
            return True
        return (not self.window_start or published >= self.window_start) and (
            not self.window_end or published <= self.window_end
        )

    def _family_complete(self, family: str) -> bool:
        selected = [
            category for category in self.categories
            if config.CATEGORIES[category]["family"] == family
        ]
        return bool(selected) and all(
            self._counts[category] >= self.max_records for category in selected
        )

    def parse_list(self, response: Response, family: str, page: int):
        payload = response.json()
        envelope = payload.get("data") if isinstance(payload, Mapping) else None
        records = envelope.get("list") if isinstance(envelope, Mapping) else None
        if payload.get("code") != "RESP200" or not isinstance(records, list):
            self.logger.warning("润世和列表返回异常：family=%s page=%s", family, page)
            self.crawler.stats.inc_value("runshihua/list_invalid")
            return

        reached_before_window = False
        for raw in records:
            if not isinstance(raw, Mapping):
                continue
            published = self._record_time(family, raw)
            if self.window_start and published and published < self.window_start:
                reached_before_window = True
            if not self._inside_window(family, raw):
                continue
            category = config.category_for_record(family, raw)
            if not category:
                self.crawler.stats.inc_value("runshihua/unknown_source_type")
                self.logger.warning(
                    "未知润世和公告类型：family=%s id=%s noticeType=%s candidateType=%s",
                    family, raw.get("id"), raw.get("noticeType"), raw.get("candidateType"),
                )
                continue
            if category not in self.categories or self._counts[category] >= self.max_records:
                continue
            notice_id = str(raw.get("id") or "").strip()
            identity = (family, notice_id)
            if not notice_id or identity in self._seen:
                continue
            self._seen.add(identity)
            source_notice_id = config.source_notice_id(family, notice_id)
            detail_url = config.detail_page_url(family, raw)
            title = str(raw.get("noticeName") or raw.get("sectionName") or "").strip()
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=source_notice_id,
                notice_type=str(config.CATEGORIES[category]["schema"]),
                list_record=raw,
                detail_url=detail_url,
                title=title,
                publish_time=published,
            )
            if not should_fetch:
                continue
            self._counts[category] += 1
            yield JsonRequest(
                config.endpoint(family, "detail"),
                data=config.detail_payload(family, raw),
                headers=self._headers(),
                callback=self.parse_detail,
                cb_kwargs={
                    "family": family,
                    "category": category,
                    "list_record": dict(raw),
                    "list_fingerprint": fingerprint,
                },
            )

        total = int(envelope.get("total") or 0)
        if (
            records
            and not reached_before_window
            and page * self.page_size < total
            and page < self.max_pages
            and not self._family_complete(family)
        ):
            yield self._list_request(family, page + 1)

    def parse_detail(
        self,
        response: Response,
        family: str,
        category: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
    ):
        payload = response.json()
        detail = payload.get("data") if isinstance(payload, Mapping) else None
        if payload.get("code") != "RESP200" or not isinstance(detail, Mapping):
            self.logger.warning(
                "润世和详情返回异常：family=%s category=%s id=%s",
                family, category, list_record.get("id"),
            )
            self.crawler.stats.inc_value("runshihua/detail_invalid")
            return
        context = {
            "family": family,
            "category": category,
            "list_record": dict(list_record),
            "list_fingerprint": list_fingerprint,
            "detail": dict(detail),
            "detail_metadata": self.build_response_metadata(
                response,
                request_kind="detail_api",
                context={
                    "family": family,
                    "category": category,
                    "sourceId": list_record.get("id"),
                },
            ),
        }
        parsed = RunshihuaParser.parse(category, detail, list_record=list_record)
        if self.parse_pdf and parsed.attachments:
            pdf_url = str(parsed.attachments[0].get("file_url") or "")
            if pdf_url:
                yield Request(
                    pdf_url,
                    headers=self._headers(pdf=True),
                    callback=self.parse_pdf_detail,
                    errback=self.pdf_failed,
                    cb_kwargs={"context": context},
                    dont_filter=True,
                )
                return
        yield self._build_item(context)

    def parse_pdf_detail(self, response: Response, context: Mapping[str, Any]):
        pdf_text = extract_pdf_text(response.body, timeout=90, mode="layout")
        if not pdf_text:
            self.crawler.stats.inc_value("runshihua/pdf_without_text_layer")
        metadata = self.build_response_metadata(
            response,
            request_kind="notice_pdf",
            context={
                "category": context.get("category"),
                "sourceId": context.get("list_record", {}).get("id"),
                "textLength": len(pdf_text),
            },
        )
        yield self._build_item(context, pdf_text=pdf_text, pdf_metadata=metadata)

    def pdf_failed(self, failure):
        self.crawler.stats.inc_value("runshihua/pdf_download_failed")
        self.logger.warning("润世和PDF下载失败，改用接口正文：%s", failure.request.url)
        context = failure.request.cb_kwargs.get("context") or {}
        yield self._build_item(
            context,
            pdf_metadata={"error": str(failure.value)},
        )

    @staticmethod
    def _snapshot_payload(
        family: str,
        category: str,
        list_record: Mapping[str, Any],
        detail: Mapping[str, Any],
    ) -> dict[str, Any]:
        # HTML正文单独保存快照，payload中不再重复一份大段HTML。
        cleaned = dict(detail)
        omitted: dict[str, str] = {}
        for key in (
            "gcjsPublicityContent",
            "noticeContent",
            "publicityContent",
            "alterationContent",
        ):
            value = cleaned.pop(key, None)
            if value not in (None, ""):
                omitted[key] = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        return {
            "sourceFamily": family,
            "sourceCategory": category,
            "list": dict(list_record),
            "detail": cleaned,
            "htmlFields": omitted,
        }

    def _build_item(
        self,
        context: Mapping[str, Any],
        *,
        pdf_text: str = "",
        pdf_metadata: Mapping[str, Any] | None = None,
    ):
        family = str(context["family"])
        category = str(context["category"])
        list_record = context["list_record"]
        detail = context["detail"]
        parsed = RunshihuaParser.parse(
            category,
            detail,
            list_record=list_record,
            pdf_text=pdf_text,
        )
        source_notice_id = config.source_notice_id(family, list_record.get("id", ""))
        complete = bool(parsed.title and parsed.data.get("项目名称") and parsed.raw_text)
        return self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_subtype=category,
            notice_id=source_notice_id,
            title=parsed.title,
            publish_time=parsed.publish_time or self._record_time(family, list_record),
            detail_url=config.detail_page_url(family, list_record),
            data=parsed.data,
            raw_data=self._snapshot_payload(
                family, category, list_record, detail
            ),
            raw_html=parsed.raw_html or None,
            raw_text=parsed.raw_text,
            parse_status="PARSED" if complete else "PARTIAL",
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=str(context.get("list_fingerprint") or ""),
            field_meta={
                "site_parser": self.extraction_model_name,
                "source_family": family,
                "source_category": category,
                "source_notice_type": list_record.get("noticeType"),
                "source_candidate_type": list_record.get("candidateType"),
                "project_type": _project_type_for_meta(list_record),
                "body_format": (
                    "html+pdf" if parsed.raw_html and pdf_text
                    else "html" if parsed.raw_html
                    else "pdf" if pdf_text
                    else "structured_api"
                ),
                "validation_warnings": parsed.validation_warnings,
            },
            response_metadata={
                "requestKind": "detail_api_and_optional_pdf",
                "detailApi": dict(context.get("detail_metadata") or {}),
                "bodyPdf": dict(pdf_metadata or {}),
            },
            attachments=parsed.attachments,
        )


def _project_type_for_meta(record: Mapping[str, Any]) -> str:
    return {"A": "工程", "B": "货物", "C": "服务"}.get(
        str(record.get("remark") or "").strip().upper(), ""
    )
