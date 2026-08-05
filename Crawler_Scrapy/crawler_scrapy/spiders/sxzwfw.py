"""山西省公共资源交易平台工程建设、政府采购公告 Spider。

示例：
    scrapy crawl sxzwfw -a days=1
    scrapy crawl sxzwfw -a start_date=2026-01-16 -a end_date=2026-07-16
    scrapy crawl sxzwfw -a sections=zbgg_zys,hxr,gs
    scrapy crawl sxzwfw -a sections=zc_gz,zc_jg

列表和详情均为服务端渲染 HTML，不需要登录 Token，也不默认启用浏览器渲染。
"""

from __future__ import annotations

import hashlib
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.sxzwfw import config
from crawler_scrapy.sites.sxzwfw.government_parser import (
    SxzwfwGovernmentProcurementParser,
)
from crawler_scrapy.sites.sxzwfw.parser import (
    SxzwfwParser,
    pages_for_total,
    visible_content_text,
)
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class SxzwfwSpider(BaseNoticeSpider):
    """按精确日期窗口动态翻页采集山西工程建设和已接入的政府采购公告。"""

    name = "sxzwfw"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["prec.sxzwfw.gov.cn"]
    parser_version = SxzwfwParser.parser_version
    extraction_model_name = "sxzwfw-rule-parser"
    section_fallback_types = {
        "zbjh": ("招标计划", "zbjh"),
        "zbgg_zys": ("招标公告", "zbgg"),
        "bg": ("招标公告", "zbgg"),
        "hxr": ("中标候选人公示", "hxr"),
        "gs": ("中标结果公示", "zbjg"),
        "qt": ("招标公告", "zbgg"),
        "zc_gz": ("更正结果公示", "gzjg"),
        "zc_jg": ("中标结果公示", "zbjg"),
    }

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": True,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
        "NOTICE_AI_INCLUDE_OPTIONAL_FIELDS": True,
        # 默认出口仍是固定认证代理；天启配置保留为手动备用。
        "TIANQI_PROXY_ENABLED": True,
        "TIANQI_PROXY_REQUIRED": True,
        "DOWNLOADER_MIDDLEWARES": {
            "crawler_scrapy.transport.proxy_middleware.TianqiProxyMiddleware": 610,
        },
    }

    ai_extract_fields = {
        "招标计划": (
            "项目类型", "项目总投资", "招标内容", "招标人名称",
            "行政监督部门", "建设地点", "建设内容及规模",
            "招标公告（资格预审公告）预计发布时间",
        ),
        "资格预审公告": (
            "项目编号/招标编号", "项目总投资/估算金额", "招标金额",
            "资金来源", "项目地点", "招标人/采购人名称",
            "项目概况与招标范围", "申请人资格要求/投标人资格要求",
            "预审文件获取时间", "获取方式", "递交截止时间", "递交方法",
            "开启时间", "开启方式", "开启地点", "评审办法", "投标保证金方式",
            "招标人地址", "招标人联系人", "招标人联系方式",
            "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
            "招标代理机构联系方式",
        ),
        "招标公告": (
            "项目编号/招标编号", "项目总投资/估算金额", "招标金额",
            "资金来源", "项目地点", "招标人/采购人名称", "项目规模",
            "工期/服务期/供货日期", "质量要求", "招标内容与范围",
            "申请人资格要求/投标人资格要求", "预审文件获取时间", "获取方式",
            "递交截止时间", "递交方法", "开启时间", "开启方式", "开启地点",
            "评审办法", "投标保证金方式", "招标人地址", "招标人联系人",
            "招标人联系方式", "招标代理机构", "招标代理机构地址",
            "招标代理机构联系人", "招标代理机构联系方式",
        ),
        "中标候选人公示": (
            "开标时间", "公示时间", "招标编号/项目编号",
            "中标候选人名称", "中标候选人报价", "中标候选人明细",
            "招标人/采购人", "招标人地址", "招标人联系人", "招标人联系方式",
            "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
            "招标代理机构联系方式",
        ),
        "定标候选人公示": (
            "开标时间", "公示时间", "招标编号/项目编号",
            "定标候选人名称", "定标候选人报价", "招标人/采购人",
            "招标人地址", "招标人联系人", "招标人联系方式",
            "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
            "招标代理机构联系方式",
        ),
        "中标结果公示": (
            "中标人名称", "中标价", "中标结果明细", "工期", "项目经理",
            "项目经理证书名称", "项目经理证书编号", "招标人/采购人",
            "招标人地址", "招标人联系人", "招标人联系方式",
            "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
            "招标代理机构联系方式", "依据文件", "依据文号",
        ),
        "更正结果公示": (
            "公告内容", "开标时间", "标书发售时间", "招标人地址",
            "招标人联系人", "招标人联系方式", "招标代理机构",
            "招标代理机构地址", "招标代理机构联系人", "招标代理机构联系方式",
            "监督部门地址", "监督部门联系人", "监督部门联系方式",
            "依据文件", "依据文号",
        ),
        "合同与履约": (
            "项目编号", "合同名称", "招标人名称", "中标人名称",
            "合同金额", "合同期限", "合同签署时间", "合同主要内容",
        ),
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        """替换站点导出器，并选择受保护直连、固定代理或天启代理出口。"""

        super().update_settings(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines[
            "crawler_scrapy.sites.sxzwfw.exporter.SxzwfwMultiFormatPipeline"
        ] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

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
        middlewares[
            "crawler_scrapy.transport.proxy_middleware.StaticProxyMiddleware"
        ] = 610 if mode == "static" else None
        middlewares[
            "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
        ] = 650
        settings.set("DOWNLOADER_MIDDLEWARES", middlewares, priority="spider")
        settings.set("TIANQI_PROXY_ENABLED", False, priority="spider")
        settings.set("STATIC_PROXY_ENABLED", mode == "static", priority="spider")
        # direct 明确关闭系统代理继承，保证测试确实从服务器本机公网出口发出。
        settings.set("HTTPPROXY_ENABLED", mode == "static", priority="spider")
        settings.set(
            "CONCURRENT_REQUESTS",
            settings.getint("DIRECT_CONCURRENT_REQUESTS", 4),
            priority="spider",
        )
        settings.set(
            "CONCURRENT_REQUESTS_PER_DOMAIN",
            settings.getint("DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN", 2),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_DELAY",
            settings.getfloat("DIRECT_DOWNLOAD_DELAY", 2.5),
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
            settings.getfloat("DIRECT_AUTOTHROTTLE_TARGET_CONCURRENCY", 0.75),
            priority="spider",
        )
        settings.set(
            "RETRY_TIMES",
            settings.getint("DIRECT_RETRY_TIMES", 2),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_TIMEOUT",
            settings.getint("DIRECT_DOWNLOAD_TIMEOUT", 180),
            priority="spider",
        )
        settings.set(
            "CLOSESPIDER_PAGECOUNT",
            settings.getint("DIRECT_MAX_RESPONSES_PER_RUN", 1000000),
            priority="spider",
        )

    def __init__(
        self,
        sections: str | None = None,
        days: int | str | None = 1,
        start_date: str | None = None,
        end_date: str | None = None,
        max_pages: int | str = 10000,
        max_records: int | str = 1000000,
        origin: str = "",
        project_type: str = "",
        title: str = "",
        split_months: str | bool = "true",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.sections = self._parse_sections(sections)
        self.max_pages = self._positive_int(max_pages, 10000)
        self.max_records = self._positive_int(max_records, 1000000)
        self.origin = str(origin or "").strip()
        self.project_type = str(project_type or "").strip()
        self.search_title = str(title or "").strip()
        self.split_months = str(split_months).strip().lower() not in {
            "0", "false", "no", "off",
        }
        self.window_start, self.window_end = self._parse_window(
            days, start_date, end_date
        )
        self.query_windows = self._split_windows(
            self.window_start,
            self.window_end,
        )
        self._seen_ids: set[str] = set()
        self._scheduled_counts = {section: 0 for section in self.sections}

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    @classmethod
    def _parse_sections(cls, value: str | None) -> tuple[str, ...]:
        if not value:
            return config.DEFAULT_SECTIONS
        requested = tuple(
            dict.fromkeys(part.strip() for part in value.split(",") if part.strip())
        )
        unknown = [section for section in requested if section not in config.SECTION_CHANNELS]
        if unknown:
            raise ValueError(
                f"未知山西栏目：{', '.join(unknown)}；可选值："
                f"{', '.join(config.SECTION_CHANNELS)}"
            )
        return requested

    @staticmethod
    def _date_value(value: str | None, *, fallback: date | None = None) -> date | None:
        raw = str(value or "").strip()
        if not raw:
            return fallback
        parsed = coerce_datetime(raw)
        if parsed is None:
            raise ValueError(
                f"无法解析日期 {raw!r}；请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS"
            )
        return parsed.date()

    @classmethod
    def _parse_window(
        cls,
        days: int | str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[date, date]:
        end = cls._date_value(end_date, fallback=datetime.now().date())
        start = cls._date_value(start_date)
        if start is None:
            try:
                count = int(days or 1)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"days 必须是正整数，实际为 {days!r}") from exc
            if count <= 0:
                raise ValueError("days 必须大于0")
            start = end - timedelta(days=count - 1)
        if start > end:
            raise ValueError("start_date 不能晚于 end_date")
        return start, end

    def _split_windows(self, start: date, end: date) -> tuple[tuple[date, date], ...]:
        if not self.split_months:
            return ((start, end),)
        result: list[tuple[date, date]] = []
        cursor = start
        while cursor <= end:
            month_end = date(
                cursor.year,
                cursor.month,
                monthrange(cursor.year, cursor.month)[1],
            )
            current_end = min(month_end, end)
            result.append((cursor, current_end))
            cursor = current_end + timedelta(days=1)
        # 新时间窗优先：测试限制 max_records 时优先得到最新公告；完整历史采集时
        # 仍会覆盖全部月份，不影响完整性。
        return tuple(reversed(result))

    @property
    def list_headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": config.WEB_BASE_URL,
            "Referer": config.INDEX_URL,
        }

    async def start(self) -> Iterable[Request]:
        self.logger.info(
            "山西历史采集：start=%s end=%s windows=%s sections=%s",
            self.window_start,
            self.window_end,
            len(self.query_windows),
            ",".join(self.sections),
        )
        for section in self.sections:
            for window_start, window_end in self.query_windows:
                yield self._list_request(section, 1, window_start, window_end)

    def _list_request(
        self,
        section: str,
        page: int,
        window_start: date,
        window_end: date,
    ) -> Request:
        form = config.build_list_form(
            section,
            start_date=window_start.isoformat(),
            end_date=window_end.isoformat(),
            title=self.search_title,
            origin=self.origin,
            project_type=self.project_type,
        )
        return Request(
            url=config.build_list_url(page),
            method="POST",
            body=urlencode(form).encode("utf-8"),
            headers=self.list_headers,
            callback=self.parse_list,
            errback=self.on_request_error,
            cb_kwargs={
                "section": section,
                "page": page,
                "window_start": window_start,
                "window_end": window_end,
            },
            dont_filter=True,
        )

    def parse_list(
        self,
        response: Response,
        section: str,
        page: int,
        window_start: date,
        window_end: date,
    ):
        records = SxzwfwParser.parse_list_records(response.body)
        total = SxzwfwParser.list_total(response.body)
        total_pages = pages_for_total(total)
        list_trace = {
            "responseMetadata": self.build_response_metadata(
                response,
                request_kind="list_page",
                context={
                    "section": section,
                    "page": page,
                    "windowStart": window_start.isoformat(),
                    "windowEnd": window_end.isoformat(),
                },
            ),
            "requestForm": config.build_list_form(
                section,
                start_date=window_start.isoformat(),
                end_date=window_end.isoformat(),
                title=self.search_title,
                origin=self.origin,
                project_type=self.project_type,
            ),
            "pagination": {
                "page": page,
                "pageSize": config.PAGE_SIZE,
                "total": total,
                "pages": total_pages,
                "recordCount": len(records),
            },
            # 当前列表记录会单独进入 raw_data.list；这里只保存整页校验信息，
            # 避免每条公告重复保存同一份列表 HTML。
            "content": {
                "bodyBytes": len(response.body),
                "bodySha256": hashlib.sha256(response.body).hexdigest(),
            },
        }
        if not records:
            self.logger.info(
                "[%s %s~%s] 列表结束：reason=source_exhausted page=%s",
                section,
                window_start,
                window_end,
                page,
            )
            return

        all_before_window = True
        for record in records:
            published = coerce_datetime(record.get("publish_time"))
            if published is None or published.date() >= window_start:
                all_before_window = False
            if published is not None and not (
                window_start <= published.date() <= window_end
            ):
                self.crawler.stats.inc_value("sxzwfw/list_outside_window")
                continue
            if self._scheduled_counts[section] >= self.max_records:
                break
            notice_id = str(record.get("notice_id") or "").strip()
            detail_url = str(record.get("detail_url") or "").strip()
            identity_key = notice_id or detail_url
            if not identity_key or identity_key in self._seen_ids:
                continue
            should_fetch, list_fingerprint = self.check_notice_candidate(
                notice_id=notice_id,
                list_record=record,
                detail_url=detail_url,
                notice_type=section,
                title=str(record.get("title") or ""),
                publish_time=published or record.get("publish_time"),
            )
            if not should_fetch:
                continue
            self._seen_ids.add(identity_key)
            self._scheduled_counts[section] += 1
            record_with_meta = dict(record)
            record_with_meta["_crawler_list_fingerprint"] = list_fingerprint
            record_with_meta["_crawler_list_trace"] = list_trace
            yield Request(
                detail_url,
                headers={"Referer": response.url},
                callback=self.parse_detail,
                errback=self.on_request_error,
                cb_kwargs={
                    "section": section,
                    "notice_id": notice_id,
                    "list_record": record_with_meta,
                },
                # 跨运行事实来源是已成功导出的公告索引。不要让 JOBDIR 把已发出
                # 但尚未来得及导出的详情永久标记为完成；中断后必须可以重取。
                dont_filter=True,
            )

        has_next = (
            page < self.max_pages
            and self._scheduled_counts[section] < self.max_records
            and not all_before_window
            and (
                (total_pages and page < total_pages)
                or (not total_pages and len(records) >= config.PAGE_SIZE)
            )
        )
        if has_next:
            yield self._list_request(section, page + 1, window_start, window_end)
        else:
            if all_before_window:
                reason = "time_boundary_reached"
            elif self._scheduled_counts[section] >= self.max_records:
                reason = "max_records"
            elif page >= self.max_pages:
                reason = "max_pages"
            else:
                reason = "source_exhausted"
            self.logger.info(
                "[%s %s~%s] 列表结束：reason=%s page=%s total=%s pages=%s",
                section,
                window_start,
                window_end,
                reason,
                page,
                total or "unknown",
                total_pages or "unknown",
            )

    def parse_detail(
        self,
        response: Response,
        section: str,
        notice_id: str,
        list_record: Mapping[str, Any],
    ):
        parser_class = (
            SxzwfwGovernmentProcurementParser
            if section in config.GOVERNMENT_SECTION_CHANNELS
            else SxzwfwParser
        )
        source_list = dict(list_record)
        list_fingerprint = str(
            source_list.pop("_crawler_list_fingerprint", "") or ""
        )
        list_trace = source_list.pop("_crawler_list_trace", None)
        response_metadata = self.build_response_metadata(
            response,
            request_kind="detail_page",
            context={"section": section},
        )
        if isinstance(list_trace, Mapping):
            related = list_trace.get("responseMetadata")
            if isinstance(related, Mapping):
                response_metadata["relatedRequests"] = {"list": dict(related)}
        try:
            parsed = parser_class.parse(
                section,
                response.body,
                source_list,
                response.url,
            )
        except Exception as exc:
            self.logger.error(
                "详情解析失败：id=%s url=%s error=%s: %s",
                notice_id,
                response.url,
                type(exc).__name__,
                exc,
            )
            crawler = getattr(self, "crawler", None)
            if crawler is not None:
                crawler.stats.inc_value("sxzwfw/detail_parse_errors")
            fallback_type, fallback_subtype = self.section_fallback_types[section]
            source_section_label = config.SECTION_CHANNELS[section][1]
            exported_subtype = (
                f"engineering.{section}.{fallback_subtype}"
                if section in config.ENGINEERING_SECTION_CHANNELS
                else fallback_subtype
            )
            yield self.build_notice_item(
                notice_type=fallback_type,
                notice_subtype=exported_subtype,
                notice_id=notice_id,
                title=str(source_list.get("title") or ""),
                publish_time=source_list.get("publish_time"),
                detail_url=response.url,
                data={"发布网站": config.PLATFORM_NAME},
                raw_data={
                    "list": source_list,
                    "detail": {
                        "parseError": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                        "parserVersion": parser_class.parser_version,
                    },
                    "transport": {
                        "list": dict(list_trace)
                        if isinstance(list_trace, Mapping)
                        else None,
                    },
                },
                raw_html=response.body,
                raw_text=visible_content_text(response.body),
                parse_status="FAILED",
                extraction_model=getattr(
                    parser_class,
                    "extraction_model_name",
                    self.extraction_model_name,
                ),
                extraction_version=parser_class.parser_version,
                field_meta={
                    "site_parser": parser_class.parser_version,
                    "source_section": section,
                    "source_section_label": source_section_label,
                    "source_channel_id": config.SECTION_CHANNELS[section][0],
                    "source_notice_type": source_section_label,
                    "schema_notice_type": fallback_type,
                    "schema_notice_subtype": fallback_subtype,
                    "source_location": source_list.get("location") or "",
                    "parse_error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                },
                response_metadata=response_metadata,
                source_list_fingerprint=list_fingerprint,
                attachments=[],
            )
            return

        engineering = section in config.ENGINEERING_SECTION_CHANNELS
        source_section_label = config.SECTION_CHANNELS[section][1]
        notice_subtype = (
            f"engineering.{section}.{parsed.subtype}"
            if engineering
            else parsed.subtype
        )
        item = self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_id=notice_id,
            title=parsed.title,
            publish_time=parsed.publish_time,
            detail_url=response.url,
            data=parsed.data,
            notice_subtype=notice_subtype,
            raw_data={
                "list": source_list,
                "detail": {
                    "title": parsed.title,
                    "publishTime": parsed.publish_time,
                    "sourceSection": section,
                    "sourceSectionLabel": source_section_label,
                    "sourceNature": parsed.source_nature,
                    "schemaNoticeType": parsed.notice_type,
                    "schemaNoticeSubtype": parsed.subtype,
                    "parserVersion": parser_class.parser_version,
                },
                "transport": {
                    "list": dict(list_trace) if isinstance(list_trace, Mapping) else None,
                },
            },
            raw_html=response.body,
            raw_text=parsed.raw_text,
            parse_status="PARSED" if parsed.raw_text else "PARTIAL",
            extraction_model=getattr(
                parser_class,
                "extraction_model_name",
                self.extraction_model_name,
            ),
            extraction_version=parser_class.parser_version,
            field_meta={
                "site_parser": parser_class.parser_version,
                "extraction_model": getattr(
                    parser_class,
                    "extraction_model_name",
                    self.extraction_model_name,
                ),
                "source_section": section,
                "source_section_label": source_section_label,
                "source_channel_id": config.SECTION_CHANNELS[section][0],
                "source_notice_type": source_section_label,
                "source_nature": parsed.source_nature,
                "source_location": source_list.get("location") or "",
                "schema_notice_type": parsed.notice_type,
                "schema_notice_subtype": parsed.subtype,
            },
            response_metadata=response_metadata,
            source_list_fingerprint=list_fingerprint,
            attachments=parsed.attachments,
        )
        crawler = getattr(self, "crawler", None)
        if crawler is not None:
            crawler.stats.inc_value(f"sxzwfw/items_built/{section}")
        if not parsed.cms_attachment:
            yield item
            return

        cms = parsed.cms_attachment
        base = str(cms.get("base") or config.WEB_BASE_URL).rstrip("/")
        meta_url = f"{base}/attachment_url.jspx?{urlencode({'cid': cms['content_id'], 'n': cms['count']})}"
        yield Request(
            meta_url,
            headers={"Referer": response.url, "Accept": "application/json,*/*"},
            callback=self.parse_attachment_metadata,
            errback=self.on_attachment_metadata_error,
            cb_kwargs={"item": item, "cms": cms},
            dont_filter=True,
        )

    def parse_attachment_metadata(self, response: Response, item, cms: Mapping[str, Any]):
        try:
            values = response.json()
        except (ValueError, TypeError):
            values = []
        if not isinstance(values, list):
            values = []
        attachments = [dict(value) for value in item.get("attachments") or []]
        base = str(cms.get("base") or config.WEB_BASE_URL).rstrip("/")
        content_id = str(cms.get("content_id") or "")
        count = int(cms.get("count") or 0)
        by_id = {
            str(value.get("source_file_id")): value for value in attachments
        }
        for index in range(count):
            source_id = f"{content_id}_{index}"
            attachment = by_id.get(source_id)
            if attachment is None:
                continue
            if index >= len(values):
                attachment["parse_status"] = "METADATA_FAILED"
                continue
            suffix = str(values[index] or "")
            url = (
                f"{base}/attachment.jspx?cid={content_id}&i={index}{suffix}"
            )
            attachment["file_url"] = url
            attachment["parse_status"] = "URL_RESOLVED"
        item["attachments"] = attachments
        item["file_urls"] = [
            str(value.get("file_url"))
            for value in attachments
            if value.get("file_url")
        ]
        metadata_trace = self.build_response_metadata(
            response,
            request_kind="attachment_metadata",
            context={
                "contentId": str(cms.get("content_id") or ""),
                "expectedCount": int(cms.get("count") or 0),
                "resolvedCount": sum(
                    1 for value in attachments if value.get("file_url")
                ),
            },
        )
        response_metadata = dict(item.get("response_metadata") or {})
        related = dict(response_metadata.get("relatedRequests") or {})
        related["attachmentMetadata"] = metadata_trace
        response_metadata["relatedRequests"] = related
        item["response_metadata"] = response_metadata
        raw_data = dict(item.get("raw_data") or {})
        transport = dict(raw_data.get("transport") or {})
        transport["attachmentMetadata"] = metadata_trace
        raw_data["transport"] = transport
        item["raw_data"] = raw_data
        return item

    def on_attachment_metadata_error(self, failure):
        item = failure.request.cb_kwargs.get("item")
        if item is None:
            return None
        attachments = [dict(value) for value in item.get("attachments") or []]
        for attachment in attachments:
            if attachment.get("parse_status") == "PENDING":
                attachment["parse_status"] = "METADATA_FAILED"
        item["attachments"] = attachments
        error_trace = {
            "requestKind": "attachment_metadata",
            "request": {
                "url": self._sanitize_trace_url(failure.request.url),
                "method": str(failure.request.method or "GET"),
            },
            "error": {
                "type": type(failure.value).__name__,
                "message": failure.getErrorMessage(),
            },
        }
        response_metadata = dict(item.get("response_metadata") or {})
        related = dict(response_metadata.get("relatedRequests") or {})
        related["attachmentMetadata"] = error_trace
        response_metadata["relatedRequests"] = related
        item["response_metadata"] = response_metadata
        raw_data = dict(item.get("raw_data") or {})
        transport = dict(raw_data.get("transport") or {})
        transport["attachmentMetadata"] = error_trace
        raw_data["transport"] = transport
        item["raw_data"] = raw_data
        self.logger.warning(
            "CMS附件元数据请求失败，保留公告和附件占位信息：%s",
            failure.getErrorMessage(),
        )
        return item

    def on_request_error(self, failure) -> None:
        request = failure.request
        self.logger.error(
            "山西请求失败：url=%s error=%s",
            request.url,
            failure.getErrorMessage(),
        )
