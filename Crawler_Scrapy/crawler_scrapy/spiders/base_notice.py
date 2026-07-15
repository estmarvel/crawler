"""所有具体网站 Spider 可复用的公告 Item 构造基类。"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Mapping, Sequence

import scrapy

from crawler_scrapy.items import NoticeItem
from crawler_scrapy.schemas.notice_fields import (
    NOTICE_SCHEMA_VERSION,
    canonicalize_attachment_list,
    canonicalize_notice_data,
    coerce_datetime,
    get_notice_type_code,
    normalize_notice_type,
)
from crawler_scrapy.storage.dedup import (
    build_list_fingerprint,
    build_notice_identity,
    get_notice_dedup_store,
)


class BaseNoticeSpider(scrapy.Spider):
    """所有具体网站公告 Spider 的公共基类。"""

    platform_name = ""
    platform_code = ""

    # None 表示由框架选择当前公告全部缺失业务字段。具体网站也可配置：
    # {"招标公告": ("项目名称", "招标金额"), "*": ("项目名称",)}
    ai_extract_fields: Mapping[str, Sequence[str]] | None = None

    def select_ai_extract_fields(
        self,
        notice_type: str,
        missing_fields: Sequence[str],
        data: Mapping[str, Any],
    ) -> list[str]:
        """网站级 AI 字段选择接口；子类也可以重写并按页面动态判断。"""

        if self.ai_extract_fields is None:
            return list(missing_fields)
        configured = self.ai_extract_fields.get(
            normalize_notice_type(notice_type),
            self.ai_extract_fields.get("*", ()),
        )
        allowed = set(configured)
        return [field for field in missing_fields if field in allowed]

    def check_notice_candidate(
        self,
        *,
        notice_id: str,
        list_record: Mapping[str, Any],
        detail_url: str = "",
        notice_type: str = "",
        title: str = "",
        publish_time: Any = "",
    ) -> tuple[bool, str]:
        """列表阶段的跨运行去重接口，返回“是否请求详情、列表指纹”。

        新公告、列表记录有变化或旧索引尚无列表指纹时请求详情；列表记录完全
        一致时直接跳过详情请求。关闭 NOTICE_DEDUP_ENABLED 时始终允许请求。
        """

        list_fingerprint = build_list_fingerprint(list_record)
        crawler = getattr(self, "crawler", None)
        if crawler is None or not crawler.settings.getbool(
            "NOTICE_DEDUP_ENABLED", True
        ):
            return True, list_fingerprint
        identity = build_notice_identity(
            platform_code=self.platform_code or self.name,
            notice_id=notice_id,
            detail_url=detail_url,
            notice_type=notice_type,
            title=title,
            publish_time=publish_time,
        )
        store = get_notice_dedup_store(
            crawler,
            output_root=crawler.settings.get("NOTICE_OUTPUT_ROOT", "output"),
            platform_code=self.platform_code or self.name,
        )
        should_fetch = store.should_fetch_detail(identity, list_fingerprint)
        if not should_fetch:
            crawler.stats.inc_value("dedup/list_details_skipped")
        return should_fetch, list_fingerprint

    @staticmethod
    def _normalize_attachment_list(
        attachments: Sequence[Mapping[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """把附件统一转换为数据库附件表兼容的 list[dict]。"""

        return canonicalize_attachment_list(attachments)

    @staticmethod
    def _content_fingerprint(
        raw_text: str,
        raw_html: Any,
        structured_data: Mapping[str, Any] | None = None,
    ) -> str:
        """按正文、HTML、结构化数据的优先级生成稳定 SHA256。"""

        if raw_text:
            content = raw_text.encode("utf-8")
        elif isinstance(raw_html, bytes):
            content = raw_html
        elif isinstance(raw_html, (bytearray, memoryview)):
            content = bytes(raw_html)
        elif raw_html not in (None, ""):
            content = str(raw_html).encode("utf-8")
        elif structured_data:
            content = json.dumps(
                dict(structured_data),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        else:
            return ""
        return hashlib.sha256(content).hexdigest()

    def build_notice_item(
        self,
        *,
        notice_type: str,
        notice_id: str = "",
        title: str = "",
        publish_time: str | date | datetime | None = None,
        detail_url: str = "",
        data: Mapping[str, Any] | None = None,
        notice_subtype: str = "",
        raw_data: Any = None,
        raw_html: bytes | bytearray | memoryview | str | None = None,
        raw_text: str | None = None,
        parse_status: str = "PARSED",
        fingerprint: str = "",
        extraction_model: str = "",
        extraction_version: str = "",
        is_verified: bool = False,
        field_meta: Mapping[str, Any] | None = None,
        source_list_fingerprint: str = "",
        attachments: Sequence[Mapping[str, Any]] | None = None,
    ) -> NoticeItem:
        """将网站专用解析结果封装成统一 NoticeItem。

        参数说明：
        - raw_html：用于保存 HTML 快照。普通 HTML 页面推荐传 response.body；
          JSON 接口中若正文位于 annContent/content 等字段，可传该 HTML 字符串。
        - raw_data：用于保留解析时使用的原始 JSON/字典，不直接写入 CSV。
        - detail_url：必须传用户实际可访问的详情页链接，而不是仅供爬虫使用的 API URL。
        """

        normalized_type = normalize_notice_type(notice_type)
        normalized_data = canonicalize_notice_data(
            normalized_type,
            data,
        )

        crawl_time = datetime.now()
        attachment_list = self._normalize_attachment_list(attachments)
        raw_text_value = str(raw_text or "").strip()
        field_meta_value = dict(field_meta or {})
        if source_list_fingerprint:
            field_meta_value["_dedup_list_fingerprint"] = str(
                source_list_fingerprint
            ).strip()
        parse_status_value = str(parse_status or "PENDING").strip().upper()
        fingerprint_value = str(fingerprint or "").strip() or self._content_fingerprint(
            raw_text_value,
            raw_html,
            normalized_data,
        )
        extraction_model_value = str(
            extraction_model
            or field_meta_value.get("site_parser")
            or "RULE"
        ).strip()
        extraction_version_value = str(
            extraction_version or NOTICE_SCHEMA_VERSION
        ).strip()

        actual_detail_url = str(
            detail_url
            or normalized_data.get("详情页链接")
            or ""
        ).strip()

        normalized_data["爬虫时间"] = crawl_time
        normalized_data["详情页链接"] = actual_detail_url
        normalized_data["公告正文"] = raw_text_value
        normalized_data["解析状态"] = parse_status_value
        normalized_data["内容指纹"] = fingerprint_value
        normalized_data["抽取方式"] = extraction_model_value
        normalized_data["抽取版本"] = extraction_version_value
        normalized_data["是否已核验"] = bool(is_verified)

        if attachment_list:
            normalized_data["附件"] = attachment_list
        elif normalized_data.get("附件") in (None, ""):
            normalized_data["附件"] = []

        file_urls: list[str] = []
        for attachment in attachment_list:
            url = (
                attachment.get("file_url")
                or attachment.get("download_url")
                or attachment.get("url")
                or attachment.get("preview_url")
            )
            if url:
                file_urls.append(str(url))

        item = NoticeItem()
        item["platform"] = self.platform_name
        item["platform_code"] = self.platform_code or self.name
        item["notice_id"] = str(notice_id or "")
        item["notice_type"] = get_notice_type_code(normalized_type)
        item["notice_subtype"] = str(notice_subtype or "")
        item["title"] = str(title or "")
        item["publish_time"] = coerce_datetime(publish_time)
        item["detail_url"] = actual_detail_url
        item["crawl_time"] = crawl_time
        item["data"] = normalized_data
        item["field_meta"] = field_meta_value
        item["raw_data"] = raw_data
        item["raw_html"] = raw_html
        item["raw_text"] = raw_text_value
        item["parse_status"] = parse_status_value
        item["fingerprint"] = fingerprint_value
        item["extraction_model"] = extraction_model_value
        item["extraction_version"] = extraction_version_value
        item["is_verified"] = bool(is_verified)
        item["snapshot_path"] = ""
        item["snapshot_sha256"] = ""
        item["attachments"] = attachment_list
        item["file_urls"] = file_urls
        return item
