import re

import scrapy

from sxbid_crawler.ai_service import supplement_row_with_ai

from sxbid_crawler.columns import COLUMNS, DEFAULT_VALUE, empty_row
from sxbid_crawler.items import SxbidProjectRowItem
from sxbid_crawler.parsers import (
    extract_pdf_urls,
    extract_project_code,
    parse_detail_summary,
    parse_notice_history,
    parse_notice_text,
    parse_related_content,
)
from sxbid_crawler.pdf_ocr import get_pdf_text_from_response


class SxbidNoticeSpider(scrapy.Spider):
    name = "sxbid_notice"
    allowed_domains = ["www.sxbid.com.cn"]

    BASE_URL = "https://www.sxbid.com.cn"

    CHANNELS = {
        11: ("依法招标-招标公告", "招标公告"),
        12: ("依法招标-中标候选人公示", "中标候选人公示"),
        13: ("依法招标-中标结果公示", "中标结果公示"),
        14: ("依法招标-更正公告公示", "更正公告"),
        41: ("前期物业-招标公告", "招标公告"),
    }

    DETAIL_PATTERN = re.compile(
        r"^/f/new/notice/[01]/[0-9a-fA-F]{32}(?:$|[?#])"
    )

    def __init__(
        self,
        max_items_per_channel=1,
        channel_ids="",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_items_per_channel = int(max_items_per_channel)

        requested = {
            int(value.strip())
            for value in str(channel_ids).split(",")
            if value.strip().isdigit()
        }
        self.channel_ids = requested or set(self.CHANNELS)

    def make_list_requests(self):
        for channel_id, (column_name, announcement_type) in self.CHANNELS.items():
            if channel_id not in self.channel_ids:
                continue

            url = f"{self.BASE_URL}/f/new/notice/list/{channel_id}"

            yield scrapy.Request(
                url,
                callback=self.parse_list,
                cb_kwargs={
                    "channel_id": channel_id,
                    "column_name": column_name,
                    "announcement_type": announcement_type,
                },
                headers={
                    "Referer": f"{self.BASE_URL}/f/new/jypt",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                dont_filter=True,
            )

    async def start(self):
        for request in self.make_list_requests():
            yield request

    def start_requests(self):
        yield from self.make_list_requests()

    def parse_list(
        self,
        response,
        channel_id,
        column_name,
        announcement_type,
    ):
        found = 0
        seen = set()

        for anchor in response.css("a[href]"):
            href = (anchor.attrib.get("href") or "").strip()

            if not self.DETAIL_PATTERN.search(href):
                continue

            detail_url = response.urljoin(href)
            if detail_url in seen:
                continue

            title = (
                anchor.attrib.get("title")
                or "".join(anchor.css("::text").getall())
            ).strip()

            if not title:
                continue

            seen.add(detail_url)
            found += 1

            yield scrapy.Request(
                detail_url,
                callback=self.parse_detail,
                cb_kwargs={
                    "raw_title": title,
                    "column_name": column_name,
                    "announcement_type": announcement_type,
                },
                headers={
                    "Referer": response.url,
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                dont_filter=False,
            )

            if found >= self.max_items_per_channel:
                break

        if found == 0:
            self.logger.warning(
                "栏目未发现详情链接: id=%s name=%s status=%s length=%s",
                channel_id,
                column_name,
                response.status,
                len(response.text),
            )
        else:
            self.logger.info(
                "栏目发现公告: id=%s name=%s count=%s",
                channel_id,
                column_name,
                found,
            )

    def parse_detail(
        self,
        response,
        raw_title,
        column_name,
        announcement_type,
    ):
        row = empty_row("")
        parse_detail_summary(response.text, row)

        row["公告类型"] = announcement_type
        row["详情链接"] = response.url
        row["来源栏目"] = column_name
        row["公告历史"] = f"{raw_title}|{response.url}"

        parse_notice_history(response.text, response.url, row)

        detail_text = "\n".join(
            value.strip()
            for value in response.xpath("//body//text()").getall()
            if value.strip()
        )

        pdf_urls = extract_pdf_urls(response.text)
        pdf_url = response.urljoin(pdf_urls[0]) if pdf_urls else ""
        project_code = extract_project_code(response.text)

        if not project_code:
            self.logger.warning(
                "详情页未找到项目编号: title=%s url=%s",
                raw_title,
                response.url,
            )
            yield from self.finish_or_request_pdf(
                row,
                pdf_url,
                response.url,
                detail_text,
            )
            return

        related_url = (
            f"{self.BASE_URL}/f/new/notice/"
            f"getRelatedContent/1/{project_code}"
        )

        yield scrapy.Request(
            related_url,
            callback=self.parse_related,
            cb_kwargs={
                "row": row,
                "pdf_url": pdf_url,
                "detail_url": response.url,
                "detail_text": detail_text,
            },
            headers={"Referer": response.url},
            dont_filter=True,
        )

    def parse_related(
        self,
        response,
        row,
        pdf_url,
        detail_url,
        detail_text,
    ):
        parse_related_content(response.text, row)

        related_parts = response.xpath("//text()").getall()
        related_text = "\n".join(
            value.strip()
            for value in related_parts
            if value.strip()
        )
        if not related_text:
            related_text = response.text

        combined_text = "\n".join(
            value for value in [detail_text, related_text] if value
        )

        yield from self.finish_or_request_pdf(
            row,
            pdf_url,
            detail_url,
            combined_text,
        )

    def finish_or_request_pdf(
        self,
        row,
        pdf_url,
        detail_url,
        detail_text,
    ):
        if not pdf_url:
            self.logger.warning("公告没有找到PDF: %s", detail_url)
            row = supplement_row_with_ai(
                row,
                detail_text,
                link=detail_url,
            )
            yield self.row_to_item(row)
            return

        yield scrapy.Request(
            pdf_url,
            callback=self.parse_pdf,
            errback=self.handle_pdf_failure,
            cb_kwargs={
                "row": row,
                "detail_text": detail_text,
                "detail_url": detail_url,
            },
            headers={"Referer": detail_url},
            dont_filter=True,
        )

    def handle_pdf_failure(self, failure):
        request = failure.request
        kwargs = request.cb_kwargs or {}

        row = kwargs.get("row", {})
        detail_text = kwargs.get("detail_text", "")
        detail_url = kwargs.get("detail_url", "")

        self.logger.error(
            "PDF请求最终失败，保留网页解析结果: "
            "detail_url=%s pdf_url=%s error=%s",
            detail_url,
            request.url,
            failure.getErrorMessage(),
        )

        row = supplement_row_with_ai(
            row,
            detail_text,
            link=detail_url,
        )
        yield self.row_to_item(row)

    def parse_pdf(
        self,
        response,
        row,
        detail_text,
        detail_url,
    ):
        is_valid_pdf = (
            len(response.body) >= 1000
            and response.body.startswith(b"%PDF")
        )

        if not is_valid_pdf:
            self.logger.error(
                "PDF重试后仍无效，保留网页解析结果: "
                "detail_url=%s pdf_url=%s bytes=%s",
                detail_url,
                response.url,
                len(response.body),
            )
            row = supplement_row_with_ai(
                row,
                detail_text,
                link=detail_url,
            )
            yield self.row_to_item(row)
            return

        pdf_text = get_pdf_text_from_response(
            response.url,
            response.body,
        )

        self.logger.info(
            "PDF正文提取完成: url=%s bytes=%s text_chars=%s",
            response.url,
            len(response.body),
            len(pdf_text),
        )

        if pdf_text:
            parse_notice_text(pdf_text, row)

        ai_source_text = "\n".join(
            value for value in [pdf_text, detail_text] if value
        )
        row = supplement_row_with_ai(
            row,
            ai_source_text,
            link=detail_url,
        )

        yield self.row_to_item(row)

    @staticmethod
    def row_to_item(row):
        item = SxbidProjectRowItem()

        for column in COLUMNS:
            item[column] = row.get(column, DEFAULT_VALUE)

        return item
