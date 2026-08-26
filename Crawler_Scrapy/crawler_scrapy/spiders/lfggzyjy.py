"""临汾市公共资源交易平台公开工程建设公告 Spider。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.ai.glm52_profile import (
    QWEN3_HYBRID_SETTINGS,
    install_hybrid_pipeline,
)
from crawler_scrapy.items import NoticeItem
from crawler_scrapy.schemas.notice_fields import ANNOUNCEMENT_SCHEMAS, coerce_datetime
from crawler_scrapy.sites.lfggzyjy import config
from crawler_scrapy.sites.lfggzyjy.parser import (
    LfggzyjyParser,
    normalize_list_record,
    parse_list_response,
)
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class LfggzyjySpider(BaseNoticeSpider):
    name = "lfggzyjy"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["lfggzyjy.linfen.gov.cn"]
    parser_version = LfggzyjyParser.parser_version
    extraction_model_name = "lfggzyjy-public-html-rule-parser"
    ai_metadata_key = "lfggzyjyHybridAi"
    ai_trusted_fields_meta_key = "lfggzyjyTrustedFields"
    ai_log_name = "临汾公共资源交易平台"

    # 临汾站优先信任列表 API、固定 DOM 和明确标签正则。这里不配置
    # “每条必走 AI”的字段；混合管线只会在候选字段缺失但正文有标签、
    # 长段落串边界或候选人/报价不一致等异常场景下升级到 AI。
    ai_extract_fields = {
        "招标计划": (),
        "招标公告": (),
        "中标候选人公示": (),
        "中标结果公示": (),
        "更正结果公示": (),
    }
    ai_sparse_review_fields = {}
    ai_candidate_fields = {
        notice_type: tuple(
            field
            for field in ANNOUNCEMENT_SCHEMAS[notice_type]
            if field not in {
                "项目名称",
                "项目编号",
                "招标编号",
                "项目编号/招标编号",
                "招标编号/项目编号",
                "发布日期",
                "发布网站",
                "公告内容",
            }
        )
        for notice_type in ai_extract_fields
    }

    custom_settings = {
        **QWEN3_HYBRID_SETTINGS,
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 2.0,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.75,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": False,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": False,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        install_hybrid_pipeline(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines[
            "crawler_scrapy.sites.lfggzyjy.exporter.LfggzyjyMultiFormatPipeline"
        ] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

        mode = str(settings.get("CRAWLER_OUTBOUND_MODE", "direct")).strip().lower()
        if mode not in {"direct", "static"}:
            raise ValueError(
                f"不支持的 CRAWLER_OUTBOUND_MODE={mode!r}；可选 direct/static"
            )
        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        middlewares[
            "crawler_scrapy.transport.proxy_middleware.StaticProxyMiddleware"
        ] = 610 if mode == "static" else None
        middlewares[
            "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
        ] = 650
        settings.set("DOWNLOADER_MIDDLEWARES", middlewares, priority="spider")
        settings.set("STATIC_PROXY_ENABLED", mode == "static", priority="spider")
        settings.set("HTTPPROXY_ENABLED", mode == "static", priority="spider")

    def __init__(
        self,
        tables: str | None = None,
        max_records: int | str = 200,
        page_size: int | str = config.PAGE_SIZE,
        max_pages: int | str = 100,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tables = self._select_tables(tables)
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, config.PAGE_SIZE), 100)
        self.max_pages = self._positive_int(max_pages, 100)
        selected_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._time_window(
            selected_days, start_date, end_date
        )
        self._seen: set[str] = set()
        self._scheduled = 0

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _select_tables(value: str | None) -> tuple[str, ...]:
        requested = (
            {part.strip() for part in value.split(",") if part.strip()}
            if value else set(config.DEFAULT_TABLES)
        )
        invalid = requested - set(config.TABLE_NOTICE_TYPES)
        if invalid:
            raise ValueError(f"不支持的临汾公告表：{sorted(invalid)}")
        return tuple(table for table in config.DEFAULT_TABLES if table in requested)

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
    def _headers(accept: str = "application/json,text/html,*/*") -> dict[str, str]:
        return {
            "Accept": accept,
            "Referer": f"{config.WEB_BASE_URL}/moreInfoController.do?getMoreNotice",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36",
        }

    def start_requests(self):
        yield Request(
            config.list_url(1, self.page_size),
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"page": 1},
            dont_filter=True,
        )

    async def start(self):
        for request in self.start_requests():
            yield request

    def parse_list(self, response: Response, page: int):
        rows, total = parse_list_response(response.body)
        allowed = set(self.tables)
        reached_before_window = False
        for row in rows:
            list_record = normalize_list_record(row)
            if list_record.table_name not in allowed:
                self.crawler.stats.inc_value(
                    f"lfggzyjy/skipped_table/{list_record.table_name or 'unknown'}"
                )
                continue
            published = coerce_datetime(list_record.publish_time)
            if self.window_end and published and published > self.window_end:
                continue
            if self.window_start and published and published < self.window_start:
                reached_before_window = True
                continue
            key = f"{list_record.table_name}:{list_record.notice_id}"
            if key in self._seen or self._scheduled >= self.max_records:
                continue
            self._seen.add(key)
            notice_type = config.TABLE_NOTICE_TYPES[list_record.table_name][1]
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=list_record.notice_id,
                list_record=list_record.raw,
                detail_url=list_record.detail_url,
                notice_type=notice_type,
                title=list_record.title,
                publish_time=published,
            )
            if not should_fetch:
                continue
            self._scheduled += 1
            yield Request(
                list_record.detail_url,
                headers=self._headers("text/html,application/xhtml+xml"),
                callback=self.parse_detail,
                cb_kwargs={
                    "list_record": list_record.raw,
                    "list_fingerprint": fingerprint,
                },
            )
        if (
            rows
            and not reached_before_window
            and page < self.max_pages
            and self._scheduled < self.max_records
            and page * self.page_size < max(total, page * self.page_size + 1)
        ):
            yield Request(
                config.list_url(page + 1, self.page_size),
                headers=self._headers(),
                callback=self.parse_list,
                cb_kwargs={"page": page + 1},
                dont_filter=True,
            )

    def parse_detail(
        self,
        response: Response,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
    ):
        parsed = LfggzyjyParser.parse(list_record, response.body)
        record = normalize_list_record(list_record)
        yield self.build_notice_item(
            notice_id=record.notice_id,
            notice_type=parsed.notice_type,
            notice_subtype=parsed.notice_subtype,
            title=parsed.title,
            publish_time=parsed.publish_time or record.publish_time,
            detail_url=record.detail_url,
            data=parsed.data,
            raw_data={"list": dict(list_record)},
            raw_html=response.body,
            raw_text=parsed.raw_text,
            parse_status="PARTIAL" if parsed.validation_warnings else "PARSED",
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            field_meta={
                "site_parser": self.parser_version,
                "source_table": record.table_name,
                "source_project_code": record.project_code,
                "source_region_code": record.region_code,
                "validation_warnings": parsed.validation_warnings,
                self.ai_trusted_fields_meta_key: self._trusted_fields(record, parsed),
            },
            response_metadata=self.build_response_metadata(
                response,
                request_kind="detail_html",
                context={
                    "tableName": record.table_name,
                    "noticeId": record.notice_id,
                },
            ),
        )
    @staticmethod
    def _trusted_fields(record, parsed) -> list[str]:
        trusted = ["项目名称", "发布日期", "发布网站"]
        if record.project_code:
            trusted.extend(["项目编号", "项目编号/招标编号", "招标编号/项目编号"])
        if parsed.notice_type == "招标计划":
            trusted.extend(["招标编号", "招标方式"] if parsed.data.get("招标方式") else [])
        return list(dict.fromkeys(field for field in trusted if field in parsed.data))

