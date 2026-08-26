"""太原市公共资源交易中心工程建设、综合交易类 Spider。"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time
from typing import Any, Mapping
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.ai.glm52_profile import (
    QWEN3_HYBRID_SETTINGS,
    install_hybrid_pipeline,
)
from crawler_scrapy.schemas.notice_fields import ANNOUNCEMENT_SCHEMAS, coerce_datetime
from crawler_scrapy.sites.tyggzy import config
from crawler_scrapy.sites.tyggzy.parser import TyggzyParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class TyggzySpider(BaseNoticeSpider):
    name = "tyggzy"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["ggzy.xzspglj.taiyuan.gov.cn"]
    parser_version = TyggzyParser.VERSION
    extraction_model_name = "tyggzy-frontend-api-rule-parser"
    ai_metadata_key = "tyggzyHybridAi"
    ai_trusted_fields_meta_key = "tyggzyApiTrustedFields"
    ai_log_name = "太原市公共资源交易中心"

    ai_extract_fields = {
        "招标计划": (),
        "资格预审公告": (
            "资金来源", "项目概况与招标范围",
            "申请人资格要求/投标人资格要求", "获取方式", "递交方法",
            "投标保证金方式",
        ),
        "招标公告": (
            "资金来源", "项目规模", "招标内容与范围",
            "申请人资格要求/投标人资格要求", "工期/服务期/供货日期",
            "质量要求", "获取方式", "递交方法", "投标保证金方式",
        ),
        "中标候选人公示": (),
        "中标结果公示": (),
        "更正结果公示": (),
        "合同与履约": (),
    }
    ai_sparse_review_fields = {}
    ai_candidate_fields = {
        notice_type: tuple(
            field for field in ANNOUNCEMENT_SCHEMAS[notice_type]
            if field not in {
                "发布日期", "发布网站", "项目性质", "公告内容",
                "项目编号/招标编号", "招标编号/项目编号",
                "项目类型/行业分类",
            }
        )
        for notice_type in ai_extract_fields
    }

    custom_settings = {
        **QWEN3_HYBRID_SETTINGS,
        "ROBOTSTXT_OBEY": False, "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.5, "AUTOTHROTTLE_ENABLED": True,
        "NOTICE_SNAPSHOT_ENABLED": True, "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        install_hybrid_pipeline(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines["crawler_scrapy.sites.tyggzy.exporter.TyggzyMultiFormatPipeline"] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

    @classmethod
    def _api_trusted_fields(
        cls, feed: str, payload: Mapping[str, Any], record: Mapping[str, Any],
        data: Mapping[str, Any], published: str,
    ) -> list[str]:
        """按太原站实际 API 字段建立逐条可信锁，不按字段名盲目信任。"""
        category = feed.split(".", 1)[1]
        if category == "contract" and isinstance(payload.get("data"), Mapping):
            return [field for field, value in data.items() if value not in (None, "", [], {})]
        project_code = record.get("tenderProjectCode") or payload.get("tenderProjectCode")
        source_fields = {
            "项目名称": payload.get("title") or record.get("title"),
            "项目编号": project_code,
            "招标编号": project_code,
            "项目编号/招标编号": project_code,
            "招标编号/项目编号": project_code,
            "发布日期": published,
            "发布网站": data.get("发布网站"),
        }
        return [field for field, value in source_fields.items() if value not in (None, "")]

    def __init__(self, feeds: str | None = None, modules: str | None = None,
                 categories: str | None = None, max_records: int | str = 200,
                 max_pages: int | str = 500, page_size: int | str = 20,
                 start_date: str | None = None, end_date: str | None = None,
                 *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.feeds = self._feeds(feeds, modules, categories)
        self.max_records = self._positive(max_records, 200)
        self.max_pages = self._positive(max_pages, 500)
        self.page_size = min(self._positive(page_size, 20), 100)
        self.window_start = self._boundary(start_date)
        self.window_end = self._boundary(end_date, end=True)
        if self.window_start and self.window_end and self.window_start > self.window_end:
            raise ValueError("start_date 不能晚于 end_date")
        self._scheduled = defaultdict(int)
        self._seen: set[tuple[str, str]] = set()

    @staticmethod
    def _positive(value: Any, default: int) -> int:
        try:
            value = int(value)
            return value if value > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _csv(value: str | None) -> set[str]:
        return {part.strip() for part in str(value or "").split(",") if part.strip()}

    @classmethod
    def _feeds(cls, feeds, modules, categories) -> tuple[str, ...]:
        if feeds:
            result = tuple(x.strip() for x in feeds.split(",") if x.strip())
            invalid = set(result) - set(config.DEFAULT_FEEDS)
            if invalid:
                raise ValueError(f"不支持的 feeds: {','.join(sorted(invalid))}")
            return result
        module_set = cls._csv(modules) or set(config.MODULES)
        category_set = cls._csv(categories) or set(config.CATEGORIES)
        invalid = (module_set - set(config.MODULES)) | (category_set - set(config.CATEGORIES))
        if invalid:
            raise ValueError(f"不支持的模块或栏目: {','.join(sorted(invalid))}")
        return tuple(feed for feed in config.DEFAULT_FEEDS if feed.split(".")[0] in module_set and feed.split(".")[1] in category_set)

    @staticmethod
    def _boundary(value: str | None, end: bool = False):
        parsed = coerce_datetime(value) if value else None
        if parsed and end and len(str(value)) == 10:
            return datetime.combine(parsed.date(), time.max)
        return parsed

    @staticmethod
    def _headers(sign: str) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*", "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Origin": config.BASE_URL, "Referer": f"{config.BASE_URL}/#/", "sign": sign,
        }

    async def start(self):
        for feed in self.feeds:
            yield self._list_request(feed, 1)

    def _list_request(self, feed: str, page: int) -> Request:
        body, sign = config.signed_form(
            config.list_values(feed, page, self.page_size),
            ("secondArea", "industriesTypeCode", "hangYe", "title", "projectCode"),
        )
        return Request(config.LIST_URL, method="POST", body=body, headers=self._headers(sign),
                       callback=self.parse_list, cb_kwargs={"feed": feed, "page": page}, dont_filter=True)

    @staticmethod
    def _records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        for key in ("tenderList", "gcjsGGList", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(x) for x in value if isinstance(x, Mapping)]
        return []

    def parse_list(self, response: Response, feed: str, page: int):
        payload = json.loads(response.text)
        rows = self._records(payload)
        all_before = bool(rows and self.window_start)
        for row in rows:
            if self._scheduled[feed] >= self.max_records:
                break
            published = coerce_datetime(row.get("bulletinIssueTime") or row.get("submitTime") or row.get("publishTime"))
            if not published or not self.window_start or published >= self.window_start:
                all_before = False
            if self.window_start and published and published < self.window_start:
                continue
            if self.window_end and published and published > self.window_end:
                continue
            guid = str(row.get("guid") or row.get("id") or "").strip()
            if not guid or (feed, guid) in self._seen:
                continue
            title = str(row.get("title") or row.get("projectName") or "")
            detail_page = config.detail_page(feed, guid)
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=guid, notice_type=config.labels(feed)[1], list_record=row,
                detail_url=detail_page, title=title, publish_time=published,
            )
            if not should_fetch:
                continue
            self._seen.add((feed, guid)); self._scheduled[feed] += 1
            category = feed.split(".", 1)[1]
            if category == "manager_change":
                values = {"guid": guid}
                body = urlencode(values).encode()
                sign = config.header_sign(values); endpoint = config.MANAGER_DETAIL_URL
            else:
                body, sign = config.signed_form(config.detail_values(feed, guid)); endpoint = config.DETAIL_URL
            yield Request(endpoint, method="POST", body=body, headers=self._headers(sign),
                          callback=self.parse_detail, cb_kwargs={"feed": feed, "record": row,
                          "fingerprint": fingerprint, "detail_page": detail_page}, dont_filter=True)
        count = int(payload.get("count") or 0)
        has_next = (page < self.max_pages and self._scheduled[feed] < self.max_records and not all_before
                    and ((count and page * self.page_size < count) or (not count and len(rows) >= self.page_size)))
        if has_next:
            yield self._list_request(feed, page + 1)

    def parse_detail(self, response: Response, feed: str, record: Mapping[str, Any], fingerprint: str, detail_page: str):
        payload = json.loads(response.text)
        parsed, raw_html = TyggzyParser.parse(feed, payload, record)
        module, category = config.labels(feed)
        yield self.build_notice_item(
            notice_type=parsed.notice_type, notice_subtype=f"tyggzy|{module}|{category}",
            notice_id=str(record.get("guid") or record.get("id") or ""), title=parsed.title,
            publish_time=parsed.publish_time, detail_url=detail_page, data=parsed.data,
            raw_data={"feed": feed, "list": dict(record), "detail": payload}, raw_html=raw_html,
            raw_text=parsed.raw_text, extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version, source_list_fingerprint=fingerprint,
            field_meta={
                "site_parser": self.extraction_model_name,
                "feed": feed,
                self.ai_trusted_fields_meta_key: self._api_trusted_fields(
                    feed, payload, record, parsed.data, parsed.publish_time
                ),
            },
            attachments=parsed.attachments,
        )
