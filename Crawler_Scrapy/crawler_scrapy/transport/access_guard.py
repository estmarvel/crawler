"""固定公网出口直连模式的访问频率与封禁响应保护。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


class DirectAccessGuardMiddleware:
    """遇到 403/429 时提高下载槽延迟，连续出现则主动关闭 Spider。

    本组件不伪造身份、不绕过验证码，只用于在目标站已经明确限流或拒绝访问时
    及时降速、停爬，保护固定公网出口 IP。
    """

    def __init__(self, crawler) -> None:
        self.crawler = crawler
        self.block_statuses = set(
            crawler.settings.getlist("DIRECT_GUARD_HTTP_CODES", [403, 429])
        )
        self.consecutive_limit = max(
            1, crawler.settings.getint("DIRECT_GUARD_CONSECUTIVE_LIMIT", 2)
        )
        self.total_limit = max(
            1, crawler.settings.getint("DIRECT_GUARD_TOTAL_LIMIT", 4)
        )
        self.base_backoff = max(
            1.0, crawler.settings.getfloat("DIRECT_GUARD_BASE_BACKOFF", 30.0)
        )
        self.max_backoff = max(
            self.base_backoff,
            crawler.settings.getfloat("DIRECT_GUARD_MAX_BACKOFF", 300.0),
        )
        self._consecutive = defaultdict(int)
        self._total_blocks = 0

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    @staticmethod
    def _host(request) -> str:
        return (urlsplit(request.url).hostname or "unknown").lower()

    @staticmethod
    def _retry_after_seconds(response) -> float:
        raw = response.headers.get(b"Retry-After")
        if not raw:
            return 0.0
        text = raw.decode("ascii", errors="ignore").strip()
        try:
            return max(0.0, float(text))
        except ValueError:
            pass
        try:
            retry_at = parsedate_to_datetime(text)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _increase_slot_delay(self, request, delay: float) -> float:
        engine = getattr(self.crawler, "engine", None)
        downloader = getattr(engine, "downloader", None)
        if downloader is None:
            return delay
        slot_key = request.meta.get("download_slot")
        if slot_key is None:
            get_slot_key = getattr(downloader, "_get_slot_key", None)
            if callable(get_slot_key):
                slot_key = get_slot_key(request)
        slot = getattr(downloader, "slots", {}).get(slot_key)
        if slot is not None:
            slot.delay = max(float(getattr(slot, "delay", 0.0)), delay)
            return slot.delay
        return delay

    async def process_response(self, request, response):
        host = self._host(request)
        if response.status not in self.block_statuses:
            if response.status < 400:
                self._consecutive[host] = 0
            return response

        self._consecutive[host] += 1
        self._total_blocks += 1
        consecutive = self._consecutive[host]
        retry_after = self._retry_after_seconds(response)
        calculated = min(
            self.base_backoff * (2 ** (consecutive - 1)),
            self.max_backoff,
        )
        applied_delay = self._increase_slot_delay(
            request,
            min(max(retry_after, calculated), self.max_backoff),
        )

        stats = self.crawler.stats
        stats.inc_value(f"direct_guard/status/{response.status}")
        stats.set_value("direct_guard/current_delay", applied_delay)
        self.crawler.spider.logger.warning(
            "固定公网出口收到限制响应：host=%s status=%s consecutive=%s "
            "total=%s delay=%.1fs",
            host,
            response.status,
            consecutive,
            self._total_blocks,
            applied_delay,
        )

        if (
            consecutive >= self.consecutive_limit
            or self._total_blocks >= self.total_limit
        ):
            stats.inc_value("direct_guard/spider_closed")
            engine = getattr(self.crawler, "engine", None)
            if engine is not None:
                await engine.close_spider_async(
                    self.crawler.spider,
                    reason="direct_access_blocked",
                )
        return response
