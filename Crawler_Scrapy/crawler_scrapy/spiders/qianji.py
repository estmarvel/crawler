"""千极链招标信息五类公告爬虫。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from io import BytesIO
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import ANNOUNCEMENT_SCHEMAS, coerce_datetime
from crawler_scrapy.sites.qianji import config
from crawler_scrapy.sites.qianji.ai_provider import API_KEY_ENV, BASE_URL, MODEL
from crawler_scrapy.sites.qianji.parser import QianjiParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


QIANJI_AI_DERIVED_OR_FIXED_FIELDS = frozenset(
    {
        "项目性质",
        "发布日期",
        "发布网站",
        "项目编号/招标编号",
        "招标编号/项目编号",
        "项目类型/行业分类",
    }
)


class QianjiSpider(BaseNoticeSpider):
    name = "qianji"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.qianjilink.com"]
    parser_version = "qianji-ai-v2"
    extraction_model_name = "qianji-ai-parser"
    # 千极数把“招标公告”和“变更公告”都映射到 TENDER Schema；验证配额
    # 仍按五个源栏目统计，避免前一个栏目占满 50 条后完全看不到变更样本。
    ai_validation_quota_by_source_category = True
    ai_validation_quota_types = ("plan", "tender", "change", "candidate", "award")

    # 按《八类公告重要业务字段提取标准》和 C 方案：只将真实样本中
    # 持续存在边界污染的字段进入常规候选窗口 AI。编号、发布信息、
    # 时间、金额和表格对齐字段由 API/DOM/规则优先，只在缺失、
    # 含 HTML、列表错位或其他明确异常时动态升级。
    ai_extract_fields = {
        "招标计划": (
            "建设内容及规模",
        ),
        "招标公告": (
            "资金来源",
            "项目规模",
            "招标内容与范围",
            "质量要求",
            "获取方式",
            "递交方法",
            "投标保证金方式",
        ),
        "中标候选人公示": (),
        "中标结果公示": (),
        "更正结果公示": (),
    }
    # 这些字段确实需要 AI 纠正规则边界，但并非每条公告都会出现。仅在规则
    # 已命中或正文出现明确标签时加入本条请求，减少模型空输出和无效调用。
    ai_sparse_review_fields = {}
    ai_candidate_fields = {
        notice_type: tuple(
            field
            for field in ANNOUNCEMENT_SCHEMAS[notice_type]
            if field not in QIANJI_AI_DERIVED_OR_FIXED_FIELDS
        )
        for notice_type in (
            "招标计划",
            "招标公告",
            "中标候选人公示",
            "中标结果公示",
            "更正结果公示",
        )
    }

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.7,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": False,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
        "NOTICE_AI_API_KEY_ENV": API_KEY_ENV,
        "NOTICE_AI_BASE_URL": BASE_URL,
        "NOTICE_AI_MODEL": MODEL,
        "NOTICE_AI_JSON_MODE": True,
        "NOTICE_AI_ENABLE_THINKING": False,
        "NOTICE_AI_INCLUDE_OPTIONAL_FIELDS": True,
        # 小样本验收阶段限制 Item 并行并拉开请求起始时间，避免同时提交
        # 多个 GLM-5.2 请求后触发限流，也防止 AI 阶段反向拖慢公告落盘。
        "CONCURRENT_ITEMS": 3,
        "REACTOR_THREADPOOL_MAXSIZE": 4,
        "NOTICE_AI_MIN_INTERVAL": 2.0,
        "NOTICE_AI_TIMEOUT": 90.0,
        "NOTICE_AI_RETRY_TIMES": 0,
        # C2 对长字段只返回原文行范围，不再重复生成整段正文。
        "NOTICE_AI_MAX_OUTPUT_TOKENS": 1200,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.AiHtmlExtractionPipeline"] = None
        pipelines[
            "crawler_scrapy.sites.qianji.hybrid_ai.QianjiHybridAiExtractionPipeline"
        ] = 200
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines["crawler_scrapy.sites.qianji.exporter.QianjiMultiFormatPipeline"] = 300
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
        project_types: str | None = None,
        max_records: int | str = 200,
        page_size: int | str = 20,
        max_pages: int | str = 100,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        parse_pdf: str | bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.feeds = self._select_feeds(categories, project_types)
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, 20), 100)
        self.max_pages = self._positive_int(max_pages, 100)
        selected_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._time_window(selected_days, start_date, end_date)
        self.parse_pdf = str(parse_pdf).lower() not in {"0", "false", "no", "off"}
        self._counts = {feed: 0 for feed in self.feeds}
        self._seen: set[str] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _select_feeds(categories: str | None, project_types: str | None) -> tuple[str, ...]:
        cats = {x.strip() for x in categories.split(",") if x.strip()} if categories else {"plan", "tender", "change", "candidate", "award"}
        types = {x.strip() for x in project_types.split(",") if x.strip()} if project_types else {"engineering", "goods", "service"}
        invalid_cats = cats - {"plan", "tender", "change", "candidate", "award"}
        invalid_types = types - {"engineering", "goods", "service"}
        if invalid_cats or invalid_types:
            raise ValueError(f"不支持的栏目/项目类型：{sorted(invalid_cats | invalid_types)}")
        return tuple(feed for feed in config.DEFAULT_FEEDS if feed.split(".")[0] in cats and (feed == "plan.all" or feed.split(".")[1] in types))

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

    def start_requests(self):
        for feed in self.feeds:
            yield self._list_request(feed, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    @staticmethod
    def _headers(accept="application/json, text/plain, */*"):
        return {"Accept": accept, "Referer": f"{config.WEB_BASE_URL}/"}

    def _list_request(self, feed: str, page: int) -> Request:
        return Request(config.list_url(feed, page, self.page_size), headers=self._headers(), callback=self.parse_list, cb_kwargs={"feed": feed, "page": page}, dont_filter=True)

    @staticmethod
    def _published(record: Mapping[str, Any]) -> datetime | None:
        return coerce_datetime(record.get("noticeStartTime") or record.get("createTime"))

    def _inside_window(self, record: Mapping[str, Any]) -> bool:
        value = self._published(record)
        return not value or ((not self.window_start or value >= self.window_start) and (not self.window_end or value <= self.window_end))

    def parse_list(self, response: Response, feed: str, page: int):
        payload = response.json()
        records = payload.get("rows") or [] if isinstance(payload, Mapping) else []
        reached_before_window = False
        for record in records:
            if not isinstance(record, Mapping):
                continue
            published = self._published(record)
            if self.window_start and published and published < self.window_start:
                reached_before_window = True
            if not self._inside_window(record):
                continue
            notice_id = str(record.get("id") or "")
            if not notice_id or notice_id in self._seen or self._counts[feed] >= self.max_records:
                continue
            self._seen.add(notice_id)
            detail_url = config.detail_page_url(notice_id)
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=notice_id, list_record=record, detail_url=detail_url,
                notice_type=config.FEEDS[feed][0], title=str(record.get("title") or ""),
                publish_time=self._published(record),
            )
            if not should_fetch:
                continue
            self._counts[feed] += 1
            yield Request(config.detail_api_url(notice_id), headers=self._headers(), callback=self.parse_detail, cb_kwargs={"feed": feed, "list_record": dict(record), "list_fingerprint": fingerprint})
        total = int(payload.get("total") or 0) if isinstance(payload, Mapping) else 0
        if records and not reached_before_window and page * self.page_size < total and page < self.max_pages and self._counts[feed] < self.max_records:
            yield self._list_request(feed, page + 1)

    def parse_detail(self, response: Response, feed: str, list_record: Mapping[str, Any], list_fingerprint: str):
        payload = response.json()
        detail = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(detail, Mapping):
            self.logger.warning("详情格式异常：%s", response.url)
            return
        notice_id = str(detail.get("id") or list_record.get("id") or "")
        context = {
            "feed": feed,
            "detail": dict(detail),
            "list_record": dict(list_record),
            "list_fingerprint": list_fingerprint,
            "response_metadata": self.build_response_metadata(
                response,
                request_kind="detail_api",
                context={"feed": feed, "noticeId": notice_id},
            ),
        }
        pdf = next(
            (
                x["file_url"]
                for x in QianjiParser.attachments(detail)
                if x.get("file_type") == "application/pdf"
            ),
            "",
        )
        if self.parse_pdf and pdf:
            yield Request(pdf, headers=self._headers("application/pdf,*/*"), callback=self.parse_pdf_detail, errback=self.pdf_failed, cb_kwargs={"context": context}, meta={"qianji_context": context}, dont_filter=True)
        else:
            yield self._build_item(context)

    def parse_pdf_detail(self, response: Response, context: Mapping[str, Any]):
        text = ""
        try:
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.body)).pages).strip()
            if not text:
                self.crawler.stats.inc_value("qianji/pdf_without_text_layer")
        except Exception as exc:
            self.logger.warning("PDF提取失败，使用HTML正文：%s", exc)
            self.crawler.stats.inc_value("qianji/pdf_extract_failed")
        enriched = dict(context)
        response_metadata = dict(context.get("response_metadata") or {})
        response_metadata["pdfTextSource"] = self.build_response_metadata(
            response,
            request_kind="attachment_pdf_text",
            context={"feed": context.get("feed", "")},
        )
        enriched["response_metadata"] = response_metadata
        yield self._build_item(enriched, pdf_text=text)

    def pdf_failed(self, failure):
        self.logger.warning("PDF下载失败，使用HTML正文：%s", failure.request.url)
        yield self._build_item(failure.request.meta["qianji_context"])

    def _build_item(self, context: Mapping[str, Any], *, pdf_text=""):
        feed, detail = str(context["feed"]), context["detail"]
        notice_type, data, attachments, raw_html, raw_text = QianjiParser.parse(feed, detail, pdf_text=pdf_text)
        notice_id = str(detail.get("id") or context["list_record"].get("id") or "")
        api_trusted_fields = ["发布日期"]
        if detail.get("projectCode"):
            api_trusted_fields.append("项目编号")
        if detail.get("bidSituation"):
            api_trusted_fields.append("项目性质")
        if detail.get("bidTypeName") and notice_type in {"招标计划", "中标结果公示"}:
            api_trusted_fields.append("招标方式")
        if detail.get("zbUnitName"):
            api_trusted_fields.append(
                "招标人名称"
                if notice_type == "招标计划"
                else (
                    "招标人/采购人名称"
                    if notice_type == "招标公告"
                    else "招标人/采购人"
                )
            )
        if detail.get("dlUnitName") and notice_type != "招标计划":
            api_trusted_fields.append("招标代理机构")
        return self.build_notice_item(
            notice_type=notice_type, notice_subtype=feed, notice_id=notice_id,
            title=str(detail.get("title") or ""), publish_time=detail.get("noticeStartTime") or detail.get("createTime"),
            detail_url=config.detail_page_url(notice_id), data=data,
            raw_data={"list": context["list_record"], "detail": detail}, raw_html=raw_html,
            raw_text=raw_text, extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version, source_list_fingerprint=str(context.get("list_fingerprint") or ""),
            field_meta={
                "site_parser": self.extraction_model_name,
                "feed": feed,
                "project_type": config.FEEDS[feed][1],
                "qianjiIdentifierExtraction": QianjiParser.identifier_source_metadata(
                    detail, raw_text
                ),
                "qianjiApiTrustedFields": list(dict.fromkeys(api_trusted_fields)),
            },
            response_metadata=dict(context.get("response_metadata") or {}),
            attachments=attachments,
        )
