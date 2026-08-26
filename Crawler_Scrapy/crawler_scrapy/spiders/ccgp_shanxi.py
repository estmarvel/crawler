"""山西政府采购网全部采购公告爬虫。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping

from scrapy import Request
from scrapy.http import JsonRequest, Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.ccgp_shanxi import config
from crawler_scrapy.sites.ccgp_shanxi.parser import CcgpShanxiParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class CcgpShanxiSpider(BaseNoticeSpider):
    name = "ccgp_shanxi"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["www.ccgp-shanxi.gov.cn"]
    parser_version = "ccgp-shanxi-v1-portal-api-html"
    extraction_model_name = "ccgp-shanxi-site-rule-parser"

    # 接口字段和明确表格字段不交给AI；AI只补正文边界较容易变化的标量字段。
    ai_extract_fields = {
        "采购公告": (
            "所属行业", "采购组织形式", "资金来源", "项目实施地点", "质量要求",
            "政府采购政策资格要求", "特定资格要求", "采购文件获取地点",
            "响应文件提交地点", "开启地点", "开启方式", "采购人地址",
            "采购人联系人", "采购人联系方式", "采购代理机构地址",
            "采购代理机构联系人", "采购代理机构联系方式", "项目联系人", "项目联系电话",
        ),
        "采购结果公告": (
            "供应商地址", "供应商统一社会信用代码", "评审总得分", "采购人代表",
            "代理服务收费标准", "代理服务收费金额", "采购人地址",
            "采购人联系方式", "采购代理机构地址", "采购代理机构联系方式",
            "项目联系人", "项目联系电话",
        ),
        "采购终止公告": ("终止/废标原因", "采购人地址", "采购人联系方式", "项目联系人", "项目联系电话"),
        "采购变更公告": ("更正事项", "恢复采购时间", "采购人地址", "采购人联系方式", "项目联系人", "项目联系电话"),
        "采购合同公告": ("履约期限", "履约地点", "履约方式", "合同变更原因"),
        "履约验收公告": ("服务内容", "服务要求", "服务期限", "服务地点", "验收方式", "验收意见", "验收结论"),
        "采购意见征询": ("采购需求概况", "采用单一来源原因", "征求意见范围", "意见递交方式", "联系人", "联系电话", "联系邮箱"),
        "*": (),
    }

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.5,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,
        "NOTICE_SNAPSHOT_ENABLED": True,
        "NOTICE_SNAPSHOT_REQUIRED": True,
        "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
    }

    def __init__(
        self,
        categories: str | None = None,
        max_records: int | str = 20,
        page_size: int | str = 15,
        max_pages: int | str = 100,
        days: int | str | None = 14,
        start_date: str | None = None,
        end_date: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.feeds = self._select_feeds(categories)
        self.max_records = self._positive_int(max_records, 20)
        self.page_size = min(self._positive_int(page_size, 15), 100)
        self.max_pages = self._positive_int(max_pages, 100)
        self.window_start, self.window_end = self._time_window(days, start_date, end_date)
        self._counts = {feed: 0 for feed in self.feeds}
        self._seen: set[str] = set()

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _select_feeds(categories: str | None) -> tuple[str, ...]:
        if not categories:
            return config.DEFAULT_FEEDS
        requested = {x.strip() for x in categories.split(",") if x.strip()}
        selected = tuple(
            feed for feed in config.DEFAULT_FEEDS
            if feed in requested or feed.split(".", 1)[0] in requested
        )
        if not selected:
            raise ValueError(f"没有匹配的公告类别：{sorted(requested)}")
        return selected

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
        end = cls._boundary(end_date, True) or datetime.now()
        start = cls._boundary(start_date, False)
        if start is None and days not in (None, ""):
            count = int(days)
            if count <= 0:
                raise ValueError("days必须大于0")
            start = end - timedelta(days=count)
        if start and start > end:
            raise ValueError("start_date不能晚于end_date")
        return start, end

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": config.WEB_BASE_URL,
            "Referer": f"{config.WEB_BASE_URL}/site/category?parentId={config.CATEGORY_PARENT_ID}",
        }

    def start_requests(self):
        for feed in self.feeds:
            yield self._list_request(feed, 1)

    async def start(self):
        for request in self.start_requests():
            yield request

    def _list_request(self, feed: str, page: int) -> JsonRequest:
        payload: dict[str, Any] = {
            "pageNo": page,
            "pageSize": self.page_size,
            "categoryCode": config.FEEDS[feed][2],
        }
        if self.window_start:
            payload["publishDateBegin"] = self.window_start.strftime("%Y-%m-%d")
        if self.window_end:
            payload["publishDateEnd"] = self.window_end.strftime("%Y-%m-%d")
        return JsonRequest(
            config.LIST_URL,
            data=payload,
            headers=self._headers(),
            callback=self.parse_list,
            cb_kwargs={"feed": feed, "page": page},
            dont_filter=True,
        )

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value) / 1000)
            except (OSError, OverflowError, ValueError):
                return None
        return coerce_datetime(value)

    def parse_list(self, response: Response, feed: str, page: int):
        payload = response.json()
        page_data = (((payload or {}).get("result") or {}).get("data") or {}) if isinstance(payload, Mapping) else {}
        records = page_data.get("data") or []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            article_id = str(record.get("articleId") or "").strip()
            identity = f"{feed}:{article_id}"
            if not article_id or identity in self._seen or self._counts[feed] >= self.max_records:
                continue
            self._seen.add(identity)
            notice_type = config.FEEDS[feed][0]
            detail_url = config.detail_page_url(article_id)
            should_fetch, list_fingerprint = self.check_notice_candidate(
                notice_id=article_id,
                list_record=record,
                detail_url=detail_url,
                notice_type=notice_type,
                title=str(record.get("title") or ""),
                publish_time=self._datetime(record.get("publishDate")),
            )
            if not should_fetch:
                continue
            self._counts[feed] += 1
            yield Request(
                config.detail_api_url(article_id),
                headers=self._headers(),
                callback=self.parse_detail,
                cb_kwargs={
                    "feed": feed,
                    "list_record": dict(record),
                    "list_fingerprint": list_fingerprint,
                },
                dont_filter=True,
            )
        total = int(page_data.get("total") or 0)
        if (
            records and page * self.page_size < total and page < self.max_pages
            and self._counts[feed] < self.max_records
        ):
            yield self._list_request(feed, page + 1)

    def parse_detail(
        self,
        response: Response,
        feed: str,
        list_record: Mapping[str, Any],
        list_fingerprint: str,
    ):
        payload = response.json()
        detail = (((payload or {}).get("result") or {}).get("data")) if isinstance(payload, Mapping) else None
        if not isinstance(detail, Mapping):
            self.logger.warning("详情接口格式异常：feed=%s url=%s", feed, response.url)
            return
        notice_type, data, attachments, raw_html, raw_text = CcgpShanxiParser.parse(
            feed, detail, list_record
        )
        article_id = str(detail.get("articleId") or list_record.get("articleId") or "")
        category_code = config.FEEDS[feed][2]
        yield self.build_notice_item(
            notice_type=notice_type,
            notice_subtype=f"{config.PLATFORM_CODE}|{config.FEEDS[feed][1]}",
            notice_id=article_id,
            title=str(detail.get("title") or list_record.get("title") or ""),
            publish_time=self._datetime(detail.get("publishDate") or list_record.get("publishDate")),
            detail_url=config.detail_page_url(article_id),
            data=data,
            raw_data={"list": dict(list_record), "detail": dict(detail)},
            raw_html=raw_html,
            raw_text=raw_text,
            extraction_model=self.extraction_model_name,
            extraction_version=self.parser_version,
            source_list_fingerprint=list_fingerprint,
            field_meta={
                "site_parser": self.extraction_model_name,
                "feed": feed,
                "categoryCode": category_code,
                "announcementType": detail.get("announcementType"),
                "api_fields": [
                    "articleId", "title", "publishDate", "projectCode", "projectName",
                    "announcementType", "categoryNames", "districtCode",
                ],
            },
            response_metadata=self.build_response_metadata(
                response,
                request_kind="detail_api",
                context={"feed": feed, "categoryCode": category_code, "articleId": article_id},
            ),
            attachments=attachments,
        )
