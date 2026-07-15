"""天启短效代理池。

与旧版实现相比：
- 密钥只从参数或环境变量读取；
- 请求 ``ts=1`` 并在过期前主动移除；
- 请求 ``mr=1``，避免重复代理占用池容量；
- 网络拉取不持有代理列表锁；
- 没有代理时抛出异常，调用方不得回退直连。
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

import requests


logger = logging.getLogger(__name__)


class ProxyPoolError(RuntimeError):
    """代理池基础异常。"""


class ProxyPoolConfigurationError(ProxyPoolError):
    """代理池配置缺失或非法。"""


class ProxyPoolEmptyError(ProxyPoolError):
    """代理池无法提供代理。"""


@dataclass
class ProxyItem:
    url: str
    expire_ts: float = 0.0
    failures: int = 0

    def expired(self, safety_seconds: int) -> bool:
        return bool(self.expire_ts) and time.time() >= self.expire_ts - safety_seconds


class TianqiProxyPool:
    """线程安全的天启短效代理池。"""

    allowed_lifetimes = {3, 5, 10, 15}
    allowed_ports = {1, 2, 3}

    def __init__(
        self,
        *,
        secret: str | None = None,
        sign: str | None = None,
        api_url: str = "http://api.tianqiip.com/getip",
        num: int = 10,
        lifetime_minutes: int = 3,
        port_type: int = 2,
        min_size: int = 3,
        max_failures: int = 1,
        api_call_limit: int = 5,
        request_timeout: float = 15.0,
        expiry_safety_seconds: int = 20,
        session: requests.Session | None = None,
    ) -> None:
        self.secret = str(secret or os.getenv("TIANQI_SECRET", "")).strip()
        self.sign = str(sign or os.getenv("TIANQI_SIGN", "")).strip()
        self.api_url = str(api_url).strip()
        self.num = max(1, min(int(num), 200))
        self.lifetime_minutes = int(lifetime_minutes)
        self.port_type = int(port_type)
        self.min_size = max(1, int(min_size))
        self.max_failures = max(1, int(max_failures))
        self.api_call_limit = max(1, int(api_call_limit))
        self.request_timeout = max(1.0, float(request_timeout))
        self.expiry_safety_seconds = max(0, int(expiry_safety_seconds))
        self._session = session or requests.Session()

        if not self.secret or not self.sign:
            raise ProxyPoolConfigurationError(
                "缺少天启代理凭据，请设置 TIANQI_SECRET 和 TIANQI_SIGN"
            )
        if self.lifetime_minutes not in self.allowed_lifetimes:
            raise ProxyPoolConfigurationError(
                f"代理有效期只支持 {sorted(self.allowed_lifetimes)} 分钟"
            )
        if self.port_type not in self.allowed_ports:
            raise ProxyPoolConfigurationError("port_type 只支持 1、2、3")

        self._items: dict[str, ProxyItem] = {}
        self._bad_total = 0
        self._api_calls = 0
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()

    @staticmethod
    def _parse_expire(value: Any) -> float:
        raw = str(value or "").strip()
        if not raw:
            return 0.0
        if raw.isdigit():
            number = float(raw)
            return number / 1000 if number > 10_000_000_000 else number
        for date_format in (
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(raw, date_format).timestamp()
            except ValueError:
                continue
        return 0.0

    def _drop_expired_locked(self) -> int:
        expired = [
            url
            for url, item in self._items.items()
            if item.expired(self.expiry_safety_seconds)
        ]
        for url in expired:
            self._items.pop(url, None)
        return len(expired)

    def _usable_size(self) -> int:
        with self._lock:
            self._drop_expired_locked()
            return len(self._items)

    def _request_params(self) -> dict[str, Any]:
        return {
            "secret": self.secret,
            "sign": self.sign,
            "num": self.num,
            "type": "json",
            "port": self.port_type,
            "time": self.lifetime_minutes,
            "mr": 1,
            "ts": 1,
        }

    def _fetch_from_api(self) -> list[ProxyItem]:
        with self._lock:
            if self._api_calls >= self.api_call_limit:
                return []
            self._api_calls += 1
            call_number = self._api_calls

        logger.info(
            "请求天启代理 API：call=%s/%s num=%s lifetime=%smin",
            call_number,
            self.api_call_limit,
            self.num,
            self.lifetime_minutes,
        )
        try:
            response = self._session.get(
                self.api_url,
                params=self._request_params(),
                timeout=self.request_timeout,
                headers={"User-Agent": "CrawlerScrapy-ProxyPool/1.0"},
            )
            if response.status_code != 200:
                logger.error(
                    "天启代理 API HTTP异常：status=%s body=%r",
                    response.status_code,
                    response.text[:200],
                )
                return []
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            # requests 的异常文本可能包含完整查询串，必须遮蔽凭据。
            message = str(exc).replace(self.secret, "***").replace(self.sign, "***")
            logger.error("天启代理 API 请求失败：%s: %s", type(exc).__name__, message)
            return []

        if not isinstance(payload, Mapping) or str(payload.get("code")) != "1000":
            logger.error(
                "天启代理 API 业务失败：code=%r",
                payload.get("code") if isinstance(payload, Mapping) else None,
            )
            return []

        result: list[ProxyItem] = []
        for row in payload.get("data") or []:
            if not isinstance(row, Mapping):
                continue
            ip = str(row.get("ip") or "").strip()
            port = str(row.get("port") or "").strip()
            if not ip or not port:
                continue
            result.append(
                ProxyItem(
                    url=f"http://{ip}:{port}",
                    expire_ts=self._parse_expire(row.get("expire")),
                )
            )
        logger.info("天启代理 API 返回 %s 个有效格式代理", len(result))
        return result

    def _refill(self) -> None:
        """单线程补池；网络请求期间不占用代理列表锁。"""

        with self._fetch_lock:
            if self._usable_size() >= self.min_size:
                return
            fetched = self._fetch_from_api()
            if not fetched:
                return
            with self._lock:
                self._drop_expired_locked()
                for item in fetched:
                    if not item.expired(self.expiry_safety_seconds):
                        self._items[item.url] = item

    def get_proxy_url(self) -> str:
        """取得一个可用代理；获取失败时必须抛错，禁止调用方直连。"""

        if self._usable_size() < self.min_size:
            self._refill()
        with self._lock:
            self._drop_expired_locked()
            if not self._items:
                raise ProxyPoolEmptyError(
                    "天启代理池为空或已达到本次 API 调用上限，禁止回退直连"
                )
            return random.choice(tuple(self._items.values())).url

    def mark_bad(self, proxy_url: str | None) -> bool:
        """记录明确代理失败；达到阈值后移除，返回是否已移除。"""

        if not proxy_url:
            return False
        with self._lock:
            item = self._items.get(proxy_url)
            if item is None:
                return False
            item.failures += 1
            if item.failures < self.max_failures:
                return False
            self._items.pop(proxy_url, None)
            self._bad_total += 1
            return True

    def mark_good(self, proxy_url: str | None) -> None:
        if not proxy_url:
            return
        with self._lock:
            item = self._items.get(proxy_url)
            if item is not None:
                item.failures = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            expired_removed = self._drop_expired_locked()
            return {
                "available": len(self._items),
                "bad_total": self._bad_total,
                "expired_removed": expired_removed,
                "api_calls_used": self._api_calls,
                "api_calls_limit": self.api_call_limit,
            }
