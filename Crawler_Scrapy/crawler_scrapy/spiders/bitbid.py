"""比比网“招标信息”四个栏目公告爬虫。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from io import BytesIO
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.ai.glm52_profile import (
    GLM52_HYBRID_SETTINGS,
    install_hybrid_pipeline,
)
from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.bitbid import config
from crawler_scrapy.sites.bitbid.parser import BitbidParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class BitbidSpider(BaseNoticeSpider):
    name = "bitbid"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.bitbid.cn", "zb.bitbid.cn"]
    parser_version = BitbidParser.parser_version
    extraction_model_name = "bitbid-site-rule-parser"
    ai_metadata_key = "bitbidHybridAi"
    ai_trusted_fields_meta_key = "bitbidApiTrustedFields"
    ai_log_name = "比比网"

    # 比比网的标题、发布日期和部分项目元数据来自 API；模型只处理正文/PDF
    # 规则出现明确异常的字段。长章节越界、HTML 残留、缺失标签值和结果列表
    # 错位均由通用 Pipeline 自动触发，不对正常字段增加调用成本。
    ai_extract_fields = {
        "招标计划": (),
        "资格预审公告": (),
        "招标公告": (),
        "中标候选人公示": (),
        "定标候选人公示": (),
        "中标结果公示": (),
        "更正结果公示": (),
    }
    ai_sparse_review_fields = {}
    ai_candidate_fields = {
        "招标计划": (
            "项目名称", "项目编号", "招标编号", "项目总投资", "招标内容",
            "建设地点", "建设内容及规模", "招标人名称",
        ),
        "招标公告": (
            "项目名称", "项目编号", "招标编号", "项目总投资/估算金额",
            "招标金额", "资金来源", "项目地点", "项目规模", "质量要求",
            "招标内容与范围", "申请人资格要求/投标人资格要求", "获取方式",
            "递交方法", "开启地点", "投标保证金方式",
        ),
        "资格预审公告": (
            "项目名称", "项目编号", "招标编号", "项目总投资/估算金额",
            "招标金额", "资金来源", "项目地点", "项目概况与招标范围",
            "申请人资格要求/投标人资格要求", "获取方式", "递交方法",
            "开启地点", "投标保证金方式",
        ),
        "中标候选人公示": (
            "项目名称", "项目编号", "招标编号", "公示时间",
            "中标候选人名称", "中标候选人报价",
        ),
        "定标候选人公示": (
            "项目名称", "项目编号", "招标编号", "公示时间",
            "定标候选人名称", "定标候选人报价", "定标候选人项目经理",
            "定标候选人项目经理相关证书及编号", "定标候选人项目副经理",
            "定标候选人项目副经理相关证书及编号", "定标候选人资信情况",
            "定标候选人业绩情况（名称、日期、金额）",
        ),
        "中标结果公示": (
            "项目名称", "项目编号", "招标编号", "中标人名称", "中标价",
            "联合体成员", "工期", "项目经理", "项目经理证书名称",
            "项目经理证书编号",
        ),
        "更正结果公示": (
            "项目名称", "项目编号", "招标编号", "开标时间",
            "标书发售时间", "公告内容", "招标人地址", "招标人联系人",
            "招标人联系方式", "招标代理机构", "招标代理机构地址",
            "招标代理机构联系人", "招标代理机构联系方式",
        ),
    }

    custom_settings = {
        **GLM52_HYBRID_SETTINGS,
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.6,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": False,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        install_hybrid_pipeline(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines["crawler_scrapy.sites.bitbid.exporter.BitbidMultiFormatPipeline"] = 300
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
        # 直连时禁止继承宿主机 HTTP_PROXY，保证出口策略与日志记录一致。
        settings.set("HTTPPROXY_ENABLED", mode == "static", priority="spider")

    def __init__(
        self,
        categories: str | None = None,
        max_records: int | str = 200,
        page_size: int | str = 20,
        max_pages: int | str = 100,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        keyword: str = "",
        region: str | int = 0,
        parse_pdf: str | bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.categories = self._parse_categories(categories)
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, 20), 100)
        self.max_pages = self._positive_int(max_pages, 100)
        requested_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._parse_time_window(requested_days, start_date, end_date)
        self.keyword = str(keyword or "").strip()
        self.region = str(region or 0)
        self.parse_pdf = str(parse_pdf).strip().lower() not in {"0", "false", "no", "off"}
        self._scheduled_counts = {category: 0 for category in self.categories}
        self._seen: set[tuple[str, str]] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _parse_categories(value: str | None) -> tuple[str, ...]:
        if not value:
            return config.DEFAULT_CATEGORIES
        result = tuple(x.strip() for x in value.split(",") if x.strip())
        invalid = [x for x in result if x not in config.CATEGORIES]
        if invalid:
            raise ValueError(f"不支持的 categories: {','.join(invalid)}")
        return result or config.DEFAULT_CATEGORIES

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
        for category in self.categories:
            yield self._list_request(category, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _headers(self, *, json_api: bool = True) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*" if json_api else "application/pdf,*/*",
            "Referer": f"{config.WEB_BASE_URL}/collection",
        }

    def _list_request(self, category: str, page: int) -> Request:
        url = config.list_url(
            category,
            page,
            self.page_size,
            begin_time=self.window_start.strftime("%Y-%m-%d %H:%M:%S") if self.window_start else "",
            end_time=self.window_end.strftime("%Y-%m-%d %H:%M:%S") if self.window_end else "",
            keyword=self.keyword,
            region=self.region,
        )
        return Request(
            url,
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"category": category, "page": page},
        )

    @staticmethod
    def _record_time(record: Mapping[str, Any]) -> datetime | None:
        for key in ("fabuTime", "gongGaoFaBuTime", "faBuTime", "createTime", "planFabuTime"):
            parsed = coerce_datetime(record.get(key))
            if parsed:
                return parsed
        return None

    def _inside_window(self, record: Mapping[str, Any]) -> bool:
        published = self._record_time(record)
        if not published:
            return True
        return (self.window_start is None or published >= self.window_start) and (
            self.window_end is None or published <= self.window_end
        )

    def parse_list(self, response: Response, category: str, page: int):
        payload = response.json()
        records = payload.get("list") or [] if isinstance(payload, Mapping) else []
        if not isinstance(records, list):
            self.logger.warning("列表返回格式异常: category=%s page=%s", category, page)
            return

        for raw in records:
            if not isinstance(raw, Mapping) or not self._inside_window(raw):
                continue
            notice_id = str(raw.get("id") or "").strip()
            identity = (category, notice_id)
            if not notice_id or identity in self._seen:
                continue
            if self._scheduled_counts[category] >= self.max_records:
                break
            self._seen.add(identity)
            detail_url = config.detail_page_url(category, notice_id)
            source_notice_id = config.source_notice_id(category, notice_id)
            title = str(raw.get("gongGaoMingCheng") or raw.get("title") or raw.get("gongShiBiaoTi") or "")
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=source_notice_id,
                notice_type=config.CATEGORIES[category]["label"],
                list_record=raw,
                detail_url=detail_url,
                title=title,
                publish_time=self._record_time(raw),
            )
            if not should_fetch:
                continue
            self._scheduled_counts[category] += 1
            yield Request(
                config.detail_api_url(category, notice_id),
                headers=self._headers(),
                callback=self.parse_detail,
                cb_kwargs={
                    "category": category,
                    "list_record": dict(raw),
                    "list_fingerprint": fingerprint,
                },
            )

        total = int(payload.get("total") or 0) if isinstance(payload, Mapping) else 0
        if (
            records
            and page * self.page_size < total
            and page < self.max_pages
            and self._scheduled_counts[category] < self.max_records
        ):
            yield self._list_request(category, page + 1)

    def parse_detail(
        self,
        response: Response,
        category: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
    ):
        payload = response.json()
        if not isinstance(payload, Mapping) or int(payload.get("code") or 0) != 200:
            self.logger.warning("详情接口返回异常: %s", response.url)
            return
        detail = BitbidParser._detail_object(category, payload)
        notice_id = str(detail.get("id") or list_record.get("id") or "")
        context = {
            "category": category,
            "payload": dict(payload),
            "list_record": dict(list_record),
            "list_fingerprint": list_fingerprint,
        }
        pdf = config.pdf_url(category, notice_id)
        if self.parse_pdf and pdf:
            yield Request(
                pdf,
                headers=self._headers(json_api=False),
                callback=self.parse_pdf_detail,
                errback=self.pdf_failed,
                cb_kwargs={"context": context},
                meta={"bitbid_context": context},
                dont_filter=True,
            )
            return
        yield self._build_item(context)

    def parse_pdf_detail(self, response: Response, context: Mapping[str, Any]):
        pdf_text = ""
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(response.body))
            pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
            if not pdf_text:
                self.crawler.stats.inc_value("bitbid/pdf_without_text_layer")
        except Exception as exc:
            self.logger.warning("PDF文字提取失败，改用接口HTML: %s: %s", response.url, exc)
            self.crawler.stats.inc_value("bitbid/pdf_extract_failed")
        yield self._build_item(context, pdf_text=pdf_text)

    def pdf_failed(self, failure):
        self.logger.warning("PDF下载失败，改用接口HTML: %s", failure.request.url)
        self.crawler.stats.inc_value("bitbid/pdf_download_failed")
        context = failure.request.meta.get("bitbid_context") or {}
        yield self._build_item(context)

    def _build_item(self, context: Mapping[str, Any], *, pdf_text: str = ""):
        category = str(context["category"])
        payload = context["payload"]
        list_record = context["list_record"]
        detail = BitbidParser._detail_object(category, payload)
        notice_type, data, attachments, raw_html, raw_text = BitbidParser.parse(
            category, payload, pdf_text=pdf_text
        )
        notice_id = str(detail.get("id") or list_record.get("id") or "")
        source_notice_id = config.source_notice_id(category, notice_id)
        title = str(
            detail.get("gongGaoMingCheng")
            or detail.get("gongShiBiaoTi")
            or detail.get("title")
            or detail.get("name")
            or list_record.get("gongGaoMingCheng")
            or list_record.get("title")
            or ""
        )
        publish_time = (
            detail.get("gongGaoFaBuTime")
            or detail.get("faBuTime")
            or detail.get("fabuTime")
            or self._record_time(list_record)
        )
        source_notice_type = (
            "资格预审公告"
            if notice_type == "资格预审公告"
            else str(data.get("公共类型") or "更正结果公示")
            if notice_type == "更正结果公示"
            else notice_type
            if category == "tender"
            and notice_type in {
                "中标候选人公示", "定标候选人公示", "中标结果公示"
            }
            else config.CATEGORIES[category]["label"]
        )
        return self.build_notice_item(
            notice_type=notice_type,
            notice_subtype=category,
            notice_id=source_notice_id,
            title=title,
            publish_time=publish_time,
            detail_url=config.detail_page_url(category, notice_id),
            data=data,
            raw_data={"list": dict(list_record), "detail": dict(payload)},
            raw_html=raw_html,
            raw_text=raw_text,
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=str(context.get("list_fingerprint") or ""),
            field_meta={
                "site_parser": self.parser_version,
                "category": category,
                "source_notice_type": source_notice_type,
                "source_category_label": config.CATEGORIES[category]["label"],
                "schema_notice_type": notice_type,
                "detail_source": "json_api_with_html_or_pdf_body",
                "bitbidApiTrustedFields": [
                    field
                    for field, present in (
                        ("发布日期", bool(publish_time)),
                    )
                    if present
                ],
            },
            attachments=attachments,
        )
