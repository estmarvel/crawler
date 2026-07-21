"""Scrapy 跨版本主动关停兼容函数。"""

from __future__ import annotations

import inspect

from twisted.internet.defer import ensureDeferred


def request_spider_close(engine, spider, reason: str):
    """发起 Spider 关停但不在下载中间件内等待关停完成。

    Scrapy 2.16 的 ``close_spider_async`` 已不再接收 spider 位置参数；旧版只有
    ``close_spider(spider, reason)`` 或仍接收 spider 的异步变体。下载中间件本身是
    engine 正在等待的请求之一，因此不能在其中 await 整个关停过程，否则会互相等待。
    """

    async_close = getattr(engine, "close_spider_async", None)
    if callable(async_close):
        try:
            parameters = inspect.signature(async_close).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "spider" in parameters:
            result = async_close(spider, reason=reason)
        else:
            result = async_close(reason=reason)
        return ensureDeferred(result) if inspect.isawaitable(result) else result

    legacy_close = getattr(engine, "close_spider", None)
    if callable(legacy_close):
        return legacy_close(spider, reason=reason)
    return None
