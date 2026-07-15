"""华新阳光采购平台 Scrapy Spider。

示例：
    scrapy crawl huaxin -a max_records=20
    scrapy crawl huaxin -a sections=zbgg_zys,hxr

``sections`` 支持：zbgg_zys、hxr、gs、zbjh。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping
from urllib.parse import urljoin

import scrapy
from scrapy import Request
from scrapy.http import JsonRequest, Response

from crawler_scrapy.sites.huaxin import config
from crawler_scrapy.sites.huaxin.parser import HuaxinParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class HuaxinSpider(BaseNoticeSpider):
    """采集华新四个一级栏目，并输出框架统一的 NoticeItem。"""

    name = "huaxin"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.ygcgpt.com"]
    parser_version = "huaxin-v5"

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
        "NOTICE_SNAPSHOT_REQUIRED": False,
        # AI 开启时允许它补充华新映射中明确列出的可选业务字段。
        "NOTICE_AI_INCLUDE_OPTIONAL_FIELDS": True,
        "HUAXIN_RESOLVE_ATTACHMENT_URLS": True,
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
        """默认使用固定认证代理；只允许固定代理或天启代理，禁止服务器直连。"""

        super().update_settings(settings)
        mode = str(settings.get("CRAWLER_OUTBOUND_MODE", "static")).strip().lower()
        if mode == "tianqi":
            return
        if mode == "direct":
            raise ValueError("华新出口策略禁止 direct，任何情况下都不得使用服务器公网 IP")
        if mode != "static":
            raise ValueError(
                f"不支持的 CRAWLER_OUTBOUND_MODE={mode!r}；可选 static/tianqi"
            )

        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        middlewares[
            "crawler_scrapy.transport.proxy_middleware.TianqiProxyMiddleware"
        ] = None
        middlewares[
            "crawler_scrapy.transport.proxy_middleware.StaticProxyMiddleware"
        ] = 610
        middlewares[
            "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
        ] = 650
        settings.set("DOWNLOADER_MIDDLEWARES", middlewares, priority="spider")

        settings.set("TIANQI_PROXY_ENABLED", False, priority="spider")
        settings.set("STATIC_PROXY_ENABLED", True, priority="spider")
        # Scrapy 内置 HttpProxyMiddleware 负责通过固定代理建立 HTTPS CONNECT。
        settings.set("HTTPPROXY_ENABLED", True, priority="spider")
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
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.sections = self._parse_sections(sections)
        self.max_records = self._positive_int(max_records, 200)
        self.page_size = min(self._positive_int(page_size, 50), 100)
        self.max_pages = self._positive_int(max_pages, 10)

        self._scheduled_counts = {section: 0 for section in self.sections}
        self._seen_ids: set[tuple[str, str]] = set()

    @staticmethod
    def _positive_int(value: int | str, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_sections(value: str | None) -> tuple[str, ...]:
        if not value:
            return config.DEFAULT_SECTIONS
        requested = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
        unknown = [section for section in requested if section not in config.SECTION_CLASSIFICATIONS]
        if unknown:
            valid = ", ".join(config.SECTION_CLASSIFICATIONS)
            raise ValueError(f"未知华新栏目：{', '.join(unknown)}；可选值：{valid}")
        return requested

    @property
    def api_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": config.API_ORIGIN,
            "Referer": f"{config.API_ORIGIN}/",
        }
        return headers

    async def start(self) -> Iterable[Request]:
        for section in self.sections:
            yield self._list_request(section, page=1)

    def _list_request(self, section: str, page: int) -> JsonRequest:
        if section == "zbjh":
            return JsonRequest(
                url=config.BID_PLAN_LIST_URL,
                method="POST",
                headers=self.api_headers,
                data=config.build_bid_plan_list_payload(page, self.page_size),
                callback=self.parse_list,
                errback=self.on_list_error,
                cb_kwargs={"section": section, "page": page},
                dont_filter=True,
            )
        return JsonRequest(
            url=config.ANNOUNCEMENT_LIST_URL,
            method="POST",
            headers=self.api_headers,
            data=config.build_list_payload(section, page, self.page_size),
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

        for record in records:
            if self._scheduled_counts[section] >= self.max_records:
                break
            if not isinstance(record, Mapping):
                continue
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
                publish_time=record.get("releaseTime") or record.get("createTime") or "",
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
            yield self._detail_request(section, notice_id, record_with_meta)

        total = self._safe_int(data.get("total")) if isinstance(data, Mapping) else 0
        pages = self._safe_int(data.get("pages")) if isinstance(data, Mapping) else 0
        should_continue = (
            self._scheduled_counts[section] < self.max_records
            and page < self.max_pages
            and bool(records)
            and len(records) >= self.page_size
            and (not total or page * self.page_size < total)
            and (not pages or page < pages)
        )
        if should_continue:
            yield self._list_request(section, page + 1)
        else:
            self.logger.info(
                "[%s] 列表结束：page=%s scheduled=%s total=%s pages=%s",
                section,
                page,
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
            url = f"{config.BID_PLAN_DETAIL_URL}/{notice_id}"
            callback = self.parse_bid_plan_detail
        else:
            url = f"{config.ANNOUNCEMENT_DETAIL_URL}?annId={notice_id}"
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
        detail["_route_planid"] = notice_id
        detail.setdefault("annId", notice_id)
        self._merge_missing(detail, list_record)
        item = self._build_item(section, detail, source)
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
        self._merge_missing(detail, list_record)
        detail.setdefault("annId", notice_id)
        item = self._build_item(section, detail, source)
        if item is not None:
            yield self._start_attachment_resolution(item)

    def _start_attachment_resolution(self, item):
        attachments = [dict(value) for value in item.get("attachments") or []]
        if (
            not attachments
            or not self.settings.getbool("HUAXIN_RESOLVE_ATTACHMENT_URLS", True)
        ):
            return item
        return self._next_attachment_request(item, attachments, 0)

    def _next_attachment_request(self, item, attachments, index: int):
        if index >= len(attachments):
            item["attachments"] = attachments
            data = dict(item.get("data") or {})
            data["附件"] = attachments
            item["data"] = data
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
            url=f"{config.BIDDING_FILE_QUERY_URL}/{file_id}",
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
                urljoin(config.API_ORIGIN + "/", str(raw_url).strip())
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
            "华新附件元数据请求失败：file_id=%s error=%s",
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

    def _backup_detail_request(
        self,
        section: str,
        notice_id: str,
        list_record: dict[str, Any],
    ) -> Request:
        return Request(
            url=f"{config.INPUT_ANNOUNCEMENT_DETAIL_URL}?annId={notice_id}",
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
    ):
        subtype, notice_type, data, attachments = HuaxinParser.parse(section, detail)
        if not notice_type:
            self.logger.warning(
                "无法识别华新公告类型：section=%s annId=%s title=%r",
                section,
                detail.get("annId"),
                detail.get("annTitle"),
            )
            return None
        detail_url = HuaxinParser.detail_url(subtype, detail)
        return self.build_notice_item(
            notice_type=notice_type,
            notice_subtype=subtype,
            notice_id=str(detail.get("annId") or detail.get("_route_planid") or ""),
            title=str(detail.get("annTitle") or detail.get("planTitle") or detail.get("projectName") or ""),
            publish_time=str(detail.get("releaseTime") or detail.get("createTime") or ""),
            detail_url=detail_url,
            data=data,
            raw_data={"detail_source": source, "detail": detail},
            raw_html=HuaxinParser.raw_html(detail),
            raw_text=HuaxinParser.raw_text(detail),
            parse_status="PARSED",
            extraction_model="huaxin-rule-parser",
            extraction_version=self.parser_version,
            is_verified=False,
            field_meta={"site_parser": self.parser_version, "detail_source": source},
            source_list_fingerprint=str(
                detail.get("_crawler_list_fingerprint") or ""
            ),
            attachments=attachments,
        )

    def on_list_error(self, failure):
        request = failure.request
        self.logger.error(
            "华新列表请求失败：section=%s page=%s error=%s",
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
            self.logger.info("华新主详情请求失败，切换备用接口：annId=%s", notice_id)
            yield self._backup_detail_request(section, notice_id, kwargs.get("list_record", {}))
            return
        self.logger.warning(
            "华新详情请求失败：section=%s annId=%s source=%s error=%s",
            section,
            notice_id,
            source,
            failure.getErrorMessage(),
        )
