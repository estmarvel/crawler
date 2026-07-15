#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天启 IP 代理池封装

默认用途：
- 从天启 IP API 提取短效代理
- 缓存在本地内存队列中
- 给 requests 直接返回 proxies 参数
- 请求失败时可 mark_bad(proxy)
- 坏代理连续失败达到阈值后自动移除
- 支持查看代理池状态 stats()
- 支持手动刷新 refresh()

安全建议：
- 不建议把 secret/sign 硬编码到源码里
- 默认从环境变量读取：
    TIANQI_SECRET
    TIANQI_SIGN
- 如果你非要硬编码，也可以在 ProxyPool(...) 初始化时传入 secret 和 sign

使用示例：
    from proxy_pool import ProxyPool, ProxyPoolEmptyError

    pool = ProxyPool(num=10, time=3, port=2)

    try:
        proxy = pool.get_proxy()
        print(proxy)
    except ProxyPoolEmptyError as e:
        print("无可用代理：", e)
"""

import os
import time
import random
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


class ProxyPoolError(Exception):
    """代理池基础异常。"""
    pass


class ProxyPoolEmptyError(ProxyPoolError):
    """代理池为空，且刷新后仍无可用代理。"""
    pass


class ProxyAPIError(ProxyPoolError):
    """天启 API 返回异常。"""
    pass


@dataclass
class ProxyItem:
    ip: str
    port: str
    scheme: str = "http"
    expire_ts: float = 0.0
    raw: Optional[Dict[str, Any]] = None

    @property
    def url(self) -> str:
        """
        requests 的代理 URL。
        注意：即使是 HTTPS 代理，requests 里一般也写 http://ip:port。
        """
        return f"http://{self.ip}:{self.port}"

    @property
    def key(self) -> str:
        return f"{self.ip}:{self.port}"

    def to_requests_proxy(self) -> Dict[str, str]:
        return {
            "http": self.url,
            "https": self.url,
        }

    def is_expired(self, safety_seconds: int = 20) -> bool:
        """
        expire_ts=0 表示不知道过期时间。
        如果知道过期时间，则提前 safety_seconds 秒视为过期，避免刚拿到就失效。
        """
        if not self.expire_ts:
            return False
        return time.time() >= self.expire_ts - safety_seconds


class ProxyPool:
    """
    天启短效代理池。

    默认参数等价于：
    http://api.tianqiip.com/getip?secret=xxx&num=10&type=json&port=2&time=3&mr=1&sign=xxx

    参数说明：
    - num: 每次提取数量，1~200
    - time: 有效期，只支持 3、5、10、15
    - port: 1 HTTP，2 HTTPS，3 SOCKS5
    - mr: 是否去重，1 去重
    - min_size: 池内代理数量低于这个值时自动刷新
    - max_fail_count: 同一个代理连续失败达到这个次数后移除
    - api_calls_limit: 本地保护用的最大 API 调用次数，不代表天启真实套餐上限
    """

    API_URL = "http://api.tianqiip.com/getip"

    def __init__(
        self,
        secret: Optional[str] = None,
        sign: Optional[str] = None,
        num: int = 10,
        time: int = 3,
        port: int = 2,
        mr: int = 1,
        min_size: int = 3,
        max_fail_count: int = 2,
        api_calls_limit: int = 200,
        request_timeout: int = 15,
        auto_refresh: bool = True,
        **extra_params: Any,
    ) -> None:
        self.secret = secret or os.getenv("TIANQI_SECRET", "").strip()
        self.sign = sign or os.getenv("TIANQI_SIGN", "").strip()

        self.default_num = int(num)
        self.default_time = int(time)
        self.default_port = int(port)
        self.default_mr = int(mr)
        self.min_size = int(min_size)
        self.max_fail_count = int(max_fail_count)
        self.api_calls_limit = int(api_calls_limit)
        self.request_timeout = int(request_timeout)
        self.auto_refresh = bool(auto_refresh)
        self.extra_params = dict(extra_params or {})

        self._lock = threading.RLock()
        self._items: List[ProxyItem] = []
        self._bad_count: Dict[str, int] = {}
        self._bad_total = 0
        self._api_calls_used = 0

        self._validate_defaults()

    def _validate_defaults(self) -> None:
        if not self.secret:
            raise ProxyAPIError(
                "缺少天启 secret。请执行：export TIANQI_SECRET='你的secret'，"
                "或者 ProxyPool(secret='...', sign='...') 初始化传入。"
            )
        if not self.sign:
            raise ProxyAPIError(
                "缺少天启 sign。请执行：export TIANQI_SIGN='你的sign'，"
                "或者 ProxyPool(secret='...', sign='...') 初始化传入。"
            )
        self._validate_num(self.default_num)
        self._validate_time(self.default_time)
        self._validate_port(self.default_port)

    @staticmethod
    def _validate_num(num: int) -> None:
        if not (1 <= int(num) <= 200):
            raise ValueError("num 必须在 1~200 之间")

    @staticmethod
    def _validate_time(time_value: int) -> None:
        if int(time_value) not in {3, 5, 10, 15}:
            raise ValueError("time 只支持 3、5、10、15")

    @staticmethod
    def _validate_port(port: int) -> None:
        if int(port) not in {1, 2, 3}:
            raise ValueError("port 只支持 1 HTTP，2 HTTPS，3 SOCKS5")

    def _build_params(self, **overrides: Any) -> Dict[str, Any]:
        num = int(overrides.pop("num", self.default_num))
        time_value = int(overrides.pop("time", self.default_time))
        port = int(overrides.pop("port", self.default_port))
        mr = int(overrides.pop("mr", self.default_mr))

        self._validate_num(num)
        self._validate_time(time_value)
        self._validate_port(port)

        params: Dict[str, Any] = {
            "secret": self.secret,
            "sign": self.sign,
            "num": num,
            "type": "json",
            "port": port,
            "time": time_value,
            "mr": mr,
        }

        merged_extra = dict(self.extra_params)
        merged_extra.update(overrides)

        # 只添加非空参数，避免传 None 或空字符串给接口
        for key, value in merged_extra.items():
            if value is not None and value != "":
                params[key] = value

        return params

    @staticmethod
    def _parse_expire_ts(raw_item: Dict[str, Any]) -> float:
        """
        天启只有开启 ts=1 才可能返回 expire。
        这里兼容常见格式：
        - 2026-06-29 12:34:56
        - 2026/06/29 12:34:56
        解析失败则返回 0，表示未知过期时间。
        """
        expire = str(raw_item.get("expire", "") or "").strip()
        if not expire:
            return 0.0

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return time.mktime(time.strptime(expire, fmt))
            except Exception:
                pass

        return 0.0

    def _fetch_from_api(self, **params_override: Any) -> List[ProxyItem]:
        if self._api_calls_used >= self.api_calls_limit:
            raise ProxyAPIError(
                f"本地 API 调用次数已达到限制：{self.api_calls_used}/{self.api_calls_limit}"
            )

        params = self._build_params(**params_override)

        try:
            resp = requests.get(
                self.API_URL,
                params=params,
                timeout=self.request_timeout,
            )
            resp.encoding = "utf-8"
        except Exception as e:
            raise ProxyAPIError(f"请求天启 API 失败：{type(e).__name__}: {e}") from e

        self._api_calls_used += 1

        if resp.status_code != 200:
            raise ProxyAPIError(f"天启 API HTTP 状态异常：status={resp.status_code}, body={resp.text[:300]}")

        try:
            data = resp.json()
        except Exception as e:
            raise ProxyAPIError(f"天启 API 返回不是合法 JSON：{resp.text[:500]}") from e

        code = data.get("code")
        if code != 1000:
            raise ProxyAPIError(f"天启 API 返回错误：code={code}, body={data}")

        rows = data.get("data") or []
        if not isinstance(rows, list):
            raise ProxyAPIError(f"天启 API data 字段格式异常：{data}")

        items: List[ProxyItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue

            ip = str(row.get("ip", "") or "").strip()
            port = str(row.get("port", "") or "").strip()

            if not ip or not port:
                continue

            item = ProxyItem(
                ip=ip,
                port=port,
                scheme="http",
                expire_ts=self._parse_expire_ts(row),
                raw=row,
            )
            items.append(item)

        if not items:
            raise ProxyAPIError(f"天启 API 成功返回但没有可用 IP：{data}")

        return items

    def _drop_expired_locked(self) -> None:
        self._items = [item for item in self._items if not item.is_expired()]

    def _dedupe_extend_locked(self, new_items: List[ProxyItem]) -> None:
        existing = {item.key for item in self._items}
        for item in new_items:
            if item.key in existing:
                continue
            self._items.append(item)
            existing.add(item.key)

    def refresh(self, clear: bool = True, **params_override: Any) -> int:
        """
        手动刷新代理池。

        默认 clear=True：
        - 清空当前代理池
        - 重新从 API 提取一批代理

        返回新增后的代理池数量。
        """
        new_items = self._fetch_from_api(**params_override)

        with self._lock:
            if clear:
                self._items.clear()
            self._dedupe_extend_locked(new_items)
            self._drop_expired_locked()
            return len(self._items)

    def ensure_available(self, **params_override: Any) -> None:
        """
        确保代理池有足够可用代理。
        """
        with self._lock:
            self._drop_expired_locked()
            current_size = len(self._items)

        if not self.auto_refresh:
            if current_size <= 0:
                raise ProxyPoolEmptyError("代理池为空，且 auto_refresh=False")
            return

        if current_size >= self.min_size:
            return

        # 池子太小，追加刷新，不清空现有代理
        try:
            self.refresh(clear=False, **params_override)
        except ProxyAPIError:
            # 如果池里还有可用代理，不因为刷新失败直接中断
            with self._lock:
                self._drop_expired_locked()
                if self._items:
                    return
            raise

        with self._lock:
            self._drop_expired_locked()
            if not self._items:
                raise ProxyPoolEmptyError("刷新后代理池仍为空")

    def get_proxy(self, **params_override: Any) -> Dict[str, str]:
        """
        获取一个代理，返回格式可直接传给 requests：

        {
            "http": "http://ip:port",
            "https": "http://ip:port"
        }

        支持临时覆盖提取参数：
            pool.get_proxy(num=20, time=5, port=2)
        """
        self.ensure_available(**params_override)

        with self._lock:
            self._drop_expired_locked()
            if not self._items:
                raise ProxyPoolEmptyError("代理池为空")

            # 随机选择，避免每次都打同一个代理
            item = random.choice(self._items)
            return item.to_requests_proxy()

    def mark_bad(self, proxy: Optional[Dict[str, str]]) -> None:
        """
        标记坏代理。
        同一个代理连续失败达到 max_fail_count 后，从池中移除。
        """
        if not proxy:
            return

        proxy_url = proxy.get("https") or proxy.get("http") or ""
        proxy_url = proxy_url.replace("http://", "").replace("https://", "").strip()
        if not proxy_url:
            return

        key = proxy_url

        with self._lock:
            self._bad_count[key] = self._bad_count.get(key, 0) + 1
            self._bad_total += 1

            if self._bad_count[key] >= self.max_fail_count:
                self._items = [item for item in self._items if item.key != key]

    def stats(self) -> Dict[str, Any]:
        """
        查看代理池状态。
        """
        with self._lock:
            self._drop_expired_locked()

            return {
                "available": len(self._items),
                "bad_total": self._bad_total,
                "bad_unique": len(self._bad_count),
                "api_calls_used": self._api_calls_used,
                "api_calls_limit": self.api_calls_limit,
                "min_size": self.min_size,
                "default_num": self.default_num,
                "default_time": self.default_time,
                "default_port": self.default_port,
                "auto_refresh": self.auto_refresh,
            }

    def clear(self) -> None:
        """
        清空代理池。
        """
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            self._drop_expired_locked()
            return len(self._items)


if __name__ == "__main__":
    """
    简单自测：
        export TIANQI_SECRET='你的secret'
        export TIANQI_SIGN='你的sign'
        python proxy_pool.py
    """
    pool = ProxyPool(num=10, time=3, port=2)

    print("正在提取代理...")
    pool.refresh(num=10, time=3, port=2)

    print("代理池状态：")
    print(pool.stats())

    print("随机取一个代理：")
    print(pool.get_proxy())
