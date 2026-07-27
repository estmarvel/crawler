"""Scrapy 强制代理 Downloader Middleware。"""

from __future__ import annotations

import base64
import os
from typing import Any
from urllib.parse import urlsplit

from scrapy import Request, signals
from scrapy.exceptions import IgnoreRequest
from scrapy.utils.defer import maybe_deferred_to_future
from scrapy.utils.response import response_status_message
from twisted.internet.threads import deferToThread

try:
    from scrapy.downloadermiddlewares.retry import get_retry_request
except ImportError:  # pragma: no cover - Scrapy 2.13+ 均提供
    get_retry_request = None

from crawler_scrapy.transport.proxy_pool import (
    ProxyPoolConfigurationError,
    ProxyPoolEmptyError,
    TianqiProxyPool,
)
from crawler_scrapy.transport.scrapy_compat import request_spider_close


class StaticProxyMiddleware:
    """强制使用一个固定的认证代理，配置缺失或失败时绝不回退直连。"""

    def __init__(
        self,
        crawler,
        *,
        endpoint: str,
        authorization: bytes,
        required: bool,
        retry_times: int,
    ) -> None:
        self.crawler = crawler
        self.endpoint = endpoint
        self.authorization = authorization
        self.required = required
        self.retry_times = max(0, retry_times)
        self._closing = False

    @classmethod
    def from_crawler(cls, crawler):
        settings = crawler.settings
        if not settings.getbool("STATIC_PROXY_ENABLED", False):
            raise ProxyPoolConfigurationError(
                "StaticProxyMiddleware 已注册但 STATIC_PROXY_ENABLED=False"
            )

        endpoint_env = str(
            settings.get("STATIC_PROXY_ENDPOINT_ENV", "HUAXIN_PROXY_ENDPOINT")
        ).strip()
        endpoint = str(os.getenv(endpoint_env, "") if endpoint_env else "").strip()
        if not endpoint:
            endpoint = str(settings.get("STATIC_PROXY_ENDPOINT", "") or "").strip()
        if endpoint and "://" not in endpoint:
            endpoint = f"http://{endpoint}"
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
            raise ProxyPoolConfigurationError(
                "固定代理地址无效，请配置 http://host:port 格式的 STATIC_PROXY_ENDPOINT"
            )
        # endpoint 中禁止内嵌凭据，避免它进入 Request.meta 和异常日志。
        if parsed.username or parsed.password:
            raise ProxyPoolConfigurationError(
                "固定代理地址不能内嵌账号密码，请改用代理凭据环境变量"
            )

        username_env = str(
            settings.get("STATIC_PROXY_USERNAME_ENV", "HUAXIN_PROXY_USERNAME")
        ).strip()
        password_env = str(
            settings.get("STATIC_PROXY_PASSWORD_ENV", "HUAXIN_PROXY_PASSWORD")
        ).strip()
        username = os.getenv(username_env, "").strip() if username_env else ""
        password = os.getenv(password_env, "").strip() if password_env else ""
        if settings.getbool("STATIC_PROXY_AUTH_REQUIRED", True) and (
            not username or not password
        ):
            raise ProxyPoolConfigurationError(
                f"缺少固定代理凭据，请设置 {username_env} 和 {password_env}"
            )

        token = base64.b64encode(f"{username}:{password}".encode("utf-8"))
        middleware = cls(
            crawler,
            endpoint=endpoint,
            authorization=b"Basic " + token,
            required=settings.getbool("STATIC_PROXY_REQUIRED", True),
            retry_times=settings.getint("STATIC_PROXY_RETRY_TIMES", 1),
        )
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
        return middleware

    @property
    def spider(self):
        return self.crawler.spider

    def _close_spider(self, reason: str) -> None:
        if self._closing:
            return
        self._closing = True
        if self.crawler.engine is not None:
            request_spider_close(
                self.crawler.engine,
                self.spider,
                reason,
            )

    @staticmethod
    def _is_auth_failure(exception: Exception) -> bool:
        """识别 HTTPS CONNECT 阶段被 Twisted 包装成异常的代理 407。"""

        message = str(exception).lower()
        return "407" in message and "proxy authentication required" in message

    def process_request(self, request: Request):
        request.meta["proxy"] = self.endpoint
        request.meta["_static_proxy"] = True
        request.headers[b"Proxy-Authorization"] = self.authorization
        return None

    async def process_response(self, request: Request, response):
        if response.status == 407:
            self.crawler.stats.inc_value("static_proxy/auth_failed")
            self._close_spider("static_proxy_auth_failed")
        elif response.status < 400:
            self.crawler.stats.inc_value("static_proxy/success")
        return response

    async def process_exception(self, request: Request, exception: Exception):
        if isinstance(exception, IgnoreRequest):
            return None
        if self._is_auth_failure(exception):
            self.crawler.stats.inc_value("static_proxy/auth_failed")
            # CONNECT 407 不会形成 Scrapy Response，必须在异常分支单独分类。
            # 设置为0可阻止后续 RetryMiddleware 对无效凭据反复尝试。
            request.meta["max_retry_times"] = 0
            self._close_spider("static_proxy_auth_failed")
            return None
        self.crawler.stats.inc_value("static_proxy/network_exception")
        retry = None
        if get_retry_request is not None:
            retry = get_retry_request(
                request,
                spider=self.spider,
                reason=exception,
                max_retry_times=self.retry_times,
                priority_adjust=-1,
            )
        if retry is not None:
            # Retry Request 会复制 meta/headers；再次明确设置，确保没有直连窗口。
            retry.meta["proxy"] = self.endpoint
            retry.meta["_static_proxy"] = True
            retry.headers[b"Proxy-Authorization"] = self.authorization
            return retry
        if self.required:
            self._close_spider("static_proxy_unavailable")
        return None

    def spider_closed(self, spider, reason: str) -> None:
        spider.logger.info(
            "固定代理关闭状态：reason=%s endpoint=%s",
            reason,
            self.endpoint,
        )


class TianqiProxyMiddleware:
    """为每个目标请求强制分配代理，不允许直连兜底。"""

    def __init__(self, crawler, pool: TianqiProxyPool, required: bool) -> None:
        self.crawler = crawler
        self.pool = pool
        self.required = required
        self._closing = False
        self.proxy_failure_statuses = set(
            crawler.settings.getlist(
                "TIANQI_PROXY_FAILURE_HTTP_CODES",
                [403, 407, 429, *range(430, 457)],
            )
        )
        self.retry_times = crawler.settings.getint("TIANQI_PROXY_RETRY_TIMES", 3)

    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool("TIANQI_PROXY_ENABLED", False):
            raise ProxyPoolConfigurationError(
                "TianqiProxyMiddleware 已注册但 TIANQI_PROXY_ENABLED=False"
            )
        pool = TianqiProxyPool(
            secret=crawler.settings.get("TIANQI_SECRET") or None,
            sign=crawler.settings.get("TIANQI_SIGN") or None,
            api_url=crawler.settings.get(
                "TIANQI_PROXY_API_URL",
                "http://api.tianqiip.com/getip",
            ),
            num=crawler.settings.getint("TIANQI_PROXY_NUM", 10),
            lifetime_minutes=crawler.settings.getint("TIANQI_PROXY_LIFETIME", 3),
            port_type=crawler.settings.getint("TIANQI_PROXY_PORT_TYPE", 2),
            min_size=crawler.settings.getint("TIANQI_PROXY_MIN_SIZE", 3),
            max_failures=crawler.settings.getint("TIANQI_PROXY_MAX_FAILURES", 1),
            api_call_limit=crawler.settings.getint("TIANQI_PROXY_API_CALL_LIMIT", 5),
            request_timeout=crawler.settings.getfloat("TIANQI_PROXY_API_TIMEOUT", 15.0),
            expiry_safety_seconds=crawler.settings.getint(
                "TIANQI_PROXY_EXPIRY_SAFETY_SECONDS",
                20,
            ),
        )
        middleware = cls(
            crawler,
            pool,
            required=crawler.settings.getbool("TIANQI_PROXY_REQUIRED", True),
        )
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
        return middleware

    @property
    def spider(self):
        return self.crawler.spider

    def _close_spider(self, reason: str) -> None:
        if self._closing:
            return
        self._closing = True
        if self.crawler.engine is not None:
            request_spider_close(
                self.crawler.engine,
                self.spider,
                reason,
            )

    async def process_request(self, request: Request):
        if request.meta.get("proxy"):
            return None
        try:
            proxy_url = await maybe_deferred_to_future(
                deferToThread(self.pool.get_proxy_url)
            )
        except (ProxyPoolConfigurationError, ProxyPoolEmptyError) as exc:
            self.crawler.stats.inc_value("proxy/pool_empty")
            self._close_spider("proxy_pool_empty")
            raise IgnoreRequest(
                f"无法取得代理，已终止任务且未发送目标请求：{exc}"
            ) from exc
        self._set_proxy(proxy_url, request)
        return None

    @staticmethod
    def _set_proxy(proxy_url: str, request: Request) -> None:
        if not proxy_url:
            raise ProxyPoolEmptyError("代理池返回空地址，禁止直连")
        request.meta["proxy"] = proxy_url
        request.meta["_tianqi_proxy"] = proxy_url
        return None

    def _retry_with_new_proxy(self, request: Request, reason: Any):
        proxy_url = request.meta.get("_tianqi_proxy") or request.meta.get("proxy")
        if self.pool.mark_bad(proxy_url):
            self.crawler.stats.inc_value("proxy/removed_bad")
        request.meta.pop("proxy", None)
        request.meta.pop("_tianqi_proxy", None)
        if get_retry_request is None:
            return None
        retry_request = get_retry_request(
            request,
            spider=self.spider,
            reason=reason,
            max_retry_times=self.retry_times,
            priority_adjust=-1,
        )
        return retry_request

    async def process_response(self, request: Request, response):
        if response.status in self.proxy_failure_statuses:
            self.crawler.stats.inc_value(f"proxy/failure_status/{response.status}")
            retry = self._retry_with_new_proxy(
                request,
                response_status_message(response.status),
            )
            if retry is not None:
                return retry
            if self.required:
                self._close_spider("proxy_retry_exhausted")
        elif response.status < 400:
            self.pool.mark_good(request.meta.get("_tianqi_proxy"))
            self.crawler.stats.inc_value("proxy/success")
        return response

    async def process_exception(self, request: Request, exception: Exception):
        if isinstance(exception, IgnoreRequest):
            return None
        if not request.meta.get("_tianqi_proxy"):
            if self.required:
                self._close_spider("unproxied_request_failure")
            return None
        self.crawler.stats.inc_value("proxy/network_exception")
        retry = self._retry_with_new_proxy(request, exception)
        if retry is None and self.required:
            self._close_spider("proxy_retry_exhausted")
        return retry

    def spider_closed(self, spider, reason: str) -> None:
        spider.logger.info("天启代理池关闭状态：reason=%s stats=%s", reason, self.pool.stats())
