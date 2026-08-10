"""山西交控招投标采购服务平台前两栏公开公告爬虫。"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from typing import Any, Mapping
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.sxjkzcpt import config
from crawler_scrapy.sites.sxjkzcpt.parser import (
    SxjkzcptParser,
    classify_category,
    extract_csrf,
    parse_list_records,
    parse_total_pages,
)
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class SxjkzcptSpider(BaseNoticeSpider):
    name = "sxjkzcpt"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.sxjkzcpt.com.cn"]
    parser_version = SxjkzcptParser.parser_version
    extraction_model_name = "sxjkzcpt-public-html-rule-parser"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "COOKIES_ENABLED": True,
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
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines[
            "crawler_scrapy.sites.sxjkzcpt.exporter.SxjkzcptMultiFormatPipeline"
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
        channels: str | None = None,
        max_records: int | str = 200,
        page_size: int | str = 100,
        max_pages: int | str = 100,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        sample_mode: str = "latest",
        sample_seed: int | str = 20260806,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.feeds = self._select_feeds(categories, channels)
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, 100), 100)
        self.max_pages = self._positive_int(max_pages, 100)
        selected_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._time_window(
            selected_days, start_date, end_date
        )
        self.sample_mode = str(sample_mode or "latest").strip().lower()
        if self.sample_mode not in {"latest", "random"}:
            raise ValueError("sample_mode 仅支持 latest/random")
        try:
            self.sample_seed = int(sample_seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("sample_seed 必须是整数") from exc
        self._counts = {feed: 0 for feed in self.feeds}
        self._seen: set[str] = set()
        self._random_discovered: set[str] = set()
        self._csrf = ""

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _select_feeds(
        categories: str | None, channels: str | None
    ) -> tuple[str, ...]:
        selected_categories = (
            {x.strip() for x in categories.split(",") if x.strip()}
            if categories
            else set(config.DEFAULT_CATEGORIES)
        )
        selected_channels = (
            {x.strip() for x in channels.split(",") if x.strip()}
            if channels
            else set(config.DEFAULT_CHANNELS)
        )
        invalid = (selected_categories - set(config.CATEGORIES)) | (
            selected_channels - set(config.CHANNELS)
        )
        if invalid:
            raise ValueError(f"不支持的栏目/频道：{sorted(invalid)}")
        return tuple(
            feed
            for feed in config.FEEDS
            if feed.split(".", 1)[0] in selected_channels
            and feed.split(".", 1)[1] in selected_categories
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
    def _headers(accept: str = "text/html, */*;q=0.8") -> dict[str, str]:
        return {
            "Accept": accept,
            "Referer": config.BOOTSTRAP_URL,
            "X-Requested-With": "XMLHttpRequest",
        }

    def start_requests(self):
        yield Request(
            config.BOOTSTRAP_URL,
            headers={"Accept": "text/html,application/xhtml+xml"},
            callback=self.parse_bootstrap,
            dont_filter=True,
        )

    async def start(self):
        for request in self.start_requests():
            yield request

    def parse_bootstrap(self, response: Response):
        self._csrf = extract_csrf(response.body)
        if not self._csrf:
            raise RuntimeError("山西交控入口页未返回 CSRF，停止请求以避免无效重试")
        for feed in self.feeds:
            yield self._list_request(feed, 1)

    def _post_request(
        self, url: str, formdata: Mapping[str, Any], **kwargs: Any
    ) -> Request:
        headers = self._headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        return Request(
            url,
            method="POST",
            body=urlencode({key: str(value) for key, value in formdata.items()}).encode(),
            headers=headers,
            **kwargs,
        )

    def _list_request(
        self, feed: str, page: int, *, sample_quota: int | None = None
    ) -> Request:
        menu_code, type_code, _, _ = config.FEEDS[feed]
        return self._post_request(
            config.LIST_URL,
            {
                "menuCode": menu_code,
                "typeCode": type_code,
                "page": str(page),
                "pageSize": str(self.page_size),
                "keyName": "",
                "_csrf": self._csrf,
            },
            callback=self.parse_list,
            cb_kwargs={
                "feed": feed,
                "page": page,
                "sample_quota": sample_quota,
            },
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

    def _record_requests(
        self,
        records,
        *,
        feed: str,
        page: int,
        quota: int | None = None,
        randomized: bool = False,
    ):
        candidates = [record for record in records if self._inside_window(record.publish_time)]
        if randomized:
            rng = random.Random(f"{self.sample_seed}:{feed}:{page}")
            rng.shuffle(candidates)
        emitted = 0
        for record in candidates:
            if self._counts[feed] >= self.max_records:
                break
            if quota is not None and emitted >= quota:
                break
            if not record.notice_id or record.notice_id in self._seen:
                continue
            self._seen.add(record.notice_id)
            list_record = {
                "notice_id": record.notice_id,
                "title": record.title,
                "publish_time": record.publish_time,
                "source_feed": feed,
                "sample_mode": self.sample_mode,
                "sample_seed": self.sample_seed if randomized else None,
                "sample_page": page,
            }
            actual, notice_type = classify_category(feed.split(".", 1)[1], record.title)
            detail_url = config.detail_page_url(record.notice_id)
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=record.notice_id,
                list_record=list_record,
                detail_url=detail_url,
                notice_type=notice_type,
                title=record.title,
                publish_time=record.publish_time,
            )
            if not should_fetch:
                continue
            self._counts[feed] += 1
            emitted += 1
            yield self._post_request(
                config.DETAIL_POST_URL,
                {"info": record.notice_id, "_csrf": self._csrf},
                callback=self.parse_detail,
                cb_kwargs={
                    "feed": feed,
                    "list_record": list_record,
                    "list_fingerprint": fingerprint,
                    "expected_category": actual,
                },
                dont_filter=True,
            )

    def _random_page_plan(self, feed: str, total_pages: int) -> dict[int, int]:
        """从全部历史页中均匀抽页，尽量让 5 条来自不同页。"""

        if total_pages <= 0:
            return {}
        page_count = min(total_pages, self.max_records)
        rng = random.Random(f"{self.sample_seed}:{feed}:pages")
        selected = sorted(rng.sample(range(1, total_pages + 1), page_count))
        base, remainder = divmod(self.max_records, page_count)
        return {
            selected[index]: base + (1 if index < remainder else 0)
            for index in range(page_count)
        }

    def parse_list(
        self,
        response: Response,
        feed: str,
        page: int,
        sample_quota: int | None = None,
    ):
        records = parse_list_records(response.body)
        total_pages = parse_total_pages(response.body)
        if self.sample_mode == "random" and feed not in self._random_discovered:
            self._random_discovered.add(feed)
            page_plan = self._random_page_plan(feed, total_pages or (1 if records else 0))
            self.logger.info(
                "随机抽样页：feed=%s seed=%s total_pages=%s plan=%s",
                feed,
                self.sample_seed,
                total_pages,
                page_plan,
            )
            for selected_page, quota in page_plan.items():
                if selected_page == page:
                    yield from self._record_requests(
                        records,
                        feed=feed,
                        page=page,
                        quota=quota,
                        randomized=True,
                    )
                else:
                    yield self._list_request(
                        feed, selected_page, sample_quota=quota
                    )
            return
        if self.sample_mode == "random":
            yield from self._record_requests(
                records,
                feed=feed,
                page=page,
                quota=sample_quota,
                randomized=True,
            )
            return

        reached_before_window = False
        for record in records:
            published = self._published(record.publish_time)
            if self.window_start and published and published < self.window_start:
                reached_before_window = True
            if not self._inside_window(record.publish_time):
                continue
        # 同一来源栏目最多请求 max_records 条；源站混栏在详情解析后再纠正。
        yield from self._record_requests(records, feed=feed, page=page)
        if (
            records
            and not reached_before_window
            and page < total_pages
            and page < self.max_pages
            and self._counts[feed] < self.max_records
        ):
            yield self._list_request(feed, page + 1)

    def parse_detail(
        self,
        response: Response,
        feed: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
        expected_category: str,
    ):
        parsed = SxjkzcptParser.parse(feed, response.body, list_record=list_record)
        notice_id = str(list_record.get("notice_id") or "")
        if parsed.access.get("requiresCa") or parsed.access.get("requiresLogin"):
            self.crawler.stats.inc_value("sxjkzcpt/access_restricted_skipped")
            self.logger.warning(
                "详情需要登录/CA，按公开采集边界跳过：id=%s title=%s",
                notice_id,
                parsed.title,
            )
            return
        if not parsed.raw_text:
            self.crawler.stats.inc_value("sxjkzcpt/empty_detail_skipped")
            self.logger.warning("详情正文为空，未导出：id=%s", notice_id)
            return
        channel = feed.split(".", 1)[0]
        subtype = f"{channel}.{parsed.category}"
        if parsed.category != expected_category:
            self.crawler.stats.inc_value("sxjkzcpt/list_detail_category_changed")
        response_meta = self.build_response_metadata(
            response,
            request_kind="detail_post",
            context={"feed": feed, "noticeId": notice_id},
        )
        # POST 详情接口地址进入 response trace；用户可打开的 GET 包装页作为 canonical URL。
        yield self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_subtype=subtype,
            notice_id=notice_id,
            title=parsed.title,
            publish_time=parsed.publish_time,
            detail_url=config.detail_page_url(notice_id),
            data=parsed.data,
            raw_data={
                "list": dict(list_record),
                "detailStructured": parsed.structured,
                "access": parsed.access,
                "sourceFeed": feed,
            },
            raw_html=response.body,
            raw_text=parsed.raw_text,
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            field_meta={
                "site_parser": self.extraction_model_name,
                "source_feed": feed,
                "source_channel": channel,
                "source_category": feed.split(".", 1)[1],
                "actual_category": parsed.category,
                "access": parsed.access,
            },
            response_metadata=response_meta,
            attachments=parsed.attachments,
        )
