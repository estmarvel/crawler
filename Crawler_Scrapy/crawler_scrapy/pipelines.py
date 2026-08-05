"""HTML 快照、公告字段校验以及 CSV/JSON 导出 Pipeline。"""

from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy.pipelines.files import FilesPipeline
from twisted.internet.threads import deferToThread

from crawler_scrapy.ai.html_extractor import (
    AiExtractionConfig,
    AiExtractionResult,
    AiHtmlExtractionService,
    html_to_text,
)
from crawler_scrapy.schemas.notice_fields import (
    ANNOUNCEMENT_TYPES,
    NOTICE_SCHEMA_VERSION,
    SYSTEM_FIELDS,
    TYPE_OUTPUT_BASENAMES,
    canonicalize_attachment_list,
    canonicalize_notice_data,
    coerce_datetime,
    get_missing_fields,
    get_notice_fields,
    get_notice_type_code,
    normalize_notice_type,
)
from crawler_scrapy.storage.dedup import (
    build_content_fingerprint,
    build_notice_identity,
    get_notice_dedup_store,
)


TRACE_FIELD = "_trace"
TRACE_SCHEMA_VERSION = "1.0"


def _safe_path_part(value: Any, fallback: str = "unknown", max_length: int = 100) -> str:
    """将网站代码、公告ID等转换为安全的目录名或文件名。"""

    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", text)
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return (text or fallback)[:max_length]


def _truncate_path_component(value: str, max_bytes: int = 240) -> str:
    """按 UTF-8 字节安全截断单个路径组件，并保留扩展名和唯一性。"""

    if len(value.encode("utf-8")) <= max_bytes:
        return value
    suffix = Path(value).suffix
    marker = f"_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"
    reserved = len((marker + suffix).encode("utf-8"))
    budget = max(1, max_bytes - reserved)
    prefix: list[str] = []
    used = 0
    for character in value[: -len(suffix)] if suffix else value:
        size = len(character.encode("utf-8"))
        if used + size > budget:
            break
        prefix.append(character)
        used += size
    return f"{''.join(prefix).rstrip('._ ')}{marker}{suffix}"


def _ensure_site_context(spider, output_root: Path) -> Path:
    """确保同一网站始终使用固定输出目录。

    目录格式：
        output/<网站代码>/

    时间只作为每条公告的“爬虫时间”字段，不参与目录命名。
    """

    platform_code = str(
        getattr(spider, "platform_code", "")
        or getattr(spider, "name", "")
        or "unknown_site"
    )
    platform_code = _safe_path_part(platform_code, "unknown_site")
    spider.platform_code = platform_code

    site_dir = output_root / platform_code
    site_dir.mkdir(parents=True, exist_ok=True)
    spider.output_site_dir = str(site_dir)
    return site_dir


def _normalize_attachment_list(value: Any) -> list[dict[str, Any]]:
    """将附件统一转换为数据库附件表兼容的 list[dict]。"""

    return canonicalize_attachment_list(value)


def _to_json_compatible(value: Any) -> Any:
    """把数据库兼容的 Python 类型转换为 JSON 可以表示的值。"""

    # 源站公告、附件等 ID 经常是 19 位整数。JSON 本身不限制整数精度，
    # 但后续 Node.js 导入器只能精确表示到 2**53-1，因此在导出边界转为
    # 字符串，确保原始响应、哈希校验和最终 MongoDB 溯源文档完全一致。
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > 9_007_199_254_740_991:
            return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="milliseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {key: _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_compatible(item) for item in value]
    return value


def _raw_html_text(value: Any) -> str | None:
    """把快照输入转换为 MongoDB rawHtml 可直接保存的文本。"""

    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    return str(value)


def _sha256_json(value: Any) -> str | None:
    if value in (None, "", {}, []):
        return None
    payload = json.dumps(
        _to_json_compatible(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class NoticeFilesPipeline(FilesPipeline):
    """下载公告附件并回写数据库附件表的预设字段。

    该管道只保存源文件，不读取文档正文，也不执行 OCR/AI。下载路径使用公告
    身份和源站文件 ID，而不是可能不断变化的签名 URL，避免同一附件被重复保存。
    ``storage_path`` 保存相对于 ``FILES_STORE`` 的路径，便于后续迁移到对象存储。
    """

    @classmethod
    def from_crawler(cls, crawler):
        obj = super().from_crawler(crawler)
        obj.enabled = crawler.settings.getbool(
            "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED", False
        )
        # 允许专用运行入口只放宽附件请求，而不必同时放宽列表和详情接口。
        # 未配置时保持 Scrapy 原有 DOWNLOAD_TIMEOUT/RETRY_TIMES 行为，因此
        # 现有独立脚本和其他 Spider 不受影响。
        obj.attachment_download_timeout = crawler.settings.getfloat(
            "NOTICE_ATTACHMENT_DOWNLOAD_TIMEOUT",
            crawler.settings.getfloat("DOWNLOAD_TIMEOUT", 180.0),
        )
        obj.attachment_retry_times = crawler.settings.getint(
            "NOTICE_ATTACHMENT_RETRY_TIMES",
            crawler.settings.getint("RETRY_TIMES", 0),
        )
        return obj

    def get_media_requests(self, item, info):
        if not self.enabled:
            return []
        requests = super().get_media_requests(item, info)
        referer = str(ItemAdapter(item).get("detail_url") or "").strip()
        for index, request in enumerate(requests):
            request.meta["_notice_attachment_index"] = index
            attachment_timeout = getattr(
                self, "attachment_download_timeout", None
            )
            attachment_retries = getattr(
                self, "attachment_retry_times", None
            )
            if attachment_timeout is not None and attachment_timeout > 0:
                request.meta["download_timeout"] = attachment_timeout
            if attachment_retries is not None and attachment_retries >= 0:
                request.meta["max_retry_times"] = attachment_retries
            # 华新文件接口会返回经过授权的站外 CDN 签名 URL。该 URL 已由详情
            # fileId 查询得到，不是页面中任意抓取的外链，因此允许媒体管道下载。
            request.meta["allow_offsite"] = True
            if referer:
                request.headers.setdefault(b"Referer", referer.encode("utf-8"))
        return requests

    def file_path(self, request, response=None, info=None, *, item=None) -> str:
        adapter = ItemAdapter(item) if item is not None else None
        attachments = list(adapter.get("attachments") or []) if adapter else []
        index = int(request.meta.get("_notice_attachment_index", 0))
        attachment = (
            attachments[index]
            if 0 <= index < len(attachments) and isinstance(attachments[index], Mapping)
            else {}
        )

        platform = _safe_path_part(
            adapter.get("platform_code") if adapter else "", "unknown_site", 80
        )
        notice_type = _safe_path_part(
            adapter.get("notice_type") if adapter else "", "unknown_type", 80
        )
        notice_id = _safe_path_part(
            adapter.get("notice_id") if adapter else "", "unknown_notice", 120
        )
        source_id = _safe_path_part(
            attachment.get("source_file_id")
            or hashlib.sha256(request.url.encode("utf-8")).hexdigest()[:20],
            "unknown_file",
            120,
        )

        file_name = str(attachment.get("file_name") or "").strip()
        if not file_name:
            suffix = Path(urlsplit(request.url).path).suffix
            file_name = f"attachment{suffix if suffix else '.bin'}"
        file_name = _safe_path_part(
            Path(file_name.replace("\\", "/")).name,
            "attachment.bin",
            10_000,
        )
        component = _truncate_path_component(f"{source_id}_{file_name}")
        return f"{platform}/attachments/{notice_type}/{notice_id}/{component}"

    async def media_downloaded(self, response, request, info, *, item=None):
        result = await super().media_downloaded(
            response, request, info, item=item
        )
        content_type = response.headers.get(b"Content-Type", b"").decode(
            "latin-1", errors="replace"
        ).split(";", 1)[0].strip()
        result["file_size_bytes"] = len(response.body)
        result["file_type"] = content_type or mimetypes.guess_type(result["path"])[0]
        return result

    def item_completed(self, results, item, info):
        item = super().item_completed(results, item, info)
        if not self.enabled:
            return item

        adapter = ItemAdapter(item)
        attachments = [dict(value) for value in adapter.get("attachments") or []]
        for index, (ok, result) in enumerate(results):
            if index >= len(attachments):
                break
            attachment = attachments[index]
            if ok:
                attachment["storage_path"] = result.get("path") or None
                attachment["file_hash"] = result.get("checksum") or None
                attachment["file_size_bytes"] = result.get("file_size_bytes")
                attachment["file_type"] = (
                    result.get("file_type")
                    or attachment.get("file_type")
                    or mimetypes.guess_type(str(attachment.get("file_name") or ""))[0]
                )
                attachment["parse_status"] = (
                    "CACHED_NO_OCR"
                    if result.get("status") in {"cached", "uptodate"}
                    else "DOWNLOADED_NO_OCR"
                )
                self.crawler.stats.inc_value("attachments/download_success")
            else:
                attachment["parse_status"] = "DOWNLOAD_FAILED"
                self.crawler.stats.inc_value("attachments/download_failed")

        adapter["attachments"] = attachments
        return item


class NoticeDedupPipeline:
    """在快照、AI 和导出之前过滤已经保存过的公告内容版本。"""

    @classmethod
    def from_crawler(cls, crawler):
        obj = cls(
            output_root=Path(crawler.settings.get("NOTICE_OUTPUT_ROOT", "output")),
            enabled=crawler.settings.getbool("NOTICE_DEDUP_ENABLED", True),
        )
        obj.crawler = crawler
        return obj

    def __init__(self, output_root: Path, enabled: bool = True) -> None:
        self.output_root = output_root
        self.enabled = enabled
        self.store = None

    def open_spider(self):
        if not self.enabled:
            return
        spider = self.crawler.spider
        site_dir = _ensure_site_context(spider, self.output_root)
        self.store = get_notice_dedup_store(
            self.crawler,
            output_root=Path(
                self.crawler.settings.get("NOTICE_DEDUP_ROOT", self.output_root)
            ),
            platform_code=spider.platform_code,
        )
        imported = self.store.bootstrap_from_json_exports(site_dir / "json")
        if imported:
            spider.logger.info("从现有JSON结果导入 %s 条公告版本索引", imported)
            self.crawler.stats.inc_value("dedup/bootstrap_versions", imported)

    def process_item(self, item):
        if not self.enabled or self.store is None:
            return item

        adapter = ItemAdapter(item)
        identity = build_notice_identity(
            platform_code=adapter.get("platform_code"),
            notice_id=adapter.get("notice_id"),
            detail_url=adapter.get("detail_url"),
            notice_type=adapter.get("notice_type"),
            title=adapter.get("title"),
            publish_time=adapter.get("publish_time"),
        )
        content_fingerprint = build_content_fingerprint(adapter)
        field_meta = dict(adapter.get("field_meta") or {})
        list_fingerprint = str(
            field_meta.get("_dedup_list_fingerprint") or ""
        ).strip()

        if not content_fingerprint:
            self.crawler.stats.inc_value("dedup/skipped_no_fingerprint")
            self.crawler.spider.logger.warning(
                "公告缺少可用内容，无法去重：identity=%s",
                identity,
            )
            return item

        if not self.store.reserve(identity, content_fingerprint):
            self.store.update_list_fingerprint(identity, list_fingerprint)
            self.crawler.stats.inc_value("dedup/duplicate_versions")
            raise DropItem(
                "公告内容版本已保存，跳过快照、AI和导出："
                f"identity={identity} fingerprint={content_fingerprint}"
            )

        field_meta["_dedup"] = {
            "identity": identity,
            "content_fingerprint": content_fingerprint,
            "list_fingerprint": list_fingerprint,
        }
        adapter["field_meta"] = field_meta
        adapter["fingerprint"] = content_fingerprint
        self.crawler.stats.inc_value("dedup/new_versions")
        return item


class HtmlSnapshotPipeline:
    """将每条公告的 HTML 原文保存为独立快照文件。

    输出示例：
        output/<网站代码>/snapshots/03_招标公告/<公告ID>_<哈希>.html

    CSV 只记录快照路径和 SHA256；JSON `_trace.rawHtml` 另保留一份可独立
    搬运的溯源包，Mongo 导入时会规范为唯一的 rawHtml 字段。
    """

    @classmethod
    def from_crawler(cls, crawler):
        obj = cls(
            output_root=Path(crawler.settings.get("NOTICE_OUTPUT_ROOT", "output")),
            enabled=crawler.settings.getbool("NOTICE_SNAPSHOT_ENABLED", True),
            required=crawler.settings.getbool("NOTICE_SNAPSHOT_REQUIRED", False),
        )
        obj.crawler = crawler
        return obj

    def __init__(self, output_root: Path, enabled: bool, required: bool):
        self.output_root = output_root
        self.enabled = enabled
        self.required = required
        self.site_dir: Path | None = None

    def open_spider(self):
        spider = self.crawler.spider
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.site_dir = _ensure_site_context(spider, self.output_root)

    @staticmethod
    def _to_bytes(raw_html: Any) -> bytes:
        if raw_html is None:
            return b""
        if isinstance(raw_html, bytes):
            return raw_html
        if isinstance(raw_html, (bytearray, memoryview)):
            return bytes(raw_html)
        if isinstance(raw_html, str):
            return raw_html.encode("utf-8")
        return str(raw_html).encode("utf-8")

    def process_item(self, item):
        spider = self.crawler.spider
        adapter = ItemAdapter(item)
        if not self.enabled:
            adapter["snapshot_path"] = ""
            adapter["snapshot_sha256"] = ""
            return item

        raw_bytes = self._to_bytes(adapter.get("raw_html"))
        if not raw_bytes:
            message = (
                "公告未提供 raw_html，无法保存HTML快照："
                f"platform={adapter.get('platform_code')} "
                f"notice_id={adapter.get('notice_id')} "
                f"type={adapter.get('notice_type')}"
            )
            if self.required:
                raise DropItem(message)
            spider.logger.warning(message)
            adapter["snapshot_path"] = ""
            adapter["snapshot_sha256"] = ""
            return item

        notice_type = normalize_notice_type(adapter.get("notice_type"))
        if notice_type not in TYPE_OUTPUT_BASENAMES:
            raise DropItem(f"无法为未知公告类型保存快照：{notice_type!r}")

        digest = hashlib.sha256(raw_bytes).hexdigest()
        identity = (
            adapter.get("notice_id")
            or adapter.get("title")
            or adapter.get("detail_url")
            or digest[:16]
        )
        safe_identity = _safe_path_part(identity, digest[:16], max_length=80)

        if self.site_dir is None:
            self.site_dir = _ensure_site_context(spider, self.output_root)

        snapshot_dir = (
            self.site_dir
            / "snapshots"
            / TYPE_OUTPUT_BASENAMES[notice_type]
        )
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshot_path = snapshot_dir / f"{safe_identity}_{digest[:12]}.html"
        if not snapshot_path.exists():
            temp_path = snapshot_path.with_suffix(".html.tmp")
            temp_path.write_bytes(raw_bytes)
            temp_path.replace(snapshot_path)

        relative_path = snapshot_path.relative_to(self.output_root).as_posix()
        adapter["snapshot_path"] = relative_path
        adapter["snapshot_sha256"] = digest
        return item


class NoticeSchemaPipeline:
    """统一公告类型、补齐字段，并同步系统字段。"""

    @classmethod
    def from_crawler(cls, crawler):
        obj = cls()
        obj.crawler = crawler
        return obj

    def process_item(self, item):
        spider = self.crawler.spider
        adapter = ItemAdapter(item)

        notice_type_name = normalize_notice_type(adapter.get("notice_type"))
        if notice_type_name not in ANNOUNCEMENT_TYPES:
            raise DropItem(
                f"不支持的公告类型：{adapter.get('notice_type')!r}; "
                f"支持类型：{', '.join(ANNOUNCEMENT_TYPES)}"
            )

        normalized_data = canonicalize_notice_data(
            notice_type_name,
            adapter.get("data") or {},
        )

        crawl_time = coerce_datetime(adapter.get("crawl_time")) or datetime.now()
        publish_time = coerce_datetime(
            adapter.get("publish_time") or normalized_data.get("发布日期")
        )

        detail_url = str(
            adapter.get("detail_url")
            or normalized_data.get("详情页链接")
            or ""
        ).strip()

        snapshot_path = str(
            adapter.get("snapshot_path")
            or normalized_data.get("HTML快照路径")
            or ""
        ).strip()
        snapshot_sha256 = str(
            adapter.get("snapshot_sha256")
            or normalized_data.get("HTML快照SHA256")
            or ""
        ).strip()

        attachments = _normalize_attachment_list(adapter.get("attachments"))
        raw_text = str(
            adapter.get("raw_text")
            or normalized_data.get("公告正文")
            or ""
        ).strip()
        # 八类结构都预设两个独立编号。站点解析器可以用 API 字段提供更可靠
        # 的值；这里仅在字段仍为空时按正文中的明确标签补齐，不猜测组合字段
        # 的语义，也不把同一个无标签编号复制到两个字段。
        identifier_sources: dict[str, str] = {}
        identifier_labels = {
            "项目编号": (
                "招标项目编号", "采购项目编号", "投资项目统一代码", "项目代码", "项目编号"
            ),
            "招标编号": ("招标编号", "采购编号"),
        }
        for identifier_field, labels in identifier_labels.items():
            if normalized_data.get(identifier_field):
                identifier_sources[identifier_field] = "site_parser"
                continue
            for label in labels:
                match = re.search(
                    rf"{re.escape(label)}\s*[：:]\s*"
                    rf"([A-Za-z0-9][A-Za-z0-9._/-]{{2,190}})",
                    raw_text,
                )
                if match:
                    normalized_data[identifier_field] = match.group(1).strip("：:()（）")
                    identifier_sources[identifier_field] = f"raw_text:{label}"
                    break
            else:
                identifier_sources[identifier_field] = "missing"
        field_meta = dict(adapter.get("field_meta") or {})
        field_meta["identifierExtraction"] = {
            "version": "notice-identifiers-v1",
            "sources": identifier_sources,
        }
        adapter["field_meta"] = field_meta
        parse_status = str(
            adapter.get("parse_status")
            or normalized_data.get("解析状态")
            or "PENDING"
        ).strip().upper()
        fingerprint = str(
            adapter.get("fingerprint")
            or normalized_data.get("内容指纹")
            or snapshot_sha256
            or ""
        ).strip()
        extraction_model = str(
            adapter.get("extraction_model")
            or normalized_data.get("抽取方式")
            or "RULE"
        ).strip()
        extraction_version = str(
            adapter.get("extraction_version")
            or normalized_data.get("抽取版本")
            or NOTICE_SCHEMA_VERSION
        ).strip()
        is_verified_value = adapter.get("is_verified")
        if is_verified_value is None:
            is_verified_value = normalized_data.get("是否已核验", False)
        if isinstance(is_verified_value, str):
            is_verified = is_verified_value.strip().lower() in {
                "1",
                "true",
                "yes",
                "y",
            }
        else:
            is_verified = bool(is_verified_value)

        adapter["notice_type"] = get_notice_type_code(notice_type_name)
        adapter["publish_time"] = publish_time
        adapter["crawl_time"] = crawl_time
        adapter["detail_url"] = detail_url
        adapter["snapshot_path"] = snapshot_path
        adapter["snapshot_sha256"] = snapshot_sha256
        adapter["raw_text"] = raw_text
        adapter["parse_status"] = parse_status
        adapter["fingerprint"] = fingerprint
        adapter["extraction_model"] = extraction_model
        adapter["extraction_version"] = extraction_version
        adapter["is_verified"] = is_verified
        adapter["attachments"] = attachments
        adapter["data"] = normalized_data
        missing_fields = get_missing_fields(
            notice_type_name,
            normalized_data,
            include_optional=False,
        )
        if not self.crawler.settings.getbool("NOTICE_SNAPSHOT_ENABLED", True):
            missing_fields = [
                field
                for field in missing_fields
                if field not in {"HTML快照路径", "HTML快照SHA256"}
            ]
        adapter["missing_fields"] = missing_fields

        if not adapter.get("title"):
            spider.logger.warning(
                "公告标题为空：platform=%s notice_id=%s type=%s",
                adapter.get("platform"),
                adapter.get("notice_id"),
                notice_type_name,
            )

        if adapter["missing_fields"]:
            spider.logger.debug(
                "字段缺失：notice_id=%s type=%s fields=%s",
                adapter.get("notice_id"),
                notice_type_name,
                adapter["missing_fields"],
            )

        return item


class AiHtmlExtractionPipeline:
    """使用 AI 补充规则解析后仍为空的公告字段。

    Pipeline 位于字段规范化之后、文件导出之前。OpenAI 兼容 API 的同步调用
    放入 Twisted 线程池，避免阻塞 Scrapy 下载与响应解析。AI 返回值只写入空字段，
    随后再次使用统一 Schema 做类型转换和缺失字段统计。
    """

    # 这些内容来自请求元数据、框架或快照流程，不应由 AI 猜测。
    NON_AI_FIELDS = frozenset({*SYSTEM_FIELDS, "发布日期", "发布网站"})

    @classmethod
    def from_crawler(cls, crawler):
        config = AiExtractionConfig.from_settings(crawler.settings)
        service = None
        initialization_error = ""
        if config.enabled:
            try:
                service = AiHtmlExtractionService(config)
            except Exception as exc:
                initialization_error = f"{type(exc).__name__}: {exc}"
                if config.api_key:
                    initialization_error = initialization_error.replace(
                        config.api_key,
                        "***",
                    )
                if config.fail_on_error:
                    raise
        obj = cls(
            config=config,
            service=service,
            initialization_error=initialization_error,
        )
        obj.crawler = crawler
        return obj

    def __init__(
        self,
        *,
        config: AiExtractionConfig,
        service: AiHtmlExtractionService | None = None,
        initialization_error: str = "",
    ) -> None:
        self.config = config
        self.service = service
        self.initialization_error = initialization_error
        self._unavailable_logged = False

    @staticmethod
    def _is_empty(value: Any) -> bool:
        return value is None or value == "" or value == [] or value == {}

    def _inc_stat(self, key: str, count: int = 1) -> None:
        stats = getattr(self.crawler, "stats", None)
        if stats is not None:
            stats.inc_value(key, count=count)

    def _target_fields(
        self,
        notice_type: str,
        data: Mapping[str, Any],
        adapter: ItemAdapter,
    ) -> list[str]:
        if self.config.include_optional_fields:
            missing = get_missing_fields(
                notice_type,
                data,
                include_optional=True,
            )
        else:
            missing = list(adapter.get("missing_fields") or [])
            if not missing:
                missing = get_missing_fields(
                    notice_type,
                    data,
                    include_optional=False,
                )
        candidates = [field for field in missing if field not in self.NON_AI_FIELDS]
        crawler = getattr(self, "crawler", None)
        spider = getattr(crawler, "spider", None)
        selector = getattr(spider, "select_ai_extract_fields", None)
        if callable(selector):
            selected = selector(notice_type, candidates, data)
            allowed = set(candidates)
            return [
                field
                for field in dict.fromkeys(selected or [])
                if field in allowed
            ]
        return candidates

    def process_item(self, item):
        if not self.config.enabled:
            return item

        spider = self.crawler.spider
        if self.service is None:
            self._inc_stat("ai/html/unavailable")
            if not self._unavailable_logged:
                spider.logger.error(
                    "AI HTML提取已启用但服务不可用：%s",
                    self.initialization_error or "未知初始化错误",
                )
                self._unavailable_logged = True
            return item

        adapter = ItemAdapter(item)
        notice_type = normalize_notice_type(adapter.get("notice_type"))
        data = dict(adapter.get("data") or {})
        fields = self._target_fields(notice_type, data, adapter)
        if not fields:
            self._inc_stat("ai/html/skipped_no_fields")
            return item

        source_text = str(adapter.get("raw_text") or "").strip()
        if not source_text:
            source_text = html_to_text(adapter.get("raw_html"))
        if not source_text:
            self._inc_stat("ai/html/skipped_no_text")
            spider.logger.debug(
                "跳过AI提取，公告正文为空：notice_id=%s",
                adapter.get("notice_id"),
            )
            return item

        self._inc_stat("ai/html/items_submitted")
        deferred = deferToThread(
            self.service.extract,
            notice_type=notice_type,
            title=str(adapter.get("title") or ""),
            fields=fields,
            text=source_text,
        )
        deferred.addCallbacks(
            self._apply_result,
            self._handle_failure,
            callbackArgs=(item,),
            errbackArgs=(item,),
        )
        return deferred

    def _apply_result(self, result: AiExtractionResult, item):
        adapter = ItemAdapter(item)
        notice_type = normalize_notice_type(adapter.get("notice_type"))
        data = dict(adapter.get("data") or {})
        field_meta = dict(adapter.get("field_meta") or {})

        self._inc_stat("ai/html/api_attempts", result.attempts)
        self._inc_stat("ai/html/prompt_tokens", result.prompt_tokens)
        self._inc_stat("ai/html/completion_tokens", result.completion_tokens)
        self._inc_stat("ai/html/total_tokens", result.total_tokens)

        if not result.success:
            self._inc_stat("ai/html/failed")
            field_meta["ai_extraction"] = {
                "status": "FAILED",
                "model": self.config.model,
                "requested_fields": result.requested_fields,
                "filled_fields": [],
                "error": result.error,
                "attempts": result.attempts,
            }
            adapter["field_meta"] = field_meta
            self.crawler.spider.logger.warning(
                "AI字段提取失败：notice_id=%s error=%s",
                adapter.get("notice_id"),
                result.error,
            )
            if self.config.fail_on_error:
                raise RuntimeError(result.error or "AI字段提取失败")
            return item

        candidate_fields: list[str] = []
        for field, value in result.values.items():
            if field not in result.requested_fields:
                continue
            if not self._is_empty(data.get(field)) or self._is_empty(value):
                continue
            data[field] = value
            candidate_fields.append(field)

        normalized_data = canonicalize_notice_data(notice_type, data)
        # 日期等字段若无法转换为数据库兼容类型，会重新变为空；不能算作成功补全。
        filled_fields = [
            field
            for field in candidate_fields
            if not self._is_empty(normalized_data.get(field))
        ]
        if filled_fields:
            previous_model = str(adapter.get("extraction_model") or "RULE").strip()
            ai_marker = f"AI:{self.config.model}"
            combined_model = (
                previous_model
                if ai_marker in previous_model
                else f"{previous_model}+{ai_marker}"
            )
            adapter["extraction_model"] = combined_model
            self._inc_stat("ai/html/items_filled")
            self._inc_stat("ai/html/fields_filled", len(filled_fields))
        else:
            self._inc_stat("ai/html/items_no_values")

        field_meta["ai_extraction"] = {
            "status": "SUCCESS",
            "model": self.config.model,
            "requested_fields": result.requested_fields,
            "filled_fields": filled_fields,
            "input_chars": result.input_chars,
            "attempts": result.attempts,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
        }
        adapter["field_meta"] = field_meta
        adapter["data"] = normalized_data
        adapter["missing_fields"] = get_missing_fields(
            notice_type,
            normalized_data,
            include_optional=False,
        )
        self._inc_stat("ai/html/success")
        return item

    def _handle_failure(self, failure, item):
        self._inc_stat("ai/html/thread_failure")
        self.crawler.spider.logger.error(
            "AI提取线程异常：notice_id=%s error=%s",
            ItemAdapter(item).get("notice_id"),
            failure.getErrorMessage(),
        )
        if self.config.fail_on_error:
            failure.raiseException()
        return item


class NoticeMultiFormatPipeline:
    """按网站和公告类型同时输出 CSV 与 JSON。

    目录结构：
        output/<网站代码>/
        ├── csv/<公告类型>.csv
        ├── json/<公告类型>.json
        └── snapshots/<公告类型>/*.html

    JSON 与 CSV 保持同一套主字段。JSON 额外带 `_trace` 溯源包；CSV 不加入
    原始载荷和 HTML，避免改变既有表头及产生超大单元格。

    CSV 和 JSON 均以追加方式保存，不在 Spider 启动时清空。相同公告身份与
    内容指纹由 NoticeDedupPipeline 拦截；内容变化会追加为新版本。
    """

    META_COLUMNS = (
        "平台名称",
        "平台代码",
        "公告ID",
        "公告类型",
        "公告子类型",
        "公告标题",
        "发布时间",
    )

    META_ITEM_KEYS = {
        "平台名称": "platform",
        "平台代码": "platform_code",
        "公告ID": "notice_id",
        "公告类型": "notice_type",
        "公告子类型": "notice_subtype",
        "公告标题": "title",
        "发布时间": "publish_time",
    }

    # 这些字段是数据库/传输元数据，只在导出记录中追加一次；它们不属于
    # ANNOUNCEMENT_SCHEMAS，也不会进入 Mongo notice_extractions.extractedFields。
    STORAGE_ITEM_KEYS = {
        "公告正文": "raw_text",
        "解析状态": "parse_status",
        "内容指纹": "fingerprint",
        "抽取方式": "extraction_model",
        "抽取版本": "extraction_version",
        "是否已核验": "is_verified",
        "爬虫时间": "crawl_time",
        "详情页链接": "detail_url",
        "HTML快照路径": "snapshot_path",
        "HTML快照SHA256": "snapshot_sha256",
        "附件": "attachments",
    }

    @classmethod
    def from_crawler(cls, crawler):
        obj = cls(
            output_root=Path(crawler.settings.get("NOTICE_OUTPUT_ROOT", "output")),
            include_meta=crawler.settings.getbool(
                "NOTICE_EXPORT_INCLUDE_META",
                True,
            ),
            include_diagnostics=crawler.settings.getbool(
                "NOTICE_EXPORT_DIAGNOSTICS",
                True,
            ),
            create_empty_files=crawler.settings.getbool(
                "NOTICE_EXPORT_EMPTY_FILES",
                False,
            ),
            include_trace=crawler.settings.getbool(
                "NOTICE_EXPORT_TRACE",
                True,
            ),
        )
        obj.crawler = crawler
        return obj

    def __init__(
        self,
        output_root: Path,
        include_meta: bool = True,
        include_diagnostics: bool = True,
        create_empty_files: bool = False,
        include_trace: bool = True,
    ):
        self.output_root = output_root
        self.include_meta = include_meta
        self.include_diagnostics = include_diagnostics
        self.create_empty_files = create_empty_files
        self.include_trace = include_trace
        self.site_dir: Path | None = None
        self.csv_dir: Path | None = None
        self.json_dir: Path | None = None
        self._csv_files: dict[str, Any] = {}
        self._csv_writers: dict[str, csv.DictWriter] = {}
        self._json_paths: dict[str, Path] = {}
        self._json_has_records: dict[str, bool] = {}

    def open_spider(self):
        spider = self.crawler.spider
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.site_dir = _ensure_site_context(spider, self.output_root)
        self.csv_dir = self.site_dir / "csv"
        self.json_dir = self.site_dir / "json"
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.json_dir.mkdir(parents=True, exist_ok=True)

        if self.create_empty_files:
            for notice_type in ANNOUNCEMENT_TYPES:
                self._get_csv_writer(notice_type)
                self._get_json_path(notice_type)

    def close_spider(self):
        for file_object in self._csv_files.values():
            file_object.close()

        self._csv_files.clear()
        self._csv_writers.clear()
        self._json_paths.clear()
        self._json_has_records.clear()

    @staticmethod
    def _serialize_csv(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(_to_json_compatible(value), ensure_ascii=False)
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="milliseconds")
        if isinstance(value, date):
            return value.isoformat()
        return value

    def _fieldnames(self, notice_type: str) -> list[str]:
        """构造表头，并保证附件为最后一列。"""

        result: list[str] = []
        if self.include_meta:
            result.extend(self.META_COLUMNS)

        notice_fields = list(get_notice_fields(notice_type))
        business_fields = [
            field for field in notice_fields if field not in SYSTEM_FIELDS
        ]
        result.extend(business_fields)

        if self.include_diagnostics:
            result.append("缺失字段")

        result.extend(SYSTEM_FIELDS)
        return result

    def _get_csv_writer(self, notice_type: str) -> csv.DictWriter:
        if notice_type in self._csv_writers:
            return self._csv_writers[notice_type]
        if self.csv_dir is None:
            raise RuntimeError("CSV目录尚未初始化")

        basename = TYPE_OUTPUT_BASENAMES[notice_type]
        path = self.csv_dir / f"{basename}.csv"
        fieldnames = self._fieldnames(notice_type)
        has_content = path.exists() and path.stat().st_size > 0
        if has_content:
            with path.open("r", encoding="utf-8-sig", newline="") as existing:
                reader = csv.reader(existing)
                existing_header = next(reader, [])
            if existing_header != fieldnames:
                raise RuntimeError(
                    "现有CSV表头与当前Schema不一致，拒绝覆盖或错误追加："
                    f"{path}"
                )
        file_object = path.open(
            "a",
            encoding="utf-8" if has_content else "utf-8-sig",
            newline="",
        )
        writer = csv.DictWriter(
            file_object,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        if not has_content:
            writer.writeheader()
            file_object.flush()

        self._csv_files[notice_type] = file_object
        self._csv_writers[notice_type] = writer
        return writer

    def _get_json_path(self, notice_type: str) -> Path:
        if notice_type in self._json_paths:
            return self._json_paths[notice_type]
        if self.json_dir is None:
            raise RuntimeError("JSON目录尚未初始化")

        basename = TYPE_OUTPUT_BASENAMES[notice_type]
        path = self.json_dir / f"{basename}.json"
        if path.exists() and path.stat().st_size > 0:
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"现有JSON无法读取，拒绝覆盖：{path}: {exc}"
                ) from exc
            if not isinstance(rows, list):
                raise RuntimeError(f"现有JSON不是数组，拒绝覆盖：{path}")
            has_records = bool(rows)
        else:
            path.write_text("[\n]\n", encoding="utf-8")
            has_records = False

        self._json_paths[notice_type] = path
        self._json_has_records[notice_type] = has_records
        return path

    def _append_json_record(
        self,
        notice_type: str,
        record: Mapping[str, Any],
    ) -> None:
        """只替换数组结尾的 ``]``，追加后文件仍是合法 JSON。"""

        path = self._get_json_path(notice_type)
        content = path.read_bytes()
        closing_position = content.rfind(b"]")
        if closing_position < 0:
            raise RuntimeError(f"现有JSON缺少数组结束符，拒绝覆盖：{path}")
        serialized = json.dumps(
            _to_json_compatible(record),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        separator = b",\n" if self._json_has_records[notice_type] else b"\n"
        with path.open("r+b") as file_object:
            file_object.seek(closing_position)
            file_object.truncate()
            file_object.write(separator)
            file_object.write(serialized)
            file_object.write(b"\n]\n")
            file_object.flush()
        self._json_has_records[notice_type] = True

    def _build_record(self, adapter: ItemAdapter, notice_type: str) -> dict[str, Any]:
        record: dict[str, Any] = {}

        if self.include_meta:
            for column, item_key in self.META_ITEM_KEYS.items():
                record[column] = adapter.get(item_key, "")

        data = adapter.get("data") or {}
        for field in get_notice_fields(notice_type):
            value = data.get(field, "")
            record[field] = value

        if self.include_diagnostics:
            record["缺失字段"] = adapter.get("missing_fields") or []

        for field, item_key in self.STORAGE_ITEM_KEYS.items():
            value = adapter.get(item_key)
            if field == "附件" and value in (None, ""):
                value = []
            record[field] = value

        return record

    def _build_trace(self, adapter: ItemAdapter) -> dict[str, Any]:
        """构造与 MongoDB raw_notices 字段一一对应的溯源包。"""

        raw_data = adapter.get("raw_data")
        payload = raw_data if isinstance(raw_data, Mapping) else {
            "value": raw_data,
        } if raw_data is not None else {
            "sourceNoticeId": str(adapter.get("notice_id") or ""),
            "sourceUrl": str(adapter.get("detail_url") or ""),
        }
        raw_html = _raw_html_text(adapter.get("raw_html"))
        raw_text = str(adapter.get("raw_text") or "") or None
        field_meta = dict(adapter.get("field_meta") or {})
        extraction_version = str(adapter.get("extraction_version") or "") or None
        return {
            "schemaVersion": TRACE_SCHEMA_VERSION,
            "noticeSchemaVersion": NOTICE_SCHEMA_VERSION,
            "payload": payload,
            "rawHtml": raw_html,
            "rawText": raw_text,
            "responseMetadata": dict(adapter.get("response_metadata") or {}),
            "crawlerVersion": extraction_version,
            "extractionModel": str(adapter.get("extraction_model") or "") or None,
            "extractionVersion": extraction_version,
            "fieldMeta": field_meta,
            "capturedAt": adapter.get("crawl_time"),
            # 这些值在 MySQL/MongoDB 中大多有正式字段；同时保留一份导出时
            # 视图，覆盖 notice_subtype、缺失字段和本地快照信息等诊断字段。
            "exportMetadata": {
                "platformName": str(adapter.get("platform") or "") or None,
                "platformCode": str(adapter.get("platform_code") or "") or None,
                "sourceNoticeId": str(adapter.get("notice_id") or "") or None,
                "noticeType": str(adapter.get("notice_type") or "") or None,
                "noticeSubtype": str(adapter.get("notice_subtype") or "") or None,
                "title": str(adapter.get("title") or "") or None,
                "publishTime": adapter.get("publish_time"),
                "detailUrl": str(adapter.get("detail_url") or "") or None,
                "parseStatus": str(adapter.get("parse_status") or "") or None,
                "isVerified": bool(adapter.get("is_verified")),
                "missingFields": list(adapter.get("missing_fields") or []),
                "snapshotPath": str(adapter.get("snapshot_path") or "") or None,
                "snapshotSha256": str(adapter.get("snapshot_sha256") or "") or None,
                "attachments": list(adapter.get("attachments") or []),
            },
            "integrity": {
                "contentFingerprint": str(adapter.get("fingerprint") or "") or None,
                "payloadSha256": _sha256_json(payload),
                "rawHtmlSha256": hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
                if raw_html else None,
                "rawTextSha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                if raw_text else None,
            },
        }

    def _build_json_record(
        self,
        adapter: ItemAdapter,
        notice_type: str,
        record: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = dict(record or self._build_record(adapter, notice_type))
        if self.include_trace:
            result[TRACE_FIELD] = self._build_trace(adapter)
        return result

    def process_item(self, item):
        adapter = ItemAdapter(item)
        notice_type = normalize_notice_type(adapter.get("notice_type"))
        record = self._build_record(adapter, notice_type)
        json_record = self._build_json_record(adapter, notice_type, record)

        csv_writer = self._get_csv_writer(notice_type)
        self._get_json_path(notice_type)
        csv_row = {
            key: self._serialize_csv(value)
            for key, value in record.items()
        }
        field_meta = dict(adapter.get("field_meta") or {})
        dedup = field_meta.get("_dedup") or {}
        store = getattr(self.crawler, "_notice_dedup_stores", {}).get(
            str(adapter.get("platform_code") or self.crawler.spider.platform_code)
        )

        try:
            self._append_json_record(notice_type, json_record)
            csv_writer.writerow(csv_row)
            self._csv_files[notice_type].flush()
            if store is not None and dedup:
                store.commit(
                    identity=dedup["identity"],
                    content_fingerprint=dedup["content_fingerprint"],
                    platform_code=str(adapter.get("platform_code") or ""),
                    notice_id=str(adapter.get("notice_id") or ""),
                    detail_url=str(adapter.get("detail_url") or ""),
                    list_fingerprint=str(dedup.get("list_fingerprint") or ""),
                )
        except Exception:
            if store is not None and dedup:
                store.release(
                    dedup.get("identity", ""),
                    dedup.get("content_fingerprint", ""),
                )
            raise

        self.crawler.stats.inc_value("export/appended_versions")

        return item
