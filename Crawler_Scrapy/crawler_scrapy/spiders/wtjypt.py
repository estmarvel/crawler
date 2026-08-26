"""伟拓招标项目全部公开分类Spider。"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from io import BytesIO
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.ai.glm52_profile import (
    QWEN3_HYBRID_SETTINGS,
    install_hybrid_pipeline,
)
from crawler_scrapy.schemas.notice_fields import ANNOUNCEMENT_SCHEMAS
from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.wtjypt import config
from crawler_scrapy.sites.wtjypt.parser import WtjyptParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class WtjyptSpider(BaseNoticeSpider):
    name = "wtjypt"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.wtjypt.com", "wtjypt.com"]
    parser_version = "wtjypt-v4-rule-pdf-hybrid-ai"
    extraction_model_name = "wtjypt-site-rule-parser"
    ai_metadata_key = "wtjyptHybridAi"
    ai_trusted_fields_meta_key = "wtjyptApiTrustedFields"
    ai_log_name = "伟拓招标采购交易平台"

    # 只有真实样本中经常发生章节边界污染的长字段常规复核；其余字段仅在
    # 缺失但正文有明确标签、HTML残留或名单/报价错位时动态升级。
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
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 2.0,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        install_hybrid_pipeline(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines["crawler_scrapy.sites.wtjypt.exporter.WtjyptMultiFormatPipeline"] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

    @classmethod
    def _api_trusted_fields(
        cls, feed: str, payload: Mapping[str, Any], data: Mapping[str, Any],
        published: str,
    ) -> list[str]:
        """锁定伟拓详情 API 明确给出的字段，禁止 AI 改写。"""
        category = feed.split(".", 2)[1]
        if category == "plan":
            return [field for field, value in data.items() if value not in (None, "", [], {})]
        source_fields = {
            "项目编号/招标编号": payload.get("tenderProjectCode"),
            "招标编号/项目编号": payload.get("tenderProjectCode"),
            "依据文号": payload.get("tenderProjectCode"),
            "所属行业": payload.get("dicIndustriesType"),
            "项目类型/行业分类": payload.get("classificationName"),
            "招标方式": payload.get("tenderModeName"),
            "开标时间": payload.get("bidOpenTime"),
            "开启时间": payload.get("bidOpenTime"),
            "招标人/采购人名称": payload.get("tendereeName"),
            "招标人/采购人": payload.get("tendereeName"),
            "招标代理机构": payload.get("tenderAgencyName"),
            "发布日期": published,
            "发布网站": data.get("发布网站"),
        }
        return [field for field, value in source_fields.items() if value not in (None, "")]

    def __init__(
        self, feeds: str | None = None, modules: str | None = None,
        bidding_categories: str | None = None, purchase_categories: str | None = None,
        project_types: str | None = None, max_records: int | str = 200,
        days: int | str | None = None, start_date: str | None = None,
        end_date: str | None = None, parse_pdf: str | bool = True,
        *args: Any, **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.feeds = self._select_feeds(feeds, modules, bidding_categories, purchase_categories, project_types)
        self.max_records = self._positive_int(max_records, 200)
        self.window_start, self.window_end = self._window(days, start_date, end_date)
        self.parse_pdf = str(parse_pdf).lower() not in {"0", "false", "no", "off"}
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
    def _csv(value: str | None) -> set[str]:
        return {x.strip() for x in str(value or "").split(",") if x.strip()}

    @classmethod
    def _select_feeds(cls, feeds, modules, bidding_categories, purchase_categories, project_types) -> tuple[str, ...]:
        if feeds:
            selected = tuple(x.strip() for x in feeds.split(",") if x.strip())
            invalid = set(selected) - set(config.DEFAULT_FEEDS)
            if invalid:
                raise ValueError(f"不支持的 feeds: {','.join(sorted(invalid))}")
            return selected
        module_filter = cls._csv(modules) or {"bidding"}
        bid_filter = cls._csv(bidding_categories) or set(config.BIDDING_CATEGORIES)
        type_filter = cls._csv(project_types)
        invalid = ((module_filter - {"bidding"}) | (bid_filter - set(config.BIDDING_CATEGORIES)) |
                   (type_filter - set(config.PROJECT_TYPES)))
        if invalid:
            raise ValueError(f"不支持的栏目或类型: {','.join(sorted(invalid))}")
        result = []
        for feed in config.DEFAULT_FEEDS:
            module, category, project_type = feed.split(".", 2)
            if module not in module_filter:
                continue
            if module == "bidding" and category not in bid_filter:
                continue
            result.append(feed)
        return tuple(result)

    @classmethod
    def _window(cls, days, start_date, end_date):
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
    def _headers(referer: str) -> dict[str, str]:
        return {"Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/json; charset=utf-8",
                "X-Requested-With": "XMLHttpRequest", "Referer": referer}

    def start_requests(self):
        for feed in self.feeds:
            module, category, _ = feed.split(".", 2)
            referer = f"{config.BASE_URL}/trade/website/pages/{'zbinfo' if module == 'bidding' else 'procureinfo'}.html"
            body = json.dumps(config.list_payload(feed), ensure_ascii=False).encode()
            yield Request(config.list_endpoint(module, category), method="POST", body=body, headers=self._headers(referer),
                          callback=self.parse_list, cb_kwargs={"feed": feed}, dont_filter=True)

    async def start(self):
        for request in self.start_requests():
            yield request

    @staticmethod
    def _records(feed: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        category = feed.split(".", 2)[1]
        key = "planInfo" if category == "plan" else ("zbInfo" if feed.startswith("bidding.") else "cgInfo")
        return [dict(x) for x in payload.get(key, []) if isinstance(x, Mapping)]

    @staticmethod
    def _publish_time(record: Mapping[str, Any]) -> datetime | None:
        value = record.get("publishTime") or record.get("publishDate")
        if isinstance(value, (int, float)) or str(value or "").isdigit():
            number = int(value)
            if number > 10_000_000_000:
                number //= 1000
            return datetime.fromtimestamp(number)
        return coerce_datetime(value)

    @staticmethod
    def _notice_id(record: Mapping[str, Any]) -> str:
        return str(record.get("noticeId") or record.get("id") or "").strip()

    def parse_list(self, response: Response, feed: str):
        payload = json.loads(response.text)
        module, category, project_type = feed.split(".", 2)
        info_type = (config.BIDDING_CATEGORIES if module == "bidding" else config.PURCHASE_CATEGORIES).get(category, ("plan", ""))[0]
        for raw in self._records(feed, payload):
            if self._scheduled[feed] >= self.max_records:
                break
            published = self._publish_time(raw)
            if self.window_start and published and published < self.window_start:
                continue
            if self.window_end and published and published > self.window_end:
                continue
            notice_id = self._notice_id(raw)
            if not notice_id or (feed, notice_id) in self._seen:
                continue
            self._seen.add((feed, notice_id))
            detail_url = config.detail_page_url(module, category, notice_id, info_type)
            title = str(raw.get("noticeName") or raw.get("projectPlanName") or "")
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=notice_id, notice_type=config.feed_labels(feed)[1], list_record=raw,
                detail_url=detail_url, title=title, publish_time=published,
            )
            if not should_fetch:
                continue
            self._scheduled[feed] += 1
            body = f"notid={notice_id}" + ("" if category == "plan" else f"&type={info_type}")
            yield Request(config.detail_endpoint(module, category), method="POST", body=body.encode(),
                          headers={"Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest", "Referer": detail_url},
                          callback=self.parse_detail, cb_kwargs={"feed": feed, "list_record": raw,
                          "list_fingerprint": fingerprint, "detail_url": detail_url})

    def parse_detail(self, response: Response, feed: str, list_record: Mapping[str, Any], list_fingerprint: str, detail_url: str):
        payload = json.loads(response.text)
        context = {
            "feed": feed, "payload": payload, "list_record": dict(list_record),
            "list_fingerprint": list_fingerprint, "detail_url": detail_url,
            "response_metadata": self.build_response_metadata(
                response, request_kind="detail_api",
                context={"feed": feed, "noticeId": self._notice_id(list_record)},
            ),
        }
        pdf = next((item.get("file_url", "") for item in WtjyptParser._attachments(payload)
                    if str(item.get("file_name") or "").lower().endswith(".pdf")
                    or str(item.get("file_url") or "").lower().split("?", 1)[0].endswith(".pdf")
                    or "pdf" in str(item.get("file_type") or "").lower()), "")
        if self.parse_pdf and pdf:
            yield Request(
                pdf, headers={"Accept": "application/pdf,*/*", "Referer": detail_url},
                callback=self.parse_pdf_detail, errback=self.pdf_failed,
                cb_kwargs={"context": context}, meta={"wtjypt_context": context},
                dont_filter=True,
            )
            return
        yield self._build_item(context)

    def parse_pdf_detail(self, response: Response, context: Mapping[str, Any]):
        text = ""
        try:
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.body)).pages).strip()
            if not text:
                self.crawler.stats.inc_value("wtjypt/pdf_without_text_layer")
        except Exception as exc:
            self.logger.warning("PDF提取失败，使用HTML正文：%s", exc)
            self.crawler.stats.inc_value("wtjypt/pdf_extract_failed")
        enriched = dict(context)
        metadata = dict(context.get("response_metadata") or {})
        metadata["pdfTextSource"] = self.build_response_metadata(
            response, request_kind="attachment_pdf_text", context={"feed": context.get("feed", "")}
        )
        enriched["response_metadata"] = metadata
        yield self._build_item(enriched, pdf_text=text)

    def pdf_failed(self, failure):
        self.logger.warning("PDF下载失败，使用HTML正文：%s", failure.request.url)
        yield self._build_item(failure.request.meta["wtjypt_context"])

    def _build_item(self, context: Mapping[str, Any], *, pdf_text: str = ""):
        feed = str(context["feed"])
        payload = context["payload"]
        list_record = context["list_record"]
        notice_type, source_method, data, attachments, raw_html, raw_text, title, published = WtjyptParser.parse(
            feed, payload, list_record, pdf_text=pdf_text
        )
        module_label, category_label, project_label = config.feed_labels(feed)
        return self.build_notice_item(
            notice_type=notice_type, notice_subtype="|".join(("wtjypt", module_label, category_label, project_label)),
            notice_id=self._notice_id(list_record), title=title, publish_time=published,
            detail_url=str(context["detail_url"]), data=data,
            raw_data={"feed": feed, "list": dict(list_record), "detail": payload},
            raw_html=raw_html, raw_text=raw_text, extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=str(context.get("list_fingerprint") or ""),
            field_meta={
                "site_parser": self.extraction_model_name,
                "feed": feed,
                "project_type": project_label,
                self.ai_trusted_fields_meta_key: self._api_trusted_fields(
                    feed, payload, data, published
                ),
            },
            response_metadata=dict(context.get("response_metadata") or {}),
            attachments=attachments,
        )
