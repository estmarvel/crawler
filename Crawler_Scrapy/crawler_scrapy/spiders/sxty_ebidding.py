"""招采进宝山西公开公告 API + 选择性混合 AI Spider。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Mapping

from scrapy import Request
from scrapy.exceptions import CloseSpider
from scrapy.http import JsonRequest, Response

from crawler_scrapy.ai.glm52_profile import (
    QWEN3_HYBRID_SETTINGS,
    install_hybrid_pipeline,
)
from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.sxty_ebidding import config
from crawler_scrapy.sites.sxty_ebidding.parser import (
    SxtyEbiddingParser,
    contains_captcha,
    decode_dynamic_res,
    find_detail_match,
)
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class SxtyEbiddingSpider(BaseNoticeSpider):
    name = "sxty_ebidding"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["sxty.ebidding.net.cn"]
    parser_version = SxtyEbiddingParser.parser_version
    extraction_model_name = "sxty-ebidding-public-api-html-rule-parser"
    ai_metadata_key = "sxtyEbiddingHybridAi"
    ai_trusted_fields_meta_key = "sxtyEbiddingApiTrustedFields"
    ai_log_name = "招采进宝山西"

    # 只有“更正实际内容”在本站各模板中长期依赖语义边界，因此每条更正类
    # 都进入候选窗口复核；其余字段仅在规则缺值且有明确标签，或命中下方
    # 站点异常钩子时升级，绝不把整篇 HTML 或全部预设字段交给模型。
    ai_extract_fields = {
        "招标计划": (),
        "资格预审公告": (),
        "招标公告": (),
        "中标候选人公示": (),
        "中标结果公示": (),
        "更正结果公示": ("公告内容",),
    }
    ai_sparse_review_fields = {}
    ai_candidate_fields = {
        "招标计划": (
            "招标方式", "项目类型", "项目总投资", "招标内容",
            "招标人名称", "行政监督部门", "建设内容及规模",
        ),
        "资格预审公告": (
            "项目编号", "招标编号", "开标时间", "项目总投资/估算金额",
            "招标金额", "资金来源", "项目地点", "项目概况与招标范围",
            "申请人资格要求/投标人资格要求", "获取方式", "递交方法",
            "开启时间", "开启地点",
            "评审办法", "投标保证金方式",
        ),
        "招标公告": (
            "项目编号", "招标编号", "开标时间", "项目总投资/估算金额",
            "招标金额", "资金来源", "项目地点", "项目规模",
            "招标内容与范围", "申请人资格要求/投标人资格要求",
            "工期/服务期/供货日期", "质量要求", "获取方式",
            "递交方法", "开启时间", "开启地点", "评审办法",
            "投标保证金方式", "招标人联系方式", "招标代理机构联系方式",
        ),
        "中标候选人公示": (
            "项目编号", "招标编号", "公示时间",
            "招标人联系方式", "招标代理机构联系方式",
        ),
        "中标结果公示": (
            "项目编号", "招标编号", "工期", "项目经理",
            "项目经理证书名称", "项目经理证书编号",
            "招标人联系方式", "招标代理机构联系方式",
        ),
        "更正结果公示": (
            "公告内容",
            "项目编号", "招标编号", "开标时间", "标书发售时间",
            "招标人联系方式", "招标代理机构联系方式",
            "监督部门地址", "监督部门联系人", "监督部门联系方式",
            "依据文件", "依据文号",
        ),
    }

    custom_settings = {
        **QWEN3_HYBRID_SETTINGS,
        # 站点没有 robots.txt（实测 404）；只访问官网前端自身使用的公开 API。
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 4.0,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 4.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,
        "AUTOTHROTTLE_MAX_DELAY": 180.0,
        # 403/429/验证码不盲目重试，由访问保护逻辑立即停爬并保留 JOBDIR。
        "RETRY_TIMES": 0,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": False,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": False,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        install_hybrid_pipeline(settings)
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

    def is_ai_field_suspicious(
        self,
        notice_type: str,
        field_name: str,
        value: Any,
        data: Mapping[str, Any],
        text: str,
    ) -> bool:
        value_text = str(value or "")
        if notice_type in {"招标公告", "资格预审公告"} and field_name in {
            "招标内容与范围", "项目概况与招标范围"
        }:
            # 本站实采中，范围章节有 28 条继续吞入质量段；任何质量标题进入
            # 范围值都属于明确边界异常，才触发 AI 的局部窗口复核。
            return bool(re.search(r"(?:质量要求|质量标准)\s*[：:]", value_text))
        if notice_type == "招标计划":
            return "|" in value_text or any(
                label in value_text
                for label in ("项目总投资：", "招标方式：", "行政监督部门：", "预计发布时间：")
            )
        return False

    def __init__(
        self,
        feeds: str | None = None,
        max_records: int | str = 1_000_000,
        max_pages: int | str = 10_000,
        page_size: int | str = 20,
        days: int | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        keyword: str = "",
        city: str = "",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        selected = tuple(
            part.strip() for part in str(feeds or "").split(",") if part.strip()
        ) or config.DEFAULT_FEEDS
        invalid = set(selected) - set(config.FEEDS)
        if invalid:
            raise ValueError(f"不支持的易招标栏目：{','.join(sorted(invalid))}")
        self.feeds = selected
        self.max_records = self._positive_int(max_records, 1_000_000)
        self.max_pages = self._positive_int(max_pages, 10_000)
        # 单页最多 50，减少分页请求的同时避免超大响应触发风控。
        self.page_size = min(self._positive_int(page_size, 20), 50)
        self.window_start, self.window_end = self._time_window(
            days, start_date, end_date
        )
        self.keyword = str(keyword or "").strip()
        self.city = str(city or "").strip()
        self._scheduled: dict[str, int] = defaultdict(int)
        self._seen: set[str] = set()

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _boundary(value: str | None, *, end_of_day: bool) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = coerce_datetime(raw)
        if parsed is None:
            raise ValueError(f"无法解析日期时间：{raw}")
        if end_of_day and len(raw) == 10:
            return datetime.combine(parsed.date(), time.max)
        return parsed

    @classmethod
    def _time_window(cls, days, start_date, end_date):
        start = cls._boundary(start_date, end_of_day=False)
        finish = cls._boundary(end_date, end_of_day=True)
        if start is None and days not in (None, ""):
            count = int(days)
            if count <= 0:
                raise ValueError("days 必须大于0")
            start = (finish or datetime.now()) - timedelta(days=count)
        if start and finish and start > finish:
            raise ValueError("start_date不能晚于end_date")
        return start, finish

    @staticmethod
    def _api_time(value: datetime | None, *, end: bool = False) -> str:
        if value is None:
            return ""
        point = value
        if end:
            point = datetime.combine(value.date(), time(23, 59, 59, 999000))
        return point.strftime("%Y-%m-%dT%H:%M:%S.") + f"{point.microsecond // 1000:03d}Z"

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": config.BASE_URL,
            "Referer": config.ENTRY_URL,
            "X-Requested-With": "XMLHttpRequest",
        }

    def start_requests(self):
        for feed in self.feeds:
            yield self._list_request(feed, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _list_request(self, feed: str, page: int) -> JsonRequest:
        payload = config.list_payload(
            feed,
            page,
            self.page_size,
            publish_date=self._api_time(self.window_start),
            publish_end_date=self._api_time(self.window_end, end=True),
            keyword=self.keyword,
            city=self.city,
        )
        return JsonRequest(
            config.LIST_API_URL,
            data=payload,
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"feed": feed, "page": page},
            dont_filter=True,
        )

    def _detail_request(
        self,
        feed: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
        list_metadata: Mapping[str, Any],
    ) -> JsonRequest:
        content_id = str(list_record["id"])
        return JsonRequest(
            config.DETAIL_API_URL,
            data=config.detail_payload(content_id),
            headers=self._headers(),
            callback=self.parse_detail,
            cb_kwargs={
                "feed": feed,
                "list_record": dict(list_record),
                "list_fingerprint": list_fingerprint,
                "list_metadata": dict(list_metadata),
            },
            dont_filter=True,
        )

    def _api_json(self, response: Response, request_kind: str) -> Mapping[str, Any]:
        body = bytes(response.body or b"")
        content_type = response.headers.get(b"Content-Type", b"").decode(
            "latin-1", errors="ignore"
        )
        if contains_captcha(body):
            self.crawler.stats.inc_value("sxty_ebidding/captcha_detected")
            self.logger.error(
                "易招标接口返回验证码页面，保护性停爬：kind=%s url=%s",
                request_kind,
                response.url,
            )
            raise CloseSpider("sxty_captcha_detected")
        if "json" not in content_type.lower() and not body.lstrip().startswith(b"{"):
            self.crawler.stats.inc_value("sxty_ebidding/non_json_response")
            self.logger.error(
                "易招标接口返回非JSON，保护性停爬：kind=%s content_type=%s",
                request_kind,
                content_type,
            )
            raise CloseSpider("sxty_non_json_response")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CloseSpider("sxty_invalid_json_response") from exc
        if not isinstance(payload, Mapping):
            raise CloseSpider("sxty_invalid_json_response")
        if payload.get("code") not in (None, 0, "0"):
            self.logger.error(
                "易招标接口业务异常：kind=%s code=%s msg=%s",
                request_kind,
                payload.get("code"),
                payload.get("msg") or payload.get("message"),
            )
            raise CloseSpider("sxty_api_rejected")
        return payload

    def parse_list(self, response: Response, feed: str, page: int):
        payload = self._api_json(response, "list_api")
        result = payload.get("res")
        if not isinstance(result, Mapping):
            raise CloseSpider("sxty_list_missing_res")
        rows = result.get("rows") or []
        if not isinstance(rows, list):
            raise CloseSpider("sxty_list_invalid_rows")
        definition = config.FEEDS[feed]
        list_metadata = self.build_response_metadata(
            response,
            request_kind="list_api",
            context={
                "feed": feed,
                "page": page,
                "categoryId": definition["category_id"],
                "total": result.get("total"),
            },
        )
        for raw in rows:
            if self._scheduled[feed] >= self.max_records:
                break
            if not isinstance(raw, Mapping):
                continue
            content_id = str(raw.get("id") or "").strip()
            if not content_id or content_id in self._seen:
                continue
            if str(raw.get("categoryId") or "") != definition["category_id"]:
                self.crawler.stats.inc_value("sxty_ebidding/list_category_mismatch")
                self.logger.warning(
                    "易招标列表栏目不匹配：feed=%s expected=%s actual=%s id=%s",
                    feed,
                    definition["category_id"],
                    raw.get("categoryId"),
                    content_id,
                )
                continue
            self._seen.add(content_id)
            detail_url = config.detail_page_url(content_id)
            should_fetch, fingerprint = self.check_notice_candidate(
                notice_id=content_id,
                notice_type=definition["schema"],
                list_record=raw,
                detail_url=detail_url,
                title=str(raw.get("title") or ""),
                publish_time=raw.get("publishDate"),
            )
            if not should_fetch:
                continue
            self._scheduled[feed] += 1
            yield self._detail_request(feed, raw, fingerprint, list_metadata)

        total_pages = int(result.get("pageCount") or 0)
        if (
            rows
            and page < self.max_pages
            and (not total_pages or page < total_pages)
            and self._scheduled[feed] < self.max_records
        ):
            yield self._list_request(feed, page + 1)

    def parse_detail(
        self,
        response: Response,
        feed: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
        list_metadata: Mapping[str, Any],
    ):
        envelope = self._api_json(response, "detail_api")
        decoded = decode_dynamic_res(envelope.get("res"))
        content_id = str(list_record.get("id") or "")
        match = find_detail_match(decoded, content_id, list_record)
        parsed = SxtyEbiddingParser.parse(feed, match, list_record)
        placeholder_count = len(
            re.findall(r"#[\u4e00-\u9fffA-Za-z][^#\n]{1,60}#", parsed.raw_text)
        )
        if "测试" in parsed.title and placeholder_count >= 3:
            self.crawler.stats.inc_value("sxty_ebidding/template_notices_skipped")
            self.logger.warning(
                "跳过未填充的公开测试模板：id=%s title=%s placeholders=%s",
                content_id,
                parsed.title,
                placeholder_count,
            )
            return
        definition = config.FEEDS[feed]
        complete = bool(parsed.title and parsed.publish_time and parsed.raw_html)
        yield self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_subtype=feed,
            notice_id=content_id,
            title=parsed.title,
            publish_time=parsed.publish_time,
            detail_url=config.detail_page_url(content_id),
            data=parsed.data,
            raw_data={
                "feed": feed,
                "sourceChannel": definition["channel_label"],
                "sourceCategory": definition["source_label"],
                "list": dict(list_record),
                "detailDecoded": decoded,
                "transport": {"resEncoding": "base64+urlencode"},
            },
            raw_html=parsed.raw_html,
            raw_text=parsed.raw_text,
            parse_status="PARSED" if complete else "PARTIAL",
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            field_meta={
                "site_parser": self.extraction_model_name,
                "source_channel": definition["channel"],
                "source_channel_label": definition["channel_label"],
                "source_category": definition["category"],
                "source_category_label": definition["source_label"],
                "source_category_id": definition["category_id"],
                "source_project_id": str(match.project.get("id") or ""),
                "source_out_project_id": str(match.project.get("outProjectId") or ""),
                "source_package_id": str(match.package.get("id") or ""),
                "source_package_code": str(match.package.get("code") or ""),
                "body_format": "html",
                "validation_warnings": parsed.validation_warnings,
                self.ai_trusted_fields_meta_key: [
                    field
                    for field, present in (
                        ("发布日期", bool(parsed.publish_time)),
                        ("发布网站", True),
                        ("项目名称", bool(match.project.get("name"))),
                        (
                            "项目类型/行业分类",
                            str(match.content.get("bidType") or "")
                            in SxtyEbiddingParser.BID_TYPE_LABELS,
                        ),
                        (
                            "组织形式",
                            str(match.project.get("bidOrganizationWay") or "")
                            in SxtyEbiddingParser.ORGANIZATION_FORM_LABELS,
                        ),
                        ("招标方式", bool(match.content.get("purchaseName"))),
                    )
                    if present
                ],
            },
            response_metadata={
                "requestKind": "public_frontend_api",
                "listApi": dict(list_metadata),
                "detailApi": self.build_response_metadata(
                    response,
                    request_kind="detail_api",
                    context={
                        "feed": feed,
                        "contentId": content_id,
                        "decodedPackageCount": len(decoded.get("packages") or []),
                    },
                ),
            },
            attachments=parsed.attachments,
        )
