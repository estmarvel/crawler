from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scrapy import Request
from scrapy.settings import Settings

from crawler_scrapy import settings as project_settings
from crawler_scrapy.spiders.huaxin import HuaxinSpider
from crawler_scrapy.transport.access_guard import DirectAccessGuardMiddleware
from crawler_scrapy.transport.proxy_middleware import StaticProxyMiddleware
from crawler_scrapy.transport.proxy_pool import ProxyPoolConfigurationError
from crawler_scrapy.transport.scrapy_compat import request_spider_close


TIANQI_MIDDLEWARE = (
    "crawler_scrapy.transport.proxy_middleware.TianqiProxyMiddleware"
)
DIRECT_GUARD_MIDDLEWARE = (
    "crawler_scrapy.transport.access_guard.DirectAccessGuardMiddleware"
)
STATIC_PROXY_MIDDLEWARE = (
    "crawler_scrapy.transport.proxy_middleware.StaticProxyMiddleware"
)


def _settings(mode: str | None = None) -> Settings:
    settings = Settings()
    settings.setmodule(project_settings, priority="project")
    if mode is not None:
        settings.set("CRAWLER_OUTBOUND_MODE", mode, priority="cmdline")
    HuaxinSpider.update_settings(settings)
    return settings


class OutboundModeTest(unittest.TestCase):
    def test_direct_is_the_default_mode(self):
        settings = _settings()
        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        self.assertEqual(settings.get("CRAWLER_OUTBOUND_MODE"), "direct")
        self.assertFalse(settings.getbool("TIANQI_PROXY_ENABLED"))
        self.assertFalse(settings.getbool("STATIC_PROXY_ENABLED"))
        self.assertFalse(settings.getbool("HTTPPROXY_ENABLED"))
        self.assertIsNone(middlewares[TIANQI_MIDDLEWARE])
        self.assertIsNone(middlewares[STATIC_PROXY_MIDDLEWARE])
        self.assertEqual(middlewares[DIRECT_GUARD_MIDDLEWARE], 650)

    def test_static_proxy_can_still_be_selected(self):
        settings = _settings("static")
        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        self.assertTrue(settings.getbool("STATIC_PROXY_ENABLED"))
        self.assertTrue(settings.getbool("HTTPPROXY_ENABLED"))
        self.assertEqual(middlewares[STATIC_PROXY_MIDDLEWARE], 610)
        self.assertEqual(middlewares[DIRECT_GUARD_MIDDLEWARE], 650)

    def test_tianqi_mode_can_still_be_selected(self):
        settings = _settings("tianqi")
        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        self.assertTrue(settings.getbool("TIANQI_PROXY_ENABLED"))
        self.assertEqual(middlewares[TIANQI_MIDDLEWARE], 610)
        self.assertNotIn(DIRECT_GUARD_MIDDLEWARE, middlewares)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            _settings("unknown")

    def test_retry_after_header_is_respected(self):
        response = type(
            "Response",
            (),
            {"headers": {b"Retry-After": b"120"}},
        )()
        self.assertEqual(
            DirectAccessGuardMiddleware._retry_after_seconds(response),
            120.0,
        )

    @staticmethod
    def _static_proxy_crawler(settings):
        return SimpleNamespace(
            settings=settings,
            signals=SimpleNamespace(connect=lambda *args, **kwargs: None),
            stats=SimpleNamespace(inc_value=lambda *args, **kwargs: None),
            engine=None,
            spider=SimpleNamespace(logger=SimpleNamespace(info=lambda *args: None)),
        )

    def test_static_proxy_credentials_stay_out_of_proxy_url(self):
        settings = _settings("static")
        crawler = self._static_proxy_crawler(settings)
        with patch.dict(
            os.environ,
            {
                "HUAXIN_PROXY_USERNAME": "proxy-user",
                "HUAXIN_PROXY_PASSWORD": "proxy-password",
            },
            clear=False,
        ):
            middleware = StaticProxyMiddleware.from_crawler(crawler)
        request = Request("https://www.ygcgpt.com/")
        middleware.process_request(request)
        self.assertEqual(request.meta["proxy"], "http://210.51.27.8:10000")
        self.assertNotIn("proxy-user", request.meta["proxy"])
        self.assertNotIn("proxy-password", request.meta["proxy"])
        self.assertTrue(request.headers[b"Proxy-Authorization"].startswith(b"Basic "))

    def test_static_proxy_missing_credentials_fails_closed(self):
        settings = _settings("static")
        crawler = self._static_proxy_crawler(settings)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProxyPoolConfigurationError):
                StaticProxyMiddleware.from_crawler(crawler)

    def test_connect_407_is_recognized_as_proxy_auth_failure(self):
        error = RuntimeError(
            "Could not open CONNECT tunnel with proxy "
            "[{'status': 407, 'reason': b'Proxy Authentication Required'}]"
        )
        self.assertTrue(StaticProxyMiddleware._is_auth_failure(error))
        self.assertFalse(
            StaticProxyMiddleware._is_auth_failure(RuntimeError("connection timed out"))
        )


class SpiderCloseCompatibilityTest(unittest.TestCase):
    def test_engine_close_is_scheduled_without_waiting(self):
        class Engine:
            def __init__(self):
                self.value = None

            def close_spider(self, spider, reason="cancelled"):
                self.value = (spider, reason)
                return "scheduled"

        spider = object()
        engine = Engine()
        result = request_spider_close(engine, spider, "static_proxy_unavailable")
        self.assertEqual(result, "scheduled")
        self.assertEqual(engine.value, (spider, "static_proxy_unavailable"))

    def test_async_only_older_signature_is_supported(self):
        class Engine:
            def __init__(self):
                self.value = None

            async def close_spider_async(self, spider, reason="cancelled"):
                self.value = (spider, reason)

        spider = object()
        engine = Engine()
        deferred = request_spider_close(engine, spider, "direct_access_blocked")
        self.assertIsNotNone(deferred)
        self.assertEqual(engine.value, (spider, "direct_access_blocked"))


if __name__ == "__main__":
    unittest.main()
