"""云买卖招标公告、候选人公示、中标公示 Spider。"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Mapping
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.eqbidding import config
from crawler_scrapy.sites.eqbidding.parser import EqbiddingParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class EqbiddingSpider(BaseNoticeSpider):
    name = "eqbidding"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.eqbidding.com", "eqbidding.com"]
    parser_version = "eqbidding-v1-frontend-api-note-html"
    extraction_model_name = "eqbidding-site-rule-parser"

    custom_settings = {
        "ROBOTSTXT_OBEY": False, "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 2.0, "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5, "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines["crawler_scrapy.sites.eqbidding.exporter.EqbiddingMultiFormatPipeline"] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

    def __init__(self, feeds: str | None = None, max_records: int | str = 100000,
                 max_pages: int | str = 10000, days: int | str | None = None,
                 start_date: str | None = None, end_date: str | None = None,
                 *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        selected = tuple(x.strip() for x in str(feeds or "").split(",") if x.strip()) or config.DEFAULT_FEEDS
        invalid = set(selected) - set(config.CATEGORIES)
        if invalid:
            raise ValueError(f"不支持的 feeds: {','.join(sorted(invalid))}")
        self.feeds = selected
        self.max_records = self._positive_int(max_records, 100000)
        self.max_pages = self._positive_int(max_pages, 10000)
        self.window_start, self.window_end = self._window(days, start_date, end_date)
        self._scheduled: dict[str, int] = defaultdict(int)
        self._seen: set[tuple[str, str]] = set()

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _window(days, start_date, end_date):
        def boundary(value, end=False):
            parsed = coerce_datetime(value) if value else None
            return datetime.combine(parsed.date(), time.max) if parsed and end and len(str(value)) == 10 else parsed
        start, finish = boundary(start_date), boundary(end_date, True)
        if start is None and days not in (None, ""):
            start = (finish or datetime.now()) - timedelta(days=int(days))
        if start and finish and start > finish:
            raise ValueError("start_date 不能晚于 end_date")
        return start, finish

    @staticmethod
    def _milliseconds(value: datetime | None, default: int) -> int:
        return int(value.timestamp() * 1000) if value else default

    @staticmethod
    def _headers(referer: str) -> dict[str, str]:
        # Origin/Referer/浏览器 UA 不能省略：源站对精简请求曾返回乱码业务字符串。
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9", "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": config.BASE_URL, "Referer": referer,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        }

    def _list_request(self, feed: str, page: int) -> Request:
        body = urlencode({"p": page, "notice_type": config.CATEGORIES[feed][0],
                          "start_time": self._milliseconds(self.window_start, 0),
                          "end_time": self._milliseconds(self.window_end, 4_102_444_799_999)}).encode("ascii")
        return Request(config.list_url(), method="POST", body=body,
                       headers=self._headers(f"{config.BASE_URL}/page_bidding_information/list.html"),
                       callback=self.parse_list, cb_kwargs={"feed": feed, "page": page}, dont_filter=True)

    def start_requests(self):
        for feed in self.feeds:
            yield self._list_request(feed, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    @staticmethod
    def _result(response: Response) -> Mapping[str, Any]:
        payload = json.loads(response.text)
        if payload.get("code") != 200 or not isinstance(payload.get("result"), Mapping):
            raise ValueError(f"云买卖接口响应异常: {payload.get('code')} {payload.get('message')}")
        return payload["result"]

    @staticmethod
    def _publish_time(record: Mapping[str, Any]):
        return coerce_datetime(record.get("notice_release_time") or record.get("created"))

    def parse_list(self, response: Response, feed: str, page: int):
        result = self._result(response)
        package = result.get("pagePackage") or {}
        records = package.get("data") or []
        for raw in records:
            if self._scheduled[feed] >= self.max_records:
                break
            kid = str(raw.get("kid") or "").strip()
            if not kid or (feed, kid) in self._seen:
                continue
            published = self._publish_time(raw)
            if self.window_start and published and published < self.window_start:
                continue
            if self.window_end and published and published > self.window_end:
                continue
            self._seen.add((feed, kid))
            detail_url = config.detail_page_url(kid, feed)
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=kid, notice_type=config.CATEGORIES[feed][1], list_record=raw,
                detail_url=detail_url, title=str(raw.get("notice_title") or raw.get("project_name") or ""),
                publish_time=published,
            )
            if not should_fetch:
                continue
            self._scheduled[feed] += 1
            yield Request(config.detail_api_url(kid), method="POST", body=b"",
                          headers=self._headers(detail_url), callback=self.parse_detail,
                          cb_kwargs={"feed": feed, "list_record": dict(raw),
                                     "list_fingerprint": fingerprint, "detail_url": detail_url}, dont_filter=True)
        page_info = package.get("page") or {}
        total_pages = int(page_info.get("totalPage") or 1)
        if page < total_pages and page < self.max_pages and self._scheduled[feed] < self.max_records:
            yield self._list_request(feed, page + 1)

    def parse_detail(self, response: Response, feed: str, list_record: Mapping[str, Any],
                     list_fingerprint: str, detail_url: str):
        detail = dict(self._result(response))
        notice_type, data, raw_html, raw_text, title, published, attachments = EqbiddingParser.parse(feed, detail, list_record)
        yield self.build_notice_item(
            notice_type=notice_type,
            notice_subtype=f"eqbidding|{config.CATEGORIES[feed][0]}",
            notice_id=str(detail.get("kid") or list_record.get("kid") or ""), title=title,
            publish_time=published, detail_url=detail_url, data=data,
            raw_data={"feed": feed, "list": dict(list_record), "detail": detail},
            raw_html=raw_html, raw_text=raw_text, extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version, source_list_fingerprint=list_fingerprint,
            attachments=attachments,
        )
