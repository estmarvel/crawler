"""山西焦煤电子招采平台“招标项目”公告爬虫。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.sxjm import config
from crawler_scrapy.sites.sxjm.parser import SxjmParser, decrypt_envelope
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class SxjmSpider(BaseNoticeSpider):
    """采集首页依法、招标、非招和简易采购四个频道的公告。"""

    name = "sxjm"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.sxccdzzcpt.cn"]
    parser_version = SxjmParser.parser_version
    extraction_model_name = "sxjm-site-rule-parser"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.5,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        # 详情 API 的 content 是源站实际公告 HTML。独立保存该原文，并由
        # Pipeline 回写 HTML快照路径/SHA256；JSON _trace.rawHtml 同时保留
        # 可直接导入 MongoDB raw_notices.rawHtml 的副本。
        "NOTICE_SNAPSHOT_ENABLED": True,
        # 极少数源站记录可能只有结构化字段而没有 content；不能因此丢掉
        # 整条公告，空 HTML 由 Pipeline 告警并保留原始 JSON payload。
        "NOTICE_SNAPSHOT_REQUIRED": False,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        """替换最终导出器，并为服务器本机直连启用限流保护。"""

        super().update_settings(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines[
            "crawler_scrapy.sites.sxjm.exporter.SxjmMultiFormatPipeline"
        ] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

        mode = str(settings.get("CRAWLER_OUTBOUND_MODE", "direct")).strip().lower()
        if mode != "direct":
            return
        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        middlewares[
            "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
        ] = 650
        settings.set("DOWNLOADER_MIDDLEWARES", middlewares, priority="spider")
        settings.set("HTTPPROXY_ENABLED", False, priority="spider")
        settings.set(
            "CONCURRENT_REQUESTS",
            settings.getint("DIRECT_CONCURRENT_REQUESTS", 1),
            priority="spider",
        )
        settings.set(
            "CONCURRENT_REQUESTS_PER_DOMAIN",
            settings.getint("DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN", 1),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_DELAY",
            settings.getfloat("DIRECT_DOWNLOAD_DELAY", 5.0),
            priority="spider",
        )
        settings.set("RANDOMIZE_DOWNLOAD_DELAY", True, priority="spider")
        settings.set("AUTOTHROTTLE_ENABLED", True, priority="spider")
        settings.set(
            "AUTOTHROTTLE_START_DELAY",
            settings.getfloat("DIRECT_AUTOTHROTTLE_START_DELAY", 5.0),
            priority="spider",
        )
        settings.set(
            "AUTOTHROTTLE_MAX_DELAY",
            settings.getfloat("DIRECT_AUTOTHROTTLE_MAX_DELAY", 120.0),
            priority="spider",
        )
        settings.set(
            "AUTOTHROTTLE_TARGET_CONCURRENCY",
            settings.getfloat("DIRECT_AUTOTHROTTLE_TARGET_CONCURRENCY", 0.25),
            priority="spider",
        )
        settings.set(
            "RETRY_TIMES",
            settings.getint("DIRECT_RETRY_TIMES", 0),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_TIMEOUT",
            settings.getint("DIRECT_DOWNLOAD_TIMEOUT", 180),
            priority="spider",
        )
        settings.set(
            "CLOSESPIDER_PAGECOUNT",
            settings.getint("DIRECT_MAX_RESPONSES_PER_RUN", 300),
            priority="spider",
        )

    def __init__(
        self,
        channels: str | None = None,
        sections: str | None = None,
        max_records: int | str = 200,
        page_size: int | str = 20,
        max_pages: int | str = 100,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.channels = self._parse_channels(channels)
        self.sections = self._parse_sections(sections)
        self.feeds = tuple(config.feeds(self.channels, self.sections))
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, 20), 100)
        self.max_pages = self._positive_int(max_pages, 100)
        requested_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._parse_time_window(
            requested_days, start_date, end_date
        )
        self._scheduled_counts = {feed: 0 for feed in self.feeds}
        self._seen_ids: set[str] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _parse_boundary(value: str | None, *, end_of_day: bool) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = coerce_datetime(raw)
        if parsed is None:
            raise ValueError(f"无法解析日期时间 {raw!r}")
        if end_of_day and len(raw) == 10:
            return datetime.combine(parsed.date(), time.max)
        return parsed

    @classmethod
    def _parse_time_window(
        cls,
        days: int | str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[datetime | None, datetime | None]:
        end = cls._parse_boundary(end_date, end_of_day=True)
        start = cls._parse_boundary(start_date, end_of_day=False)
        if start is None and days not in (None, ""):
            try:
                count = int(days)
            except (TypeError, ValueError) as exc:
                raise ValueError("days/lookback_days 必须是正整数") from exc
            if count <= 0:
                raise ValueError("days/lookback_days 必须大于 0")
            start = (end or datetime.now()) - timedelta(days=count)
        if start is not None and end is not None and start > end:
            raise ValueError("start_date 不能晚于 end_date")
        return start, end

    @staticmethod
    def _parse_channels(value: str | None) -> tuple[str, ...]:
        if not value:
            return config.DEFAULT_CHANNELS
        channels = tuple(part.strip() for part in value.split(",") if part.strip())
        invalid = [channel for channel in channels if channel not in config.CHANNELS]
        if invalid:
            raise ValueError(f"不支持的 channels: {','.join(invalid)}")
        return channels or config.DEFAULT_CHANNELS

    def _parse_sections(self, value: str | None) -> tuple[str, ...] | None:
        if not value:
            return None
        sections = tuple(part.strip() for part in value.split(",") if part.strip())
        valid = {
            section
            for channel in self.channels
            for section in config.CHANNELS[channel]["sections"]
        }
        invalid = [section for section in sections if section not in valid]
        if invalid:
            raise ValueError(f"所选频道不支持 sections: {','.join(invalid)}")
        return sections or None

    def start_requests(self):
        for channel, section, announcement_type in self.feeds:
            yield self._list_request(channel, section, announcement_type, 1)

    async def start(self):
        """兼容 Scrapy 2.13+ 的异步启动入口。"""

        for request in self.start_requests():
            yield request

    def _list_request(
        self, channel: str, section: str, announcement_type: str, page: int
    ) -> Request:
        query = urlencode(
            config.list_params(channel, announcement_type, page, self.page_size)
        )
        return Request(
            f"{config.LIST_URL}?{query}",
            callback=self.parse_list,
            cb_kwargs={
                "channel": channel,
                "section": section,
                "announcement_type": announcement_type,
                "page": page,
            },
            dont_filter=True,
        )

    @staticmethod
    def _source_publish_time(record: Mapping[str, Any]) -> str:
        for key in ("publish_time_format", "publish_time", "created_at_format", "created_at"):
            value = str(record.get(key) or "").strip()
            parsed = coerce_datetime(value)
            # 部分招标计划把缺失发布时间序列化为 Unix 零值；不能把它当成
            # 真实业务时间，应继续回退到 created_at。
            if parsed is not None and parsed.year > 1970:
                return value
        return ""

    @classmethod
    def _record_time(cls, record: Mapping[str, Any]) -> datetime | None:
        value = cls._source_publish_time(record)
        if value:
            return coerce_datetime(value)
        return None

    def _inside_window(self, record: Mapping[str, Any]) -> bool:
        published = self._record_time(record)
        if published is None:
            return True
        if self.window_start is not None and published < self.window_start:
            return False
        return self.window_end is None or published <= self.window_end

    def parse_list(
        self, response: Response, channel: str, section: str,
        announcement_type: str, page: int
    ):
        feed = (channel, section, announcement_type)
        envelope = response.json()
        result = decrypt_envelope(envelope)
        if not isinstance(result, Mapping):
            self.logger.warning("列表返回类型异常: section=%s page=%s", section, page)
            return
        records = result.get("data") or []
        if not isinstance(records, list):
            records = []
        request_params = config.list_params(
            channel, announcement_type, page, self.page_size
        )
        list_trace = {
            "responseMetadata": self.build_response_metadata(
                response,
                request_kind="list_api",
                context={
                    "channel": channel,
                    "section": section,
                    "announcementType": announcement_type,
                    "page": page,
                    "pageSize": self.page_size,
                },
            ),
            "requestParams": request_params,
            "businessEnvelope": {
                key: value for key, value in envelope.items() if key != "result"
            },
            "pagination": {
                key: value for key, value in result.items() if key != "data"
            },
        }

        scheduled_before = self._scheduled_counts[feed]
        for record in records:
            if not isinstance(record, Mapping) or not self._inside_window(record):
                continue
            notice_id = str(record.get("id") or "").strip()
            if not notice_id or notice_id in self._seen_ids:
                continue
            if self._scheduled_counts[feed] >= self.max_records:
                break
            self._seen_ids.add(notice_id)
            detail_url = config.detail_page_url(notice_id)
            should_fetch, list_fingerprint = self.check_notice_candidate(
                notice_id=notice_id,
                list_record=record,
                detail_url=detail_url,
                title=str(record.get("title") or ""),
                publish_time=self._source_publish_time(record),
            )
            if not should_fetch:
                continue
            self._scheduled_counts[feed] += 1
            record_with_trace = dict(record)
            record_with_trace["_crawler_list_trace"] = list_trace
            yield Request(
                config.DETAIL_URL.format(notice_id=notice_id),
                callback=self.parse_detail,
                # 列表阶段的持久化公告索引才是跨运行事实来源。这里不让
                # JOBDIR 的 URL 去重永久吞掉“已请求但尚未来得及导出”的详情，
                # 也允许显式 --check-updates 时重新获取内容发生变化的同一 URL。
                dont_filter=True,
                cb_kwargs={
                    "channel": channel,
                    "section": section,
                    "announcement_type": announcement_type,
                    "list_record": record_with_trace,
                    "list_fingerprint": list_fingerprint,
                },
            )

        crawler = getattr(self, "crawler", None)
        if crawler is not None and crawler.settings.getbool(
            "SXJM_PROGRESS_LOG_LIST", False
        ):
            self.logger.info(
                "[SXJM列表进度] 频道=%s 栏目=%s 编码=%s 页=%s "
                "本页=%s 总数=%s 新增详情=%s 当前feed已安排=%s",
                channel,
                section,
                announcement_type,
                page,
                len(records),
                result.get("total") or 0,
                self._scheduled_counts[feed] - scheduled_before,
                self._scheduled_counts[feed],
            )

        total = int(result.get("total") or 0)
        more = page * self.page_size < total and bool(records)
        if (
            more
            and page < self.max_pages
            and self._scheduled_counts[feed] < self.max_records
        ):
            yield self._list_request(channel, section, announcement_type, page + 1)

    def parse_detail(
        self,
        response: Response,
        channel: str,
        section: str,
        announcement_type: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
    ):
        envelope = response.json()
        result = decrypt_envelope(envelope)
        if not isinstance(result, Mapping):
            self.logger.warning("详情返回类型异常: %s", response.url)
            return
        source_list = dict(list_record)
        list_trace = source_list.pop("_crawler_list_trace", None)
        source_detail = dict(result)
        detail = {**source_list, **source_detail}
        # 详情接口中的 announcement_type 偶有缺失或历史值不一致；请求列表时
        # 使用的编码才是本次分类依据，单独保留，且不覆盖原始响应。
        detail["_crawler_announcement_type"] = str(announcement_type)
        subtype, notice_type, data, attachments = SxjmParser.parse(
            channel, section, detail
        )
        if subtype != section:
            raise ValueError(
                f"SXJM解析栏目不一致: requested={section} parsed={subtype}"
            )
        if not notice_type:
            self.logger.warning("无法识别公告类型: id=%s section=%s", detail.get("id"), section)
            return
        notice_id = str(detail.get("id") or "")
        raw_html = SxjmParser.raw_html(detail)
        response_metadata = self.build_response_metadata(
            response,
            request_kind="detail_api",
            context={
                "channel": channel,
                "section": section,
                "announcementType": str(announcement_type),
            },
        )
        response_metadata["businessEnvelope"] = {
            key: value for key, value in envelope.items() if key != "result"
        }
        if isinstance(list_trace, Mapping):
            response_metadata["relatedRequests"] = {
                "list": list_trace.get("responseMetadata"),
            }
            response_metadata["listBusinessEnvelope"] = list_trace.get(
                "businessEnvelope"
            )
        yield self.build_notice_item(
            notice_type=notice_type,
            notice_subtype=f"{channel}.{subtype}",
            notice_id=notice_id,
            title=str(detail.get("title") or detail.get("project_name") or ""),
            publish_time=self._source_publish_time(detail),
            detail_url=config.detail_page_url(notice_id),
            data=data,
            raw_data={
                "list": source_list,
                "detail": source_detail,
                "transport": {
                    "list": list_trace,
                    "detailEnvelope": response_metadata["businessEnvelope"],
                },
            },
            raw_html=raw_html,
            raw_text=SxjmParser.raw_text(detail),
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            response_metadata=response_metadata,
            field_meta={
                "site_parser": self.parser_version,
                "channel": channel,
                "section": section,
                "source_notice_type": config.source_notice_type(section),
                "source_section_label": config.section_label(channel, section),
                "source_announcement_type": str(announcement_type),
                "source_announcement_type_label": config.announcement_type_label(
                    announcement_type
                ),
                "source_notice_nature": data.get("源站公告性质", ""),
                "schema_notice_type": config.schema_notice_type(section),
                "detail_source": "decrypted_detail_api",
            },
            source_list_fingerprint=list_fingerprint,
            attachments=attachments,
        )
