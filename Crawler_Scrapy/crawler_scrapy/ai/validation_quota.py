"""Validation-only per-announcement-type quota placed before the AI pipeline."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from itemadapter import ItemAdapter
from scrapy import signals
from scrapy.exceptions import DropItem
from twisted.internet import reactor

from crawler_scrapy.schemas.notice_fields import normalize_notice_type


class AiValidationQuotaPipeline:
    """Limit saved/model-reviewed samples without changing production crawls.

    ``NOTICE_VALIDATION_MAX_PER_TYPE=0`` keeps this pipeline as a no-op. A
    positive limit is only intended for isolated accuracy tests and is applied
    before hybrid AI, so over-quota records consume neither tokens nor output
    slots.
    """

    @classmethod
    def from_crawler(cls, crawler):
        instance = cls(
            crawler=crawler,
            limit=crawler.settings.getint("NOTICE_VALIDATION_MAX_PER_TYPE", 0),
            output_root=Path(
                crawler.settings.get("NOTICE_OUTPUT_ROOT", "output")
            ),
        )
        crawler.signals.connect(instance.spider_opened, signal=signals.spider_opened)
        return instance

    def __init__(
        self, *, crawler, limit: int, output_root: Path | None = None
    ) -> None:
        self.crawler = crawler
        self.limit = max(0, int(limit))
        self.output_root = output_root
        self.counts: dict[str, int] = defaultdict(int)
        self.expected: tuple[str, ...] = ()
        self._close_requested = False

    def spider_opened(self, spider) -> None:
        configured = getattr(spider, "ai_validation_quota_types", ())
        self.expected = tuple(
            str(value)
            for value in (configured or getattr(spider, "ai_extract_fields", {}))
        )
        if self.limit > 0 and self.output_root is not None:
            self._restore_existing_counts(spider)

    def _restore_existing_counts(self, spider) -> None:
        """Restore validation quotas from valid JSON exports after interruption.

        Scrapy ``JOBDIR`` restores pending requests, while this restores the
        pre-AI per-type counters.  Without both pieces, a resumed 50-sample
        validation could save another 50 records for a type that was partly
        completed before the interruption.
        """

        platform_code = str(
            getattr(spider, "platform_code", "")
            or getattr(spider, "name", "")
        ).strip()
        if not platform_code:
            return
        json_dir = self.output_root / platform_code / "json"
        if not json_dir.is_dir():
            return

        identities: dict[str, set[str]] = defaultdict(set)
        for json_path in sorted(json_dir.glob("*.json")):
            try:
                records = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                spider.logger.warning(
                    "恢复验收配额时跳过不可读JSON：%s (%s)", json_path, exc
                )
                continue
            if not isinstance(records, list):
                continue
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                key = self._quota_key_from_mapping(record, spider)
                if not key:
                    continue
                identity = self._record_identity(record, json_path, index)
                identities[key].add(identity)

        for key, values in identities.items():
            restored = len(values)
            self.counts[key] = restored
            self.crawler.stats.set_value(
                f"validation_quota/resumed/{key}", restored
            )
            self.crawler.stats.set_value(
                f"validation_quota/accepted/{key}", restored
            )
        if identities:
            spider.logger.info(
                "从现有JSON恢复验收配额：%s",
                ", ".join(
                    f"{key}={len(values)}"
                    for key, values in sorted(identities.items())
                ),
            )

    @staticmethod
    def _record_identity(record: dict[str, Any], path: Path, index: int) -> str:
        platform = str(
            record.get("平台代码") or record.get("platform_code") or ""
        ).strip()
        notice_id = str(
            record.get("公告ID") or record.get("notice_id") or ""
        ).strip()
        if notice_id:
            return f"{platform}|id:{notice_id}"
        detail_url = str(
            record.get("详情链接") or record.get("detail_url") or ""
        ).strip()
        if detail_url:
            return f"{platform}|url:{detail_url}"
        return f"{path}:{index}"

    @classmethod
    def _quota_key_from_mapping(cls, record: dict[str, Any], spider) -> str:
        if getattr(spider, "ai_validation_quota_by_source_category", False):
            subtype = str(
                record.get("公告子类型")
                or record.get("notice_subtype")
                or ""
            ).strip()
            return subtype.split(".", 1)[0]
        return normalize_notice_type(
            record.get("公告类型") or record.get("notice_type")
        )

    @staticmethod
    def _quota_key(adapter: ItemAdapter, spider) -> str:
        if getattr(spider, "ai_validation_quota_by_source_category", False):
            subtype = str(adapter.get("notice_subtype") or "").strip()
            return subtype.split(".", 1)[0]
        return normalize_notice_type(adapter.get("notice_type"))

    def process_item(self, item: Any):
        if self.limit <= 0:
            return item

        spider = self.crawler.spider
        adapter = ItemAdapter(item)
        key = self._quota_key(adapter, spider)
        if not key:
            return item
        if self.counts[key] >= self.limit:
            self.crawler.stats.inc_value(f"validation_quota/dropped/{key}")
            raise DropItem(f"validation quota reached: {key}={self.limit}")

        self.counts[key] += 1
        self.crawler.stats.set_value(
            f"validation_quota/accepted/{key}", self.counts[key]
        )
        if (
            self.expected
            and not self._close_requested
            and all(self.counts[value] >= self.limit for value in self.expected)
        ):
            self._close_requested = True
            reactor.callLater(
                0,
                self.crawler.engine.close_spider,
                spider,
                "validation_type_quota_reached",
            )
        return item
