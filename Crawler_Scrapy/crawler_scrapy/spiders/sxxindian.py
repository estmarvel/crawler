"""山西新点招标信息、企业采购全栏目采集 Spider。"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from scrapy import FormRequest, Request
from scrapy.http import Response

from crawler_scrapy.ai.glm52_profile import (
    QWEN3_HYBRID_SETTINGS,
    install_hybrid_pipeline,
)
from crawler_scrapy.schemas.notice_fields import (
    ANNOUNCEMENT_SCHEMAS,
    coerce_datetime,
    normalize_notice_type,
)
from crawler_scrapy.sites.sxxindian import config
from crawler_scrapy.sites.sxxindian.parser import SxxindianParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


SXXINDIAN_AI_FIXED_FIELDS = frozenset(
    {
        "发布日期",
        "发布网站",
        "项目性质",
        "项目编号/招标编号",
        "招标编号/项目编号",
        "项目类型/行业分类",
        "公告内容",
    }
)


class SxxindianSpider(BaseNoticeSpider):
    name = "sxxindian"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.sxxindian.com", "sxxindian.com"]
    parser_version = "sxxindian-v4-unambiguous-multi-lot-amount"
    extraction_model_name = "sxxindian-site-rule-parser"
    ai_metadata_key = "sxxindianHybridAi"
    ai_trusted_fields_meta_key = "sxxindianTrustedFields"
    ai_log_name = "山西新点"

    ai_extract_fields = {
        "招标计划": ("建设内容及规模",),
        "资格预审公告": (
            "资金来源",
            "项目概况与招标范围",
            "申请人资格要求/投标人资格要求",
            "获取方式",
            "递交方法",
            "投标保证金方式",
        ),
        "招标公告": (
            "资金来源",
            "项目规模",
            "招标内容与范围",
            "申请人资格要求/投标人资格要求",
            "工期/服务期/供货日期",
            "质量要求",
            "获取方式",
            "递交方法",
            "投标保证金方式",
        ),
        "中标候选人公示": (),
        "中标结果公示": (),
        "更正结果公示": (),
        "合同与履约": (),
    }
    ai_sparse_review_fields = {}
    ai_candidate_fields = {
        notice_type: tuple(
            field
            for field in ANNOUNCEMENT_SCHEMAS[notice_type]
            if field not in SXXINDIAN_AI_FIXED_FIELDS
        )
        for notice_type in ai_extract_fields
    }

    custom_settings = {
        **QWEN3_HYBRID_SETTINGS,
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.5,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": False,
        # 公告与附件分阶段运行，避免大文件阻塞公告和 AI 字段审查。
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": False,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        install_hybrid_pipeline(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines[
            "crawler_scrapy.sites.sxxindian.exporter.SxxindianMultiFormatPipeline"
        ] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

    def __init__(
        self,
        feeds: str | None = None,
        modules: str | None = None,
        bidding_categories: str | None = None,
        project_types: str | None = None,
        purchase_categories: str | None = None,
        max_records: int | str = 200,
        max_records_per_notice_type: int | str = 0,
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
        self.feeds = self._select_feeds(
            feeds, modules, bidding_categories, project_types, purchase_categories
        )
        self.max_records = self._positive_int(max_records, 200)
        self.max_records_per_notice_type = self._nonnegative_int(
            max_records_per_notice_type, 0
        )
        self.page_size = min(self._positive_int(page_size, 20), 100)
        self.max_pages = self._positive_int(max_pages, 100)
        requested_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._parse_time_window(
            requested_days, start_date, end_date
        )
        # 新点列表接口要求日期；未指定时默认采集近一年，历史脚本会显式传开始日期。
        if self.window_start is None:
            self.window_start = datetime.now() - timedelta(days=365)
        if self.window_end is None:
            self.window_end = datetime.now()
        self._scheduled_counts: dict[str, int] = defaultdict(int)
        self._scheduled_type_counts: dict[str, int] = defaultdict(int)
        self._emitted_type_counts: dict[str, int] = defaultdict(int)
        self._existing_type_counts_loaded = False
        self._seen: set[tuple[str, str]] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _nonnegative_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default

    @staticmethod
    def _predicted_notice_type(feed: str, title: str = "") -> str:
        module, category, _ = feed.split(".", 2)
        return SxxindianParser._notice_type(module, category, title)

    def _type_quota_reached(self, notice_type: str) -> bool:
        return bool(
            self.max_records_per_notice_type
            and self._scheduled_type_counts[notice_type]
            >= self.max_records_per_notice_type
        )

    def _feed_quota_reached(self, feed: str) -> bool:
        # “其他公告”会按标题/正文落入多种统一类型，不能用单一固定类型
        # 提前停止翻页；仍受每 feed 的 max_records 安全上限保护。
        _, category, _ = feed.split(".", 2)
        return category != "other" and self._type_quota_reached(
            self._predicted_notice_type(feed)
        )

    @staticmethod
    def _csv(value: str | None) -> set[str]:
        return {x.strip() for x in str(value or "").split(",") if x.strip()}

    @classmethod
    def _select_feeds(
        cls,
        feeds: str | None,
        modules: str | None,
        bidding_categories: str | None,
        project_types: str | None,
        purchase_categories: str | None,
    ) -> tuple[str, ...]:
        if feeds:
            selected = tuple(x.strip() for x in feeds.split(",") if x.strip())
            invalid = [x for x in selected if x not in config.DEFAULT_FEEDS]
            if invalid:
                raise ValueError(f"不支持的 feeds: {','.join(invalid)}")
            return selected

        module_filter = cls._csv(modules) or {"bidding", "purchase"}
        bad_modules = module_filter - {"bidding", "purchase"}
        if bad_modules:
            raise ValueError(f"不支持的 modules: {','.join(sorted(bad_modules))}")
        bid_filter = cls._csv(bidding_categories) or set(config.BIDDING_CATEGORIES)
        type_filter = cls._csv(project_types) or set(config.PROJECT_TYPES)
        buy_filter = cls._csv(purchase_categories) or set(config.PURCHASE_CATEGORIES)
        invalid = (
            (bid_filter - set(config.BIDDING_CATEGORIES))
            | (type_filter - set(config.PROJECT_TYPES))
            | (buy_filter - set(config.PURCHASE_CATEGORIES))
        )
        if invalid:
            raise ValueError(f"不支持的栏目或类型: {','.join(sorted(invalid))}")

        result: list[str] = []
        for feed in config.DEFAULT_FEEDS:
            module, category, detail_type = feed.split(".", 2)
            if module not in module_filter:
                continue
            if module == "bidding" and (
                category not in bid_filter
                or (detail_type != "all" and detail_type not in type_filter)
            ):
                continue
            if module == "purchase" and category not in buy_filter:
                continue
            result.append(feed)
        return tuple(result)

    @staticmethod
    def _parse_boundary(value: str | None, *, end_of_day: bool) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = coerce_datetime(raw)
        if parsed is None:
            raise ValueError(f"无法解析日期时间 {raw!r}")
        return datetime.combine(parsed.date(), time.max) if end_of_day and len(raw) == 10 else parsed

    @classmethod
    def _parse_time_window(
        cls, days: int | str | None, start_date: str | None, end_date: str | None
    ) -> tuple[datetime | None, datetime | None]:
        start = cls._parse_boundary(start_date, end_of_day=False)
        end = cls._parse_boundary(end_date, end_of_day=True)
        if start is None and days not in (None, ""):
            count = int(days)
            if count <= 0:
                raise ValueError("days/lookback_days 必须大于0")
            start = (end or datetime.now()) - timedelta(days=count)
        if start and end and start > end:
            raise ValueError("start_date 不能晚于 end_date")
        return start, end

    def start_requests(self):
        self._load_existing_type_counts()
        for feed in self.feeds:
            if not self._feed_quota_reached(feed):
                yield self._list_request(feed, 0)

    def _load_existing_type_counts(self) -> None:
        if self._existing_type_counts_loaded:
            return
        self._existing_type_counts_loaded = True
        if not self.max_records_per_notice_type:
            return
        root = Path(
            self.crawler.settings.get("NOTICE_OUTPUT_ROOT", "new_output")
        ).expanduser().resolve()
        counts: dict[str, int] = defaultdict(int)
        for path in (root / self.platform_code / "json").glob("*.json"):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for row in rows if isinstance(rows, list) else ():
                if not isinstance(row, Mapping):
                    continue
                notice_type = normalize_notice_type(row.get("公告类型"))
                if notice_type:
                    counts[notice_type] += 1
        for notice_type, count in counts.items():
            self._scheduled_type_counts[notice_type] = max(
                self._scheduled_type_counts[notice_type], count
            )
            self._emitted_type_counts[notice_type] = max(
                self._emitted_type_counts[notice_type], count
            )

    async def start(self):
        for request in self.start_requests():
            yield request

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{config.BASE_URL}/biddingInfo.html",
        }

    def _list_request(self, feed: str, page_index: int) -> FormRequest:
        module = feed.split(".", 1)[0]
        return FormRequest(
            config.list_endpoint(module),
            method="POST",
            formdata=config.list_form(
                feed,
                start_date=self.window_start.strftime("%Y-%m-%d"),
                end_date=self.window_end.strftime("%Y-%m-%d"),
                page_size=self.page_size,
                page_index=page_index,
            ),
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"feed": feed, "page_index": page_index},
            dont_filter=True,
        )

    @staticmethod
    def _decode_list(response: Response) -> tuple[list[dict[str, Any]], int]:
        payload = json.loads(response.text)
        custom = payload.get("custom") if isinstance(payload, Mapping) else None
        if isinstance(custom, str):
            custom = json.loads(custom)
        if not isinstance(custom, Mapping):
            return [], 0
        records = custom.get("Table") or []
        if isinstance(records, Mapping):
            records = [records]
        return [dict(x) for x in records if isinstance(x, Mapping)], int(custom.get("RowCount") or 0)

    @staticmethod
    def _record_time(record: Mapping[str, Any]) -> datetime | None:
        return coerce_datetime(record.get("date") or record.get("publishdate") or record.get("time"))

    def _inside_window(self, record: Mapping[str, Any]) -> bool:
        published = self._record_time(record)
        if published is None:
            return True
        return self.window_start <= published <= self.window_end

    @staticmethod
    def _notice_id(feed: str, record: Mapping[str, Any]) -> str:
        href = str(record.get("href") or "").strip()
        path = urlparse(href).path.rstrip("/")
        candidate = path.rsplit("/", 1)[-1].split(".", 1)[0]
        if candidate and candidate.lower() not in {"showinfo", "info", "detail"}:
            return candidate
        seed = f"{feed}|{href}|{record.get('title')}|{record.get('date')}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()

    def parse_list(self, response: Response, feed: str, page_index: int):
        try:
            records, total = self._decode_list(response)
        except Exception as exc:
            self.logger.warning("列表接口解析失败 feed=%s page=%s: %s", feed, page_index, exc)
            return

        boundary_index = self.descending_time_boundary_index(
            records, self._record_time, self.window_start
        )
        candidate_records = records[:boundary_index] if boundary_index is not None else records
        module, category, detail_type = feed.split(".", 2)
        _, category_label, project_label = config.feed_labels(feed)
        for raw in candidate_records:
            if self._scheduled_counts[feed] >= self.max_records:
                break
            if not self._inside_window(raw):
                continue
            href = str(raw.get("href") or "").strip()
            if not href:
                continue
            notice_id = self._notice_id(feed, raw)
            identity = (feed, notice_id)
            if identity in self._seen:
                continue
            self._seen.add(identity)
            detail_url = urljoin(config.BASE_URL + "/", href)
            title = str(raw.get("title") or "").strip()
            predicted_notice_type = self._predicted_notice_type(feed, title)
            if self._type_quota_reached(predicted_notice_type):
                continue
            publish_time = self._record_time(raw)
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=notice_id,
                notice_type=category_label,
                list_record=raw,
                detail_url=detail_url,
                title=title,
                publish_time=publish_time,
            )
            if not should_fetch:
                continue
            self._scheduled_counts[feed] += 1
            self._scheduled_type_counts[predicted_notice_type] += 1
            yield Request(
                detail_url,
                headers={"Referer": response.url},
                callback=self.parse_detail,
                cb_kwargs={
                    "feed": feed,
                    "list_record": raw,
                    "list_fingerprint": fingerprint,
                    "module_label": "招标信息" if module == "bidding" else "企业采购",
                    "category_label": category_label,
                    "project_label": project_label,
                    "predicted_notice_type": predicted_notice_type,
                },
            )

        next_page = page_index + 1
        if (
            records
            and boundary_index is None
            and next_page * self.page_size < total
            and next_page < self.max_pages
            and self._scheduled_counts[feed] < self.max_records
            and not self._feed_quota_reached(feed)
        ):
            yield self._list_request(feed, next_page)
        elif boundary_index is not None:
            self.crawler.stats.inc_value("history/time_boundary_stops")
            self.logger.info(
                "[SXXINDIAN列表结束] feed=%s page=%s "
                "reason=time_boundary_reached boundary_index=%s",
                feed, page_index, boundary_index,
            )

    def parse_detail(
        self,
        response: Response,
        feed: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
        module_label: str,
        category_label: str,
        project_label: str,
        predicted_notice_type: str = "",
    ):
        notice_type, source_method, data, attachments, raw_html, raw_text = SxxindianParser.parse(
            feed, response.text, list_record
        )
        self._load_existing_type_counts()
        if (
            self.max_records_per_notice_type
            and self._emitted_type_counts[notice_type]
            >= self.max_records_per_notice_type
        ):
            return
        self._emitted_type_counts[notice_type] += 1
        detail_type = project_label
        if feed.startswith("purchase."):
            detail_type = source_method or "其他/未标明"
        notice_id = self._notice_id(feed, list_record)
        if predicted_notice_type and notice_type != predicted_notice_type:
            self.crawler.stats.inc_value("sxxindian/predicted_type_mismatch")
        title = str(list_record.get("title") or data.get("项目名称") or "").strip()
        yield self.build_notice_item(
            notice_type=notice_type,
            notice_subtype="|".join(
                ("sxxindian", module_label, category_label, detail_type)
            ),
            notice_id=notice_id,
            title=title,
            publish_time=self._record_time(list_record),
            detail_url=response.url,
            data=data,
            raw_data={"feed": feed, "list": dict(list_record)},
            raw_html=raw_html,
            raw_text=raw_text,
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            field_meta={
                "site_parser": self.parser_version,
                "source_feed": feed,
                "source_module": module_label,
                "source_category": category_label,
                "source_project_type": project_label,
                self.ai_trusted_fields_meta_key: [
                    field
                    for field, present in (
                        ("发布日期", bool(self._record_time(list_record))),
                        ("发布网站", bool(data.get("发布网站"))),
                        (
                            "项目类型/行业分类",
                            bool(data.get("项目类型/行业分类")),
                        ),
                    )
                    if present
                ],
            },
            response_metadata=self.build_response_metadata(
                response,
                request_kind="detail_html",
                context={"feed": feed, "noticeId": notice_id},
            ),
            attachments=attachments,
        )
