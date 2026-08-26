"""吕梁市公共资源交易中心全部交易大类和公告栏目的 Spider。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from io import BytesIO
import hashlib
import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader
from scrapy import Request
from scrapy.http import Response

from crawler_scrapy.schemas.notice_fields import coerce_datetime
from crawler_scrapy.sites.llggzy import config
from crawler_scrapy.sites.llggzy.parser import LlggzyParser
from crawler_scrapy.spiders.base_notice import BaseNoticeSpider


class LlggzySpider(BaseNoticeSpider):
    name = "llggzy"
    platform_name = config.PLATFORM_NAME
    platform_code = config.PLATFORM_CODE
    allowed_domains = ["ggzyjyzx.lvliang.gov.cn"]
    parser_version = LlggzyParser.VERSION
    extraction_model_name = "llggzy-cms-pdf-rule-parser"
    custom_settings = {
        "ROBOTSTXT_OBEY": False, "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.8, "AUTOTHROTTLE_ENABLED": True,
        "NOTICE_SNAPSHOT_ENABLED": True, "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": True,
        # 合同公开中存在约 80MB 的公开 PDF，60 秒会在正文提取前超时。
        "DOWNLOAD_TIMEOUT": 300,
    }

    @classmethod
    def update_settings(cls, settings) -> None:
        super().update_settings(settings)
        pipelines = settings.getdict("ITEM_PIPELINES")
        pipelines["crawler_scrapy.pipelines.NoticeMultiFormatPipeline"] = None
        pipelines["crawler_scrapy.sites.llggzy.exporter.LlggzyMultiFormatPipeline"] = 300
        settings.set("ITEM_PIPELINES", pipelines, priority="spider")

    def __init__(self, feeds: str | None = None, modules: str | None = None,
                 categories: str | None = None, max_records: int | str = 200,
                 max_pages: int | str = 5000, start_date: str | None = None,
                 end_date: str | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        selected_feeds = {x.strip() for x in str(feeds or "").split(",") if x.strip()}
        selected_modules = {x.strip() for x in str(modules or "").split(",") if x.strip()}
        selected_categories = {x.strip() for x in str(categories or "").split(",") if x.strip()}
        self.feeds = tuple(feed for feed in config.DEFAULT_FEEDS
                           if (not selected_feeds or feed in selected_feeds)
                           and (not selected_modules or feed.split(".", 1)[0] in selected_modules)
                           and (not selected_categories or feed.split(".", 1)[1] in selected_categories))
        invalid = selected_feeds - set(config.DEFAULT_FEEDS)
        if invalid:
            raise ValueError(f"未知栏目: {sorted(invalid)}")
        self.max_records = self._positive(max_records, 200)
        self.max_pages = self._positive(max_pages, 5000)
        self.window_start = self._boundary(start_date)
        self.window_end = self._boundary(end_date, end=True)
        self._scheduled = defaultdict(int)
        self._seen: set[tuple[str, str]] = set()

    @staticmethod
    def _positive(value: Any, default: int) -> int:
        try:
            number = int(value)
            return number if number > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _boundary(value: str | None, end: bool = False):
        if not value:
            return None
        parsed = coerce_datetime(value)
        if not parsed:
            raise ValueError(f"日期格式错误: {value}")
        return datetime.combine(parsed.date(), time.max if end else time.min)

    def start_requests(self):
        for feed in self.feeds:
            yield Request(config.list_url(feed), callback=self.parse_list,
                          cb_kwargs={"feed": feed, "page": 1}, dont_filter=True)

    async def start(self):
        for request in self.start_requests():
            yield request

    def parse_list(self, response: Response, feed: str, page: int):
        soup = BeautifulSoup(response.text, "html.parser")
        rows = []
        for li in soup.select("ul.notice-list li"):
            anchor = li.select_one("a[href]")
            if not anchor:
                continue
            href = urljoin(response.url, anchor.get("href", ""))
            if not re.search(r"/\d+\.htm(?:\?|$)", href):
                continue
            visible_title = anchor.get_text(" ", strip=True)
            if feed == "other.tender" and "[招标公告]" not in visible_title and "【招标公告】" not in visible_title:
                continue
            published = li.select_one("span")
            rows.append({"url": href, "title": anchor.get("title") or anchor.get_text(" ", strip=True),
                         "published": published.get_text(" ", strip=True) if published else ""})
        before_window = bool(rows and self.window_start)
        for row in rows:
            if self._scheduled[feed] >= self.max_records:
                break
            published_dt = coerce_datetime(row["published"])
            if self.window_end and published_dt and published_dt > self.window_end:
                before_window = False
                continue
            if self.window_start and published_dt and published_dt < self.window_start:
                continue
            before_window = False
            identity = (feed, row["url"])
            if identity in self._seen:
                continue
            self._seen.add(identity)
            self._scheduled[feed] += 1
            fingerprint = hashlib.sha256(f'{row["title"]}|{row["published"]}|{row["url"]}'.encode()).hexdigest()
            yield Request(row["url"], callback=self.parse_detail,
                          cb_kwargs={"feed": feed, "record": row, "fingerprint": fingerprint}, dont_filter=True)
        if rows and page < self.max_pages and self._scheduled[feed] < self.max_records and not before_window:
            yield Request(config.list_url(feed, page + 1), callback=self.parse_list,
                          cb_kwargs={"feed": feed, "page": page + 1}, dont_filter=True)

    def parse_detail(self, response: Response, feed: str, record: Mapping[str, str], fingerprint: str):
        soup = BeautifulSoup(response.text, "html.parser")
        title_node = soup.select_one(".detail-h1")
        title = title_node.get_text(" ", strip=True) if title_node else record["title"]
        info_text = soup.select_one(".detail-infor")
        publish_match = re.search(r"发布时间\s*[：:]\s*([0-9年月日:/ .-]+)", info_text.get_text(" ", strip=True) if info_text else "")
        published = publish_match.group(1).strip() if publish_match else record["published"]
        content = soup.select_one(".detail-con1")
        body_text = content.get_text("\n", strip=True) if content else ""
        iframe = content.select_one("iframe[src]") if content else None
        pdf_url = urljoin(response.url, iframe.get("src", "")) if iframe else ""
        context = {"feed": feed, "record": dict(record), "fingerprint": fingerprint,
                   "title": title, "published": published, "detail_html": response.text,
                   "detail_url": response.url, "body_text": body_text, "pdf_url": pdf_url}
        if pdf_url:
            yield Request(pdf_url, callback=self.parse_pdf, errback=self.parse_pdf_error,
                          cb_kwargs={"context": context}, dont_filter=True,
                          meta={"handle_httpstatus_all": True})
        else:
            yield self._build_item(context, body_text)

    def parse_pdf(self, response: Response, context: Mapping[str, Any]):
        text = ""
        if not response.body.lstrip().startswith(b"%PDF"):
            soup = BeautifulSoup(response.text, "html.parser")
            nested = soup.select_one("iframe[src],embed[src],object[data],a[href$='.pdf']")
            nested_url = nested.get("src") or nested.get("data") or nested.get("href") if nested else ""
            if nested_url:
                nested_context = dict(context)
                nested_context["pdf_url"] = urljoin(response.url, nested_url)
                yield Request(nested_context["pdf_url"], callback=self.parse_pdf, errback=self.parse_pdf_error,
                              cb_kwargs={"context": nested_context}, dont_filter=True,
                              meta={"handle_httpstatus_all": True})
                return
        try:
            reader = PdfReader(BytesIO(response.body))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if not text.strip():
                self.crawler.stats.inc_value("llggzy/pdf_text_layer_empty")
                self.logger.warning("PDF无可提取文本层（疑似扫描件），保留原PDF附件供核验: %s", response.url)
        except Exception as exc:
            self.logger.warning("PDF正文提取失败 url=%s status=%s error=%s", response.url, response.status, exc)
        yield self._build_item(context, text or context["body_text"])

    def parse_pdf_error(self, failure):
        context = failure.request.cb_kwargs["context"]
        self.logger.warning("PDF请求失败，仍保存详情和公告记录: %s", failure.value)
        yield self._build_item(context, context["body_text"])

    def _build_item(self, context: Mapping[str, Any], body_text: str):
        feed = context["feed"]
        info = config.feed_info(feed)
        pdf_url = context["pdf_url"]
        attachments = []
        if pdf_url:
            path_name = urlsplit(pdf_url).path.rsplit("/", 1)[-1]
            attachments.append({"file_name": path_name if "." in path_name else f'{context["title"]}.pdf',
                                "file_url": pdf_url, "source_file_id": path_name, "source": "detail_iframe"})
        parsed, raw_html = LlggzyParser.parse(
            feed, context["title"], context["published"], body_text,
            context["detail_url"], attachments, context["detail_html"],
        )
        notice_id_match = re.search(r"/(\d+)\.htm", context["detail_url"])
        notice_id = notice_id_match.group(1) if notice_id_match else hashlib.sha256(context["detail_url"].encode()).hexdigest()[:24]
        return self.build_notice_item(
            notice_type=parsed.notice_type,
            notice_subtype=f'llggzy|{info["module_label"]}|{info["category_label"]}',
            notice_id=notice_id, title=parsed.title, publish_time=parsed.publish_time,
            detail_url=context["detail_url"], data=parsed.data,
            raw_data={"feed": feed, "list": context["record"], "pdf_url": pdf_url},
            raw_html=raw_html, raw_text=parsed.raw_text,
            extraction_model=self.extraction_model_name, extraction_version=self.parser_version,
            source_list_fingerprint=context["fingerprint"], attachments=parsed.attachments,
        )
