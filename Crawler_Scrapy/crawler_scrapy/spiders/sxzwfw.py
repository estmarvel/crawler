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
import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.ai.glm52_profile import (
    GLM52_HYBRID_SETTINGS,
    install_hybrid_pipeline,
)
from crawler_scrapy.schemas.notice_fields import coerce_datetime, get_notice_type_code
from crawler_scrapy.sites.sxzwfw import config
from crawler_scrapy.sites.sxzwfw.government_parser import (
    SxzwfwGovernmentProcurementParser,
)
from crawler_scrapy.sites.sxzwfw.parser import (
    ParsedNotice,
    SxzwfwParser,
    pages_for_total,
    visible_content_text,
)
from crawler_scrapy.sites.sxjm.download_attachments import attachment_storage_path
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class SxzwfwSpider(BaseNoticeSpider):
    """按精确日期窗口动态翻页采集山西工程建设和已接入的政府采购公告。"""

    name = "sxzwfw"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["prec.sxzwfw.gov.cn"]
    parser_version = SxzwfwParser.parser_version
    extraction_model_name = "sxzwfw-rule-parser"
    ai_metadata_key = "sxzwfwHybridAi"
    ai_trusted_fields_meta_key = "sxzwfwTrustedFields"
    ai_log_name = "山西省公共资源交易平台"
    section_fallback_types = {
        "zbjh": ("招标计划", "zbjh"),
        "zbgg_zys": ("招标公告", "zbgg"),
        "bg": ("更正结果公示", "gzjg"),
        "hxr": ("中标候选人公示", "hxr"),
        "gs": ("中标结果公示", "zbjg"),
        "qt": ("招标公告", "zbgg"),
        "zc_gz": ("更正结果公示", "gzjg"),
        "zc_jg": ("中标结果公示", "zbjg"),
    }

    custom_settings = {
        **GLM52_HYBRID_SETTINGS,
        "ROBOTSTXT_OBEY": False,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": True,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
        "NOTICE_AI_INCLUDE_OPTIONAL_FIELDS": True,
        # 统一入口默认使用受保护直连；以下天启配置仅保留为手动备用，
        # update_settings 会按所选出口模式禁用不需要的代理中间件。
        "TIANQI_PROXY_ENABLED": True,
        "TIANQI_PROXY_REQUIRED": True,
        "DOWNLOADER_MIDDLEWARES": {
            "crawler_scrapy.transport.proxy_middleware.TianqiProxyMiddleware": 610,
        },
    }

    # 旧配置会把 Schema 中几乎全部字段交给“只补空值”模型。现在改为 C
    # 方案：正常规则字段不调用 AI，只有更正正文/合同正文做常规边界复核；
    # 其余字段在缺失且有明确标签、HTML 残留、章节污染或列表错位时升级。
    ai_extract_fields = {
        "招标计划": (),
        "资格预审公告": (),
        "招标公告": (),
        "中标候选人公示": (),
        "定标候选人公示": (),
        "中标结果公示": (),
        "更正结果公示": (),
        "合同与履约": (),
    }
    ai_sparse_review_fields = {}
    ai_candidate_fields = {
        "招标计划": (
            "项目名称", "项目编号", "招标编号", "项目总投资", "招标内容",
            "建设地点", "建设内容及规模", "招标人名称", "行政监督部门",
        ),
        "资格预审公告": (
            "项目名称", "项目编号", "招标编号", "项目总投资/估算金额",
            "招标金额", "资金来源", "项目地点", "项目概况与招标范围",
            "申请人资格要求/投标人资格要求", "获取方式", "递交方法",
            "开启地点", "投标保证金方式",
        ),
        "招标公告": (
            "项目名称", "项目编号", "招标编号", "项目总投资/估算金额",
            "招标金额", "资金来源", "项目地点", "项目规模", "质量要求",
            "招标内容与范围", "申请人资格要求/投标人资格要求", "获取方式",
            "递交方法", "开启地点", "投标保证金方式",
        ),
        "中标候选人公示": (
            "项目名称", "项目编号", "招标编号", "公示时间",
            "中标候选人名称", "中标候选人报价",
        ),
        "定标候选人公示": (
            "项目名称", "项目编号", "招标编号", "公示时间",
            "定标候选人名称", "定标候选人报价",
        ),
        "中标结果公示": (
            "项目名称", "项目编号", "招标编号", "中标人名称", "中标价",
            "联合体成员", "工期", "项目经理", "项目经理证书名称",
            "项目经理证书编号",
        ),
        "更正结果公示": (
            "项目名称", "项目编号", "招标编号", "开标时间", "标书发售时间",
            "公告内容", "招标人地址", "招标人联系人", "招标人联系方式",
            "招标代理机构", "招标代理机构地址", "招标代理机构联系人",
            "招标代理机构联系方式",
        ),
        "合同与履约": (
            "项目名称", "项目编号", "招标编号", "合同名称", "招标人名称",
            "中标人名称", "合同金额", "合同期限", "合同签署时间",
            "合同主要内容",
        ),
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        """替换站点导出器，并选择受保护直连、固定代理或天启代理出口。"""

        super().update_settings(settings)
        install_hybrid_pipeline(settings)
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
        # 每个栏目只预排当前时间窗；当前月不足 max_records 时再由
        # parse_list 递进到下一个月。旧实现一次性排入“月份数×栏目数”请求，
        # 即使测试只需 5 条也会继续访问所有历史月份。
        if not self.query_windows:
            return
        window_start, window_end = self.query_windows[0]
        for section in self.sections:
            yield self._list_request(
                section, 1, window_start, window_end, window_index=0
            )

    def _list_request(
        self,
        section: str,
        page: int,
        window_start: date,
        window_end: date,
        window_index: int = 0,
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
                "window_index": window_index,
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
        window_index: int = 0,
    ) -> list[Request]:
        requests: list[Request] = []
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
            if (
                self._scheduled_counts[section] < self.max_records
                and window_index + 1 < len(self.query_windows)
            ):
                next_start, next_end = self.query_windows[window_index + 1]
                requests.append(
                    self._list_request(
                        section,
                        1,
                        next_start,
                        next_end,
                        window_index=window_index + 1,
                    )
                )
            return requests

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
            requests.append(
                Request(
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
            requests.append(
                self._list_request(
                    section,
                    page + 1,
                    window_start,
                    window_end,
                    window_index=window_index,
                )
            )
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
            if (
                self._scheduled_counts[section] < self.max_records
                and window_index + 1 < len(self.query_windows)
            ):
                next_start, next_end = self.query_windows[window_index + 1]
                requests.append(
                    self._list_request(
                        section,
                        1,
                        next_start,
                        next_end,
                        window_index=window_index + 1,
                    )
                )
        return requests

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

        body_attachment = next(
            (
                value
                for value in parsed.attachments
                if value.get("is_notice_body") and value.get("file_url")
            ),
            None,
        )
        if body_attachment:
            yield Request(
                str(body_attachment["file_url"]),
                headers={"Referer": response.url, "Accept": "application/pdf,*/*"},
                callback=self.parse_embedded_body_pdf,
                errback=self.parse_embedded_body_pdf_error,
                cb_kwargs={
                    "section": section,
                    "notice_id": notice_id,
                    "source_list": source_list,
                    "list_fingerprint": list_fingerprint,
                    "detail_url": response.url,
                    "detail_body": bytes(response.body),
                    "detail_metadata": response_metadata,
                },
                # 省级页面会嵌入市级公共资源交易中心的正文 PDF。
                meta={"allow_offsite": True},
                dont_filter=True,
            )
            return

        yield from self._finish_parsed_detail(
            section=section,
            notice_id=notice_id,
            source_list=source_list,
            list_fingerprint=list_fingerprint,
            detail_url=response.url,
            detail_body=bytes(response.body),
            detail_metadata=response_metadata,
            parsed=parsed,
        )

    @staticmethod
    def _pdf_text(content: bytes) -> str:
        """从 iframe 公告 PDF 的文字层读取正文。

        不做 OCR；没有文字层时保留附件并将公告标记为 PARTIAL，
        避免将只有“公告发布时间”的记录误判为完整解析。
        """

        if not content:
            return ""
        try:
            from pypdf import PdfReader

            return "\n".join(
                page.extract_text() or ""
                for page in PdfReader(BytesIO(content)).pages
            ).strip()
        except Exception:  # noqa: BLE001 - 解析失败由 PARTIAL 状态显式保留
            return ""

    def parse_embedded_body_pdf(
        self,
        response: Response,
        section: str,
        notice_id: str,
        source_list: Mapping[str, Any],
        list_fingerprint: str,
        detail_url: str,
        detail_body: bytes,
        detail_metadata: Mapping[str, Any],
    ):
        parser_class = (
            SxzwfwGovernmentProcurementParser
            if section in config.GOVERNMENT_SECTION_CHANNELS
            else SxzwfwParser
        )
        pdf_text = self._pdf_text(bytes(response.body))
        parsed = parser_class.parse(
            section,
            detail_body,
            source_list,
            detail_url,
            supplemental_text=pdf_text,
        )
        self._cache_embedded_body_pdf(
            parsed=parsed,
            notice_id=notice_id,
            content=bytes(response.body),
            text_extracted=bool(pdf_text),
        )
        metadata = dict(detail_metadata)
        related = dict(metadata.get("relatedRequests") or {})
        body_metadata = self.build_response_metadata(
            response,
            request_kind="embedded_notice_body_pdf",
            context={"section": section, "noticeId": notice_id},
        )
        body_metadata["contentSha256"] = hashlib.sha256(response.body).hexdigest()
        body_metadata["contentBytes"] = len(response.body)
        body_metadata["textExtracted"] = bool(pdf_text)
        related["bodyDocument"] = body_metadata
        metadata["relatedRequests"] = related
        yield from self._finish_parsed_detail(
            section=section,
            notice_id=notice_id,
            source_list=source_list,
            list_fingerprint=list_fingerprint,
            detail_url=detail_url,
            detail_body=detail_body,
            detail_metadata=metadata,
            parsed=parsed,
            force_partial=not bool(pdf_text),
        )

    def parse_embedded_body_pdf_error(self, failure):
        values = failure.request.cb_kwargs
        section = values["section"]
        parser_class = (
            SxzwfwGovernmentProcurementParser
            if section in config.GOVERNMENT_SECTION_CHANNELS
            else SxzwfwParser
        )
        parsed = parser_class.parse(
            section,
            values["detail_body"],
            values["source_list"],
            values["detail_url"],
        )
        for attachment in parsed.attachments:
            if attachment.get("is_notice_body"):
                attachment["parse_status"] = "DOWNLOAD_FAILED"
        metadata = dict(values["detail_metadata"])
        related = dict(metadata.get("relatedRequests") or {})
        related["bodyDocument"] = {
            "requestKind": "embedded_notice_body_pdf",
            "error": str(failure.value),
        }
        metadata["relatedRequests"] = related
        yield from self._finish_parsed_detail(
            section=section,
            notice_id=values["notice_id"],
            source_list=values["source_list"],
            list_fingerprint=values["list_fingerprint"],
            detail_url=values["detail_url"],
            detail_body=values["detail_body"],
            detail_metadata=metadata,
            parsed=parsed,
            force_partial=True,
        )

    def _cache_embedded_body_pdf(
        self,
        *,
        parsed: ParsedNotice,
        notice_id: str,
        content: bytes,
        text_extracted: bool,
    ) -> None:
        attachment = next(
            (value for value in parsed.attachments if value.get("is_notice_body")),
            None,
        )
        if not attachment or not content:
            return
        record = {
            "平台代码": self.platform_code,
            "公告类型": get_notice_type_code(parsed.notice_type) or parsed.notice_type,
            "公告ID": notice_id,
        }
        relative_path = attachment_storage_path(record, attachment)
        output_root = Path(
            self.crawler.settings.get("NOTICE_OUTPUT_ROOT", "new_output")
        ).expanduser().resolve()
        target = output_root / relative_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.stat().st_size != len(content):
                temporary = target.with_name(f".{target.name}.part")
                temporary.write_bytes(content)
                temporary.replace(target)
        except OSError as exc:
            self.logger.warning("iframe正文PDF落盘失败 notice=%s: %s", notice_id, exc)
            attachment["parse_status"] = "STORAGE_FAILED"
            return
        attachment.update(
            {
                "storage_path": relative_path,
                "file_hash": hashlib.md5(
                    content, usedforsecurity=False
                ).hexdigest(),
                "file_size_bytes": len(content),
                "file_type": "application/pdf",
                "parse_status": (
                    "TEXT_EXTRACTED" if text_extracted else "DOWNLOADED_NO_OCR"
                ),
            }
        )

    def _finish_parsed_detail(
        self,
        *,
        section: str,
        notice_id: str,
        source_list: Mapping[str, Any],
        list_fingerprint: str,
        detail_url: str,
        detail_body: bytes,
        detail_metadata: Mapping[str, Any],
        parsed: ParsedNotice,
        force_partial: bool = False,
    ):
        parser_class = (
            SxzwfwGovernmentProcurementParser
            if section in config.GOVERNMENT_SECTION_CHANNELS
            else SxzwfwParser
        )
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
            detail_url=detail_url,
            data=parsed.data,
            notice_subtype=notice_subtype,
            raw_data={
                "list": source_list,
            },
            raw_html=detail_body,
            raw_text=parsed.raw_text,
            parse_status=(
                "PARTIAL"
                if force_partial or not self._has_substantive_body(parsed.raw_text)
                else "PARSED"
            ),
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
                "sxzwfwTrustedFields": ["发布日期"] if parsed.publish_time else [],
            },
            response_metadata=detail_metadata,
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
            headers={"Referer": detail_url, "Accept": "application/json,*/*"},
            callback=self.parse_attachment_metadata,
            errback=self.on_attachment_metadata_error,
            cb_kwargs={"item": item, "cms": cms},
            dont_filter=True,
        )

    @staticmethod
    def _has_substantive_body(raw_text: str) -> bool:
        value = re.sub(
            r"(?m)^\s*公告发布时间\s*[：:].*$",
            "",
            str(raw_text or ""),
        ).strip()
        return len(value) >= 40

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
