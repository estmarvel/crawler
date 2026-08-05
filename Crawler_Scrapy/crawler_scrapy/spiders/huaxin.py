"""华新阳光采购平台 Scrapy Spider。

示例：
    scrapy crawl huaxin -a max_records=20
    scrapy crawl huaxin -a sections=zbgg_zys,hxr
    scrapy crawl huaxin -a days=180 -a max_records=100000 -a max_pages=10000

``sections`` 支持：zbgg_zys、hxr、gs、zbjh。``days`` 表示从当前时间向前
采集多少天；也可以显式传 ``start_date/end_date``。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin

import scrapy
from scrapy import Request
from scrapy.http import JsonRequest, Response

from crawler_scrapy.sites.huaxin import config
from crawler_scrapy.sites.huaxin.parser import HuaxinParser
from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class HuaxinSpider(BaseNoticeSpider):
    """采集华新四个一级栏目，并输出框架统一的 NoticeItem。"""

    name = "huaxin"
    site_config = config
    parser_class = HuaxinParser
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.ygcgpt.com"]
    parser_version = "huaxin-v11-trace-and-fulltext"
    extraction_model_name = "huaxin-rule-parser"

    # 结构化 API 已直接返回的字段不再交给 AI；只有规则解析后仍为空、且可能
    # 出现在 annContent/annContent2 正文中的字段才进入公共 AI 接口。
    ai_extract_fields = {
        "招标计划": (),
        "资格预审公告": (
            "项目总投资/估算金额", "招标金额", "资金来源",
            "项目概况与招标范围", "申请人资格要求/投标人资格要求",
            "获取方式", "递交方法", "开启地点", "评审办法",
            "投标保证金方式", "招标人地址", "招标人联系人",
            "招标人联系方式", "招标代理机构地址", "招标代理机构联系人",
            "招标代理机构联系方式",
        ),
        "招标公告": (
            "项目总投资/估算金额", "招标金额", "资金来源", "项目规模",
            "工期/服务期/供货日期", "质量要求", "招标内容与范围",
            "申请人资格要求/投标人资格要求", "获取方式", "递交方法",
            "开启地点", "评审办法", "投标保证金方式", "招标人地址",
            "招标人联系人", "招标人联系方式", "招标代理机构地址",
            "招标代理机构联系人", "招标代理机构联系方式",
        ),
        "中标候选人公示": (
            "开标时间", "中标候选人名称", "中标候选人报价",
            "招标人地址", "招标人联系人", "招标人联系方式",
            "招标代理机构地址", "招标代理机构联系人",
            "招标代理机构联系方式",
        ),
        "中标结果公示": (
            "联合体成员", "工期", "项目经理", "项目经理证书名称",
            "项目经理证书编号", "招标人地址", "招标人联系人",
            "招标人联系方式", "招标代理机构地址", "招标代理机构联系人",
            "招标代理机构联系方式", "依据文件", "依据文号",
        ),
        "更正结果公示": (
            "开标时间", "标书发售时间", "公告内容", "招标人地址",
            "招标人联系人", "招标人联系方式", "招标代理机构地址",
            "招标代理机构联系人", "招标代理机构联系方式",
            "监督部门地址", "监督部门联系人", "监督部门联系方式",
            "依据文件", "依据文号",
        ),
    }

    custom_settings = {
        # API 位于独立端口，目标站 robots 文件不能表达该 JSON 接口的规则；
        # 只对本 Spider 关闭，其他站点继续使用全局配置。
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "DOWNLOAD_DELAY": 0.3,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
        # 华新详情由结构化 API 返回；不再保存 HTML 快照。招标计划本身没有
        # raw_html，关闭后也不会产生误导性的快照缺失警告。
        "NOTICE_SNAPSHOT_ENABLED": False,
        "NOTICE_SNAPSHOT_REQUIRED": False,
        # AI 开启时允许它补充华新映射中明确列出的可选业务字段。
        "NOTICE_AI_INCLUDE_OPTIONAL_FIELDS": True,
        "NOTICE_RESOLVE_ATTACHMENT_URLS": True,
        # 只下载源附件并回写预设元数据字段；不执行 OCR/AI 文档解析。
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
        # 天启备用模式的原有配置；update_settings 会按所选出口模式覆盖它。
        "TIANQI_PROXY_ENABLED": True,
        "TIANQI_PROXY_REQUIRED": True,
        "DOWNLOADER_MIDDLEWARES": {
            "crawler_scrapy.transport.proxy_middleware.TianqiProxyMiddleware": 610,
        },
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        """选择受保护直连、固定代理或天启代理出口。"""

        super().update_settings(settings)
        mode = str(settings.get("CRAWLER_OUTBOUND_MODE", "static")).strip().lower()
        if mode == "tianqi":
            return
        if mode not in {"direct", "static"}:
            raise ValueError(
                f"不支持的 CRAWLER_OUTBOUND_MODE={mode!r}；可选 direct/static/tianqi"
            )

        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        middlewares[
            "crawler_scrapy.transport.proxy_middleware.TianqiProxyMiddleware"
        ] = None
        static_middleware = (
            "crawler_scrapy.transport.proxy_middleware.StaticProxyMiddleware"
        )
        middlewares[static_middleware] = 610 if mode == "static" else None
        middlewares[
            "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
        ] = 650
        settings.set("DOWNLOADER_MIDDLEWARES", middlewares, priority="spider")

        settings.set("TIANQI_PROXY_ENABLED", False, priority="spider")
        settings.set("STATIC_PROXY_ENABLED", mode == "static", priority="spider")
        # direct 明确关闭 HttpProxyMiddleware，避免继承宿主机 HTTP_PROXY；static
        # 则由它负责建立带认证的 HTTPS CONNECT。
        settings.set("HTTPPROXY_ENABLED", mode == "static", priority="spider")
        settings.set(
            "CONCURRENT_REQUESTS",
            settings.getint("DIRECT_CONCURRENT_REQUESTS", 2),
            priority="spider",
        )
        settings.set(
            "CONCURRENT_REQUESTS_PER_DOMAIN",
            settings.getint("DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN", 1),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_DELAY",
            settings.getfloat("DIRECT_DOWNLOAD_DELAY", 3.0),
            priority="spider",
        )
        settings.set("RANDOMIZE_DOWNLOAD_DELAY", True, priority="spider")
        settings.set("AUTOTHROTTLE_ENABLED", True, priority="spider")
        settings.set(
            "AUTOTHROTTLE_START_DELAY",
            settings.getfloat("DIRECT_AUTOTHROTTLE_START_DELAY", 3.0),
            priority="spider",
        )
        settings.set(
            "AUTOTHROTTLE_MAX_DELAY",
            settings.getfloat("DIRECT_AUTOTHROTTLE_MAX_DELAY", 60.0),
            priority="spider",
        )
        settings.set(
            "AUTOTHROTTLE_TARGET_CONCURRENCY",
            settings.getfloat("DIRECT_AUTOTHROTTLE_TARGET_CONCURRENCY", 0.5),
            priority="spider",
        )
        settings.set(
            "RETRY_TIMES",
            settings.getint("DIRECT_RETRY_TIMES", 1),
            priority="spider",
        )
        settings.set(
            "CLOSESPIDER_PAGECOUNT",
            settings.getint("DIRECT_MAX_RESPONSES_PER_RUN", 300),
            priority="spider",
        )

    def __init__(
        self,
        sections: str | None = None,
        max_records: int | str = 200,
        page_size: int | str = 50,
        max_pages: int | str = 10,
        days: int | str | None = None,
        lookback_days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.sections = self._parse_sections(sections)
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, 50), 100)
        self.max_pages = self._positive_int(max_pages, 10)
        requested_days = lookback_days if lookback_days not in (None, "") else days
        self.window_start, self.window_end = self._parse_time_window(
            requested_days,
            start_date,
            end_date,
        )

        self._scheduled_counts = {section: 0 for section in self.sections}
        self._scanned_counts = {section: 0 for section in self.sections}
        self._seen_ids: set[tuple[str, str]] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_boundary(value: str | None, *, end_of_day: bool) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = coerce_datetime(raw)
        if parsed is None:
            raise ValueError(
                f"无法解析日期时间 {raw!r}；请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS"
            )
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
        explicit_start = cls._parse_boundary(start_date, end_of_day=False)
        if explicit_start is not None:
            start = explicit_start
        elif days not in (None, ""):
            try:
                count = int(days)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"days/lookback_days 必须是正整数，实际为 {days!r}"
                ) from exc
            if count <= 0:
                raise ValueError("days/lookback_days 必须大于0")
            effective_end = end or datetime.now()
            start = effective_end - timedelta(days=count)
        else:
            start = None
        if start is not None and end is not None and start > end:
            raise ValueError("start_date 不能晚于 end_date")
        return start, end

    @staticmethod
    def _record_publish_time(
        section: str,
        record: Mapping[str, Any],
    ) -> datetime | None:
        keys = (
            ("releaseTime", "noticePlanSendTime", "publishTime", "createTime")
            if section == "zbjh"
            else ("releaseTime", "publishTime", "createTime", "updateTime")
        )
        for key in keys:
            parsed = coerce_datetime(record.get(key))
            if parsed is not None:
                return parsed
        return None

    def _time_window_decision(
        self,
        section: str,
        record: Mapping[str, Any],
    ) -> tuple[bool, datetime | None, str]:
        published = self._record_publish_time(section, record)
        if published is None:
            return True, None, "unknown"
        if self.window_start is not None and published < self.window_start:
            return False, published, "before_start"
        if self.window_end is not None and published > self.window_end:
            return False, published, "after_end"
        return True, published, "inside"

    def _page_reaches_start_boundary(
        self,
        section: str,
        records: list[Any],
    ) -> bool:
        if self.window_start is None:
            return False
        dated = [
            value
            for record in records
            if isinstance(record, Mapping)
            and (value := self._record_publish_time(section, record)) is not None
        ]
        if not dated:
            return False
        descending = all(
            dated[index] >= dated[index + 1]
            for index in range(len(dated) - 1)
        )
        # 正常倒序页只要页尾越过开始时间即可停止；乱序页必须整页都早于开始时间，
        # 防止置顶旧公告造成过早停止。
        return (
            descending and dated[-1] < self.window_start
        ) or all(value < self.window_start for value in dated)

    def _detail_is_in_window(
        self,
        section: str,
        detail: Mapping[str, Any],
        notice_id: str,
    ) -> bool:
        include, published, reason = self._time_window_decision(section, detail)
        if include:
            return True
        crawler = getattr(self, "crawler", None)
        if crawler is not None:
            crawler.stats.inc_value(f"history/detail_skipped/{reason}")
        self.logger.debug(
            "[%s] 详情超出时间范围，跳过：id=%s publish_time=%s reason=%s",
            section,
            notice_id,
            published,
            reason,
        )
        return False

    @classmethod
    def _parse_sections(cls, value: str | None) -> tuple[str, ...]:
        if not value:
            return cls.site_config.DEFAULT_SECTIONS
        requested = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
        unknown = [
            section
            for section in requested
            if section not in cls.site_config.SECTION_CLASSIFICATIONS
        ]
        if unknown:
            valid = ", ".join(cls.site_config.SECTION_CLASSIFICATIONS)
            raise ValueError(
                f"未知{cls.platform_name}栏目：{', '.join(unknown)}；可选值：{valid}"
            )
        return requested

    @property
    def api_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": self.site_config.WEB_BASE_URL,
            "Referer": f"{self.site_config.WEB_BASE_URL}/",
        }
        return headers

    async def start(self) -> Iterable[Request]:
        self.logger.info(
            "采集时间窗口：start=%s end=%s sections=%s page_size=%s",
            self.window_start or "unbounded",
            self.window_end or "now",
            ",".join(self.sections),
            self.page_size,
        )
        for section in self.sections:
            yield self._list_request(section, page=1)

    def _list_request(self, section: str, page: int) -> JsonRequest:
        if section == "zbjh":
            return JsonRequest(
                url=self.site_config.BID_PLAN_LIST_URL,
                method="POST",
                headers=self.api_headers,
                data=self.site_config.build_bid_plan_list_payload(page, self.page_size),
                callback=self.parse_list,
                errback=self.on_list_error,
                cb_kwargs={"section": section, "page": page},
                dont_filter=True,
            )
        return JsonRequest(
            url=self.site_config.ANNOUNCEMENT_LIST_URL,
            method="POST",
            headers=self.api_headers,
            data=self.site_config.build_list_payload(section, page, self.page_size),
            callback=self.parse_list,
            errback=self.on_list_error,
            cb_kwargs={"section": section, "page": page},
            dont_filter=True,
        )

    @staticmethod
    def _json_object(response: Response) -> dict[str, Any]:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError(f"响应JSON不是对象：{type(value).__name__}")
        return value

    def parse_list(self, response: Response, section: str, page: int):
        try:
            payload = self._json_object(response)
        except (ValueError, TypeError) as exc:
            self.logger.error("[%s] 列表第%s页JSON解析失败：%s", section, page, exc)
            return

        if payload.get("code") != 200:
            self.logger.error(
                "[%s] 列表第%s页业务失败：code=%r msg=%r",
                section,
                page,
                payload.get("code"),
                payload.get("msg"),
            )
            return

        data = payload.get("data") or {}
        records = data.get("records") or [] if isinstance(data, Mapping) else []
        if not isinstance(records, list):
            self.logger.error("[%s] 列表 records 类型异常：%s", section, type(records).__name__)
            return

        total = self._safe_int(data.get("total")) if isinstance(data, Mapping) else 0
        pages = self._safe_int(data.get("pages")) if isinstance(data, Mapping) else 0
        request_payload = (
            self.site_config.build_bid_plan_list_payload(page, self.page_size)
            if section == "zbjh"
            else self.site_config.build_list_payload(section, page, self.page_size)
        )
        list_trace = {
            "responseMetadata": self.build_response_metadata(
                response,
                request_kind="list_api",
                context={"section": section, "page": page},
            ),
            "requestPayload": request_payload,
            # records 已逐条保存在 raw_data.list，不在每条公告中重复整页数据。
            "businessEnvelope": {
                "code": payload.get("code"),
                "message": payload.get("msg"),
                "page": page,
                "pageSize": self.page_size,
                "total": total,
                "pages": pages,
            },
        }

        boundary_reached = self._page_reaches_start_boundary(section, records)

        for record in records:
            if self._scheduled_counts[section] >= self.max_records:
                break
            if not isinstance(record, Mapping):
                continue
            self._scanned_counts[section] += 1
            include, record_time, time_reason = self._time_window_decision(
                section,
                record,
            )
            if not include:
                crawler = getattr(self, "crawler", None)
                if crawler is not None:
                    crawler.stats.inc_value(
                        f"history/list_skipped/{time_reason}"
                    )
                continue
            if time_reason == "unknown":
                self.logger.debug(
                    "[%s] 列表记录缺少可解析发布时间，为避免漏数仍请求详情：%r",
                    section,
                    record,
                )
            # 招标计划列表同时包含内部 planId（p...）和数值主键 id，详情路由只接受
            # 数值 id；普通公告则使用 annId。
            notice_id = self._list_record_id(section, record)
            if not notice_id:
                continue
            identity = (section, notice_id)
            if identity in self._seen_ids:
                continue
            should_fetch, list_fingerprint = self.check_notice_candidate(
                notice_id=notice_id,
                list_record=record,
                notice_type=section,
                title=str(record.get("annTitle") or record.get("planTitle") or ""),
                publish_time=record_time
                or record.get("releaseTime")
                or record.get("createTime")
                or "",
            )

            if not should_fetch:
                self.logger.debug(
                    "[%s] 公告列表记录未变化，跳过详情请求：%s",
                    section,
                    notice_id,
                )
                continue
            self._seen_ids.add(identity)
            self._scheduled_counts[section] += 1
            record_with_meta = dict(record)
            record_with_meta["_crawler_list_fingerprint"] = list_fingerprint
            record_with_meta["_crawler_list_trace"] = list_trace
            yield self._detail_request(section, notice_id, record_with_meta)

        should_continue = (
            self._scheduled_counts[section] < self.max_records
            and page < self.max_pages
            and not boundary_reached
            and bool(records)
            and len(records) >= self.page_size
            and (not total or page * self.page_size < total)
            and (not pages or page < pages)
        )
        if should_continue:
            yield self._list_request(section, page + 1)
        else:
            if boundary_reached:
                stop_reason = "time_boundary_reached"
                crawler = getattr(self, "crawler", None)
                if crawler is not None:
                    crawler.stats.inc_value("history/time_boundary_stops")
            elif self._scheduled_counts[section] >= self.max_records:
                stop_reason = "max_records"
            elif page >= self.max_pages:
                stop_reason = "max_pages"
            elif not records:
                stop_reason = "empty_page"
            else:
                stop_reason = "source_exhausted"
            self.logger.info(
                "[%s] 列表结束：reason=%s page=%s scanned=%s scheduled=%s "
                "total=%s pages=%s",
                section,
                stop_reason,
                page,
                self._scanned_counts[section],
                self._scheduled_counts[section],
                total or "unknown",
                pages or "unknown",
            )

    @staticmethod
    def _list_record_id(section: str, record: Mapping[str, Any]) -> str:
        if section == "zbjh":
            value = record.get("id") or record.get("annId") or record.get("planId")
        else:
            value = record.get("annId") or record.get("id") or record.get("planId")
        return str(value or "").strip()

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _detail_request(
        self,
        section: str,
        notice_id: str,
        list_record: dict[str, Any],
    ) -> Request:
        if section == "zbjh":
            url = f"{self.site_config.BID_PLAN_DETAIL_URL}/{notice_id}"
            callback = self.parse_bid_plan_detail
        else:
            url = f"{self.site_config.ANNOUNCEMENT_DETAIL_URL}?annId={notice_id}"
            callback = self.parse_announcement_detail
        return Request(
            url=url,
            headers=self.api_headers,
            callback=callback,
            errback=self.on_detail_error,
            cb_kwargs={
                "section": section,
                "notice_id": notice_id,
                "list_record": list_record,
                "source": "primary",
            },
            dont_filter=True,
        )

    def parse_bid_plan_detail(
        self,
        response: Response,
        section: str,
        notice_id: str,
        list_record: dict[str, Any],
        source: str,
    ):
        detail = self._extract_detail(response, section, notice_id)
        if not detail:
            return
        source_detail = dict(detail)
        detail_envelope = self._business_envelope(response)
        detail["_route_planid"] = notice_id
        detail.setdefault("annId", notice_id)
        self._merge_missing(detail, list_record)
        if not self._detail_is_in_window(section, detail, notice_id):
            return
        item = self._build_item(
            section,
            detail,
            source,
            response=response,
            list_record=list_record,
            source_detail=source_detail,
            detail_envelope=detail_envelope,
        )
        if item is not None:
            yield self._start_attachment_resolution(item)

    def parse_announcement_detail(
        self,
        response: Response,
        section: str,
        notice_id: str,
        list_record: dict[str, Any],
        source: str,
    ):
        detail = self._extract_detail(response, section, notice_id)
        if not detail and source == "primary":
            yield self._backup_detail_request(section, notice_id, list_record)
            return
        if not detail:
            return
        source_detail = dict(detail)
        detail_envelope = self._business_envelope(response)
        self._merge_missing(detail, list_record)
        detail.setdefault("annId", notice_id)
        if not self._detail_is_in_window(section, detail, notice_id):
            return
        item = self._build_item(
            section,
            detail,
            source,
            response=response,
            list_record=list_record,
            source_detail=source_detail,
            detail_envelope=detail_envelope,
        )
        if item is not None:
            yield self._start_attachment_resolution(item)

    def _start_attachment_resolution(self, item):
        attachments = [dict(value) for value in item.get("attachments") or []]
        if (
            not attachments
            or not self.settings.getbool("NOTICE_RESOLVE_ATTACHMENT_URLS", True)
        ):
            return item
        return self._next_attachment_request(item, attachments, 0)

    def _next_attachment_request(self, item, attachments, index: int):
        if index >= len(attachments):
            item["attachments"] = attachments
            item["file_urls"] = [
                str(value.get("file_url"))
                for value in attachments
                if value.get("file_url")
            ]
            return item

        file_id = str(attachments[index].get("source_file_id") or "").strip()
        if not file_id:
            attachments[index]["parse_status"] = "MISSING_SOURCE_FILE_ID"
            return self._next_attachment_request(item, attachments, index + 1)
        return Request(
            url=f"{self.site_config.BIDDING_FILE_QUERY_URL}/{file_id}",
            headers=self.api_headers,
            callback=self.parse_attachment_info,
            errback=self.on_attachment_error,
            cb_kwargs={
                "item": item,
                "attachments": attachments,
                "index": index,
            },
            dont_filter=True,
        )

    def parse_attachment_info(self, response, item, attachments, index: int):
        attachment = attachments[index]
        try:
            payload = self._json_object(response)
            info = payload.get("data") if payload.get("code") == 200 else None
        except (ValueError, TypeError):
            info = None
        if isinstance(info, Mapping):
            # 详情页调用 fileobtain(false, ..., 5)，前端实际打开 data.url；没有
            # 预览 URL 时才回退 downloadUrl。
            raw_url = info.get("url") or info.get("downloadUrl") or ""
            attachment["file_url"] = (
                urljoin(self.site_config.API_ORIGIN + "/", str(raw_url).strip())
                if raw_url
                else None
            )
            if not attachment.get("file_name"):
                attachment["file_name"] = str(
                    info.get("fileName") or info.get("name") or ""
                ).strip() or None
            size = info.get("fileSize", info.get("size"))
            try:
                attachment["file_size_bytes"] = int(size) if size not in (None, "") else None
            except (TypeError, ValueError):
                attachment["file_size_bytes"] = None
            attachment["parse_status"] = (
                "URL_RESOLVED" if attachment.get("file_url") else "METADATA_NO_URL"
            )
        else:
            attachment["parse_status"] = "METADATA_UNAVAILABLE"
        return self._next_attachment_request(item, attachments, index + 1)

    def on_attachment_error(self, failure):
        request = failure.request
        attachments = request.cb_kwargs["attachments"]
        index = request.cb_kwargs["index"]
        attachments[index]["parse_status"] = "METADATA_REQUEST_FAILED"
        self.logger.warning(
            "%s附件元数据请求失败：file_id=%s error=%s",
            self.platform_name,
            attachments[index].get("source_file_id"),
            failure.getErrorMessage(),
        )
        return self._next_attachment_request(
            request.cb_kwargs["item"],
            attachments,
            index + 1,
        )

    def _extract_detail(
        self,
        response: Response,
        section: str,
        notice_id: str,
    ) -> dict[str, Any] | None:
        try:
            payload = self._json_object(response)
        except (ValueError, TypeError) as exc:
            self.logger.warning("[%s] 详情%s JSON解析失败：%s", section, notice_id, exc)
            return None
        detail = payload.get("data") if payload.get("code") == 200 else None
        if not isinstance(detail, dict) or not detail:
            self.logger.debug(
                "[%s] 详情%s无数据：code=%r msg=%r",
                section,
                notice_id,
                payload.get("code"),
                payload.get("msg"),
            )
            return None
        return detail

    def _business_envelope(self, response: Response) -> dict[str, Any]:
        """保留详情接口业务包络，但避免与 raw_data.detail 重复保存 data。"""

        try:
            payload = self._json_object(response)
        except (ValueError, TypeError):
            return {}
        return {
            str(key): value
            for key, value in payload.items()
            if key != "data"
        }

    def _backup_detail_request(
        self,
        section: str,
        notice_id: str,
        list_record: dict[str, Any],
    ) -> Request:
        return Request(
            url=f"{self.site_config.INPUT_ANNOUNCEMENT_DETAIL_URL}?annId={notice_id}",
            headers=self.api_headers,
            callback=self.parse_announcement_detail,
            errback=self.on_detail_error,
            cb_kwargs={
                "section": section,
                "notice_id": notice_id,
                "list_record": list_record,
                "source": "backup",
            },
            dont_filter=True,
        )

    @staticmethod
    def _merge_missing(target: dict[str, Any], fallback: Mapping[str, Any]) -> None:
        for key, value in fallback.items():
            if key not in target or target[key] in (None, "", [], {}):
                target[key] = value

    def _build_item(
        self,
        section: str,
        detail: dict[str, Any],
        source: str,
        *,
        response: Response | None = None,
        list_record: Mapping[str, Any] | None = None,
        source_detail: Mapping[str, Any] | None = None,
        detail_envelope: Mapping[str, Any] | None = None,
    ):
        subtype, notice_type, data, attachments = self.parser_class.parse(
            section, detail
        )
        if not notice_type:
            self.logger.warning(
                "无法识别%s公告类型：section=%s annId=%s title=%r",
                self.platform_name,
                section,
                detail.get("annId"),
                detail.get("annTitle"),
            )
            return None
        detail_url = self.parser_class.detail_url(subtype, detail)
        list_record_value = dict(list_record or {})
        list_trace = list_record_value.get("_crawler_list_trace")
        detail_response_metadata = self.build_response_metadata(
            response,
            request_kind="detail_api",
            context={"section": section, "detailSource": source},
        ) if response is not None else {}
        if isinstance(list_trace, Mapping):
            related = list_trace.get("responseMetadata")
            if isinstance(related, Mapping):
                detail_response_metadata["relatedRequests"] = [dict(related)]
        if detail_envelope:
            detail_response_metadata["businessEnvelope"] = dict(detail_envelope)
        return self.build_notice_item(
            notice_type=notice_type,
            notice_subtype=subtype,
            notice_id=str(detail.get("annId") or detail.get("_route_planid") or ""),
            title=str(detail.get("annTitle") or detail.get("planTitle") or detail.get("projectName") or ""),
            publish_time=str(detail.get("releaseTime") or detail.get("createTime") or ""),
            detail_url=detail_url,
            data=data,
            raw_data={
                "detailSource": source,
                "list": {
                    key: value
                    for key, value in list_record_value.items()
                    if not str(key).startswith("_crawler_")
                },
                "detail": dict(source_detail or detail),
                "transport": {
                    "list": dict(list_trace) if isinstance(list_trace, Mapping) else None,
                    "detailEnvelope": dict(detail_envelope or {}),
                },
            },
            raw_html=self.parser_class.raw_html(detail),
            raw_text=self.parser_class.raw_text(detail),
            parse_status="PARSED",
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            is_verified=False,
            field_meta={"site_parser": self.parser_version, "detail_source": source},
            response_metadata=detail_response_metadata,
            source_list_fingerprint=str(
                detail.get("_crawler_list_fingerprint") or ""
            ),
            attachments=attachments,
        )

    def on_list_error(self, failure):
        request = failure.request
        self.logger.error(
            "%s列表请求失败：section=%s page=%s error=%s",
            self.platform_name,
            request.cb_kwargs.get("section"),
            request.cb_kwargs.get("page"),
            failure.getErrorMessage(),
        )

    def on_detail_error(self, failure):
        request = failure.request
        kwargs = request.cb_kwargs
        section = kwargs.get("section", "")
        notice_id = kwargs.get("notice_id", "")
        source = kwargs.get("source", "primary")
        if section != "zbjh" and source == "primary":
            self.logger.info(
                "%s主详情请求失败，切换备用接口：annId=%s",
                self.platform_name,
                notice_id,
            )
            yield self._backup_detail_request(section, notice_id, kwargs.get("list_record", {}))
            return
        self.logger.warning(
            "%s详情请求失败：section=%s annId=%s source=%s error=%s",
            self.platform_name,
            section,
            notice_id,
            source,
            failure.getErrorMessage(),
        )
