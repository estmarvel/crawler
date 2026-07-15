"""爬取结果持久化与去重组件。"""

from crawler_scrapy.storage.dedup import (
    JsonNoticeDedupStore,
    build_content_fingerprint,
    build_list_fingerprint,
    build_notice_identity,
    get_notice_dedup_store,
)

__all__ = [
    "JsonNoticeDedupStore",
    "build_content_fingerprint",
    "build_list_fingerprint",
    "build_notice_identity",
    "get_notice_dedup_store",
]
