from __future__ import annotations

import time
import unittest

from crawler_scrapy.transport.proxy_pool import (
    ProxyPoolConfigurationError,
    ProxyPoolEmptyError,
    ProxyItem,
    TianqiProxyPool,
)


class FakeResponse:
    def __init__(self, payload, *, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload, *, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(
            self.payload,
            status_code=self.status_code,
            text=self.text,
        )


class TianqiProxyPoolTest(unittest.TestCase):
    def make_pool(self, session, **overrides):
        values = {
            "secret": "test-secret",
            "sign": "test-sign",
            "session": session,
            "num": 10,
            "lifetime_minutes": 3,
            "min_size": 1,
            "api_call_limit": 1,
        }
        values.update(overrides)
        return TianqiProxyPool(**values)

    def test_credentials_are_required(self):
        with self.assertRaises(ProxyPoolConfigurationError):
            TianqiProxyPool(secret="", sign="", session=FakeSession({}))

    def test_fetch_uses_confirmed_short_proxy_parameters(self):
        session = FakeSession(
            {
                "code": 1000,
                "data": [
                    {
                        "ip": "127.0.0.2",
                        "port": "18080",
                        "expire": "2099-01-01 00:00:00",
                    }
                ],
            }
        )
        pool = self.make_pool(session)
        self.assertEqual(pool.get_proxy_url(), "http://127.0.0.2:18080")
        params = session.calls[0][1]["params"]
        self.assertEqual(params["num"], 10)
        self.assertEqual(params["time"], 3)
        self.assertEqual(params["port"], 2)
        self.assertEqual(params["mr"], 1)
        self.assertEqual(params["ts"], 1)

    def test_expired_proxy_is_never_returned(self):
        session = FakeSession({"code": 1000, "data": []})
        pool = self.make_pool(session)
        pool._items["http://expired:1"] = ProxyItem(
            "http://expired:1",
            expire_ts=time.time() - 1,
        )
        with self.assertRaises(ProxyPoolEmptyError):
            pool.get_proxy_url()

    def test_bad_proxy_is_removed_at_confirmed_threshold(self):
        session = FakeSession(
            {"code": 1000, "data": [{"ip": "127.0.0.3", "port": "18081"}]}
        )
        pool = self.make_pool(session, max_failures=1)
        proxy = pool.get_proxy_url()
        self.assertTrue(pool.mark_bad(proxy))
        self.assertEqual(pool.stats()["available"], 0)

    def test_api_limit_fails_closed_without_direct_fallback(self):
        session = FakeSession({"code": 1006, "data": []})
        pool = self.make_pool(session, api_call_limit=1)
        with self.assertRaises(ProxyPoolEmptyError):
            pool.get_proxy_url()
        with self.assertRaises(ProxyPoolEmptyError):
            pool.get_proxy_url()
        self.assertEqual(len(session.calls), 1)

    def test_http_error_log_does_not_contain_credentials(self):
        session = FakeSession(
            {},
            status_code=400,
            text='{"code":1005,"msg":"quota exhausted"}',
        )
        pool = self.make_pool(session)
        with self.assertLogs(
            "crawler_scrapy.transport.proxy_pool",
            level="ERROR",
        ) as captured:
            with self.assertRaises(ProxyPoolEmptyError):
                pool.get_proxy_url()
        logs = "\n".join(captured.output)
        self.assertNotIn("test-secret", logs)
        self.assertNotIn("test-sign", logs)


if __name__ == "__main__":
    unittest.main()
