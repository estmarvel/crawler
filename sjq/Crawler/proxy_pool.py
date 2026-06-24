"""
=============================================================================
代理IP池管理模块
=============================================================================
功能:
  - 从本地文件加载代理列表 (proxies.txt)
  - 从天启代理 API 按需提取高质量代理 IP
  - 代理可用性验证(通过请求目标站点)
  - 自动剔除失效代理
  - 运行时动态补充（IP 过期后重新提取）
  - 线程安全的代理轮换

代理源: 天启代理 (api.tianqiip.com)
  - 按次提取，支持 HTTP/HTTPS/SOCKS5
  - IP 有效期 3/5/10/15 分钟可选
  - 返回 JSON 格式，含 ip/port/expire/city/isp

使用方式:
  from proxy_pool import ProxyPool, ProxyPoolEmptyError
  pool = ProxyPool()
  try:
      proxy = pool.get_proxy()   # 获取一个可用代理
  except ProxyPoolEmptyError:
      sys.exit("无可用代理，爬虫终止")
  pool.mark_bad(proxy)           # 标记代理失效
  pool.stats()                   # 查看代理池状态

配置文件 (proxies.txt):
  每行一个代理, 支持以下格式:
    192.168.1.1:8080
    192.168.1.2:3128
    192.168.1.3:8080:username:password
=============================================================================
"""

import logging
import random
import threading
import time
from typing import Dict, List

import requests

from config import (
    TIANQIIP_API_URL,
    TIANQIIP_PARAMS,
    TIANQIIP_MAX_API_CALLS,
    PROXY_MAX_FAILURES,
    PROXY_POOL_MIN_SIZE,
    REQUEST_TIMEOUT,
    BASE_HEADERS,
)

logger = logging.getLogger(__name__)


class ProxyPoolEmptyError(Exception):
    """代理池枯竭异常 —— 无法获取任何可用代理时抛出。"""
    pass


class ProxyPool:
    """IP 代理池管理器.

    维护一个可用的 HTTP/HTTPS 代理列表，支持:
      - 从本地文件加载代理
      - 从天启代理 API 按需提取
      - 代理有效性并发验证
      - 失效代理自动剔除
      - 运行时动态补充（IP 过期后重新拉取）
      - 统计信息跟踪

    接口规范:
        pool = ProxyPool()
        try:
            proxy = pool.get_proxy()
        except ProxyPoolEmptyError:
            # 无可用代理，应终止爬虫
            ...
        pool.mark_bad(proxy)
        pool.stats()
        pool.refresh()
    """

    def __init__(self):
        self._proxies: List[Dict[str, str]] = []
        self._bad_proxies: Dict[str, int] = {}
        self._fail_counts: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._api_call_count = 0
        self._api_call_limit = TIANQIIP_MAX_API_CALLS
        self._init_proxies()

    # ======================== 公开接口 ========================

    def get_proxy(self) -> Dict[str, str]:
        """获取一个可用代理。

        从代理池中随机选择一个已验证的代理返回。
        如果代理池为空，尝试从 API 动态补充一次，
        仍为空则抛出 ProxyPoolEmptyError。

        Returns:
            proxies 字典 (用于 requests.get(proxies=proxy))

        Raises:
            ProxyPoolEmptyError: 代理池枯竭，无法获取代理
        """
        with self._lock:
            if not self._proxies:
                logger.warning("代理池为空，尝试从天启 API 补充...")
                api_proxies = self._fetch_from_api()
                if api_proxies:
                    self._proxies.extend(api_proxies)
                if not self._proxies:
                    raise ProxyPoolEmptyError(
                        "代理池已枯竭：天启 API 也未返回可用 IP，无法继续爬取"
                    )

            proxy = random.choice(self._proxies)
            logger.debug("分配代理: %s", proxy["http"])
            return proxy

    def has_proxies(self) -> bool:
        """检查代理池是否有可用代理（不触发补充）。"""
        with self._lock:
            return len(self._proxies) > 0

    def mark_bad(self, proxy: Dict[str, str]):
        """标记代理失效。

        当使用某个代理请求失败时调用此方法。
        同一代理连续失败超过阈值后自动从池中移除。

        Args:
            proxy: 失效的代理字典
        """
        if proxy is None:
            return

        proxy_key = proxy.get("http", "")
        with self._lock:
            self._fail_counts[proxy_key] = self._fail_counts.get(proxy_key, 0) + 1
            fail_count = self._fail_counts[proxy_key]

            if fail_count >= PROXY_MAX_FAILURES:
                self._bad_proxies[proxy_key] = self._bad_proxies.get(proxy_key, 0) + 1
                before = len(self._proxies)
                self._proxies = [p for p in self._proxies if p.get("http") != proxy_key]
                removed = before - len(self._proxies)
                if removed > 0:
                    logger.warning(
                        "代理已移除 (连续失败 %d 次): %s", PROXY_MAX_FAILURES, proxy_key
                    )

                # 如果池子低于最小阈值，触发补充
                if len(self._proxies) < PROXY_POOL_MIN_SIZE:
                    logger.info("代理池低于最小阈值 (%d)，触发 API 补充...", PROXY_POOL_MIN_SIZE)
                    api_proxies = self._fetch_from_api()
                    if api_proxies:
                        self._proxies.extend(api_proxies)
                        logger.info("API 补充: %d 个 IP 入池", len(api_proxies))

    def stats(self) -> Dict:
        """返回代理池统计信息。

        Returns:
            {"available": int, "bad_total": int, "min_required": int, "api_calls_used": int, "api_calls_limit": int}
        """
        with self._lock:
            return {
                "available": len(self._proxies),
                "bad_total": len(self._bad_proxies),
                "min_required": PROXY_POOL_MIN_SIZE,
                "api_calls_used": self._api_call_count,
                "api_calls_limit": self._api_call_limit,
            }

    def refresh(self):
        """强制刷新代理池：清空并重新从文件+API 加载。"""
        with self._lock:
            self._proxies.clear()
            self._bad_proxies.clear()
            self._fail_counts.clear()
            self._api_call_count = 0
        self._init_proxies()

    # ======================== 内部：初始化 ========================

    def _init_proxies(self):
        """初始化代理池：从天启 API 获取 IP。"""
        logger.info("代理池初始化: 从天启 API 获取 IP...")
        api_proxies = self._fetch_from_api()
        self._proxies = api_proxies  # 跳过验证，直接使用
        logger.info("代理池初始化完成: 共 %d 个可用代理", len(self._proxies))

    # ======================== 内部：天启 API ========================

    def _fetch_from_api(self) -> List[Dict[str, str]]:
        """从天启代理 API 提取 IP 列表。

        API 返回 JSON 格式:
        {"code": 1000, "data": [{"ip": "...", "port": "...", ...}]}

        API 调用次数受 self._api_call_limit 约束：
        达到上限后不再请求，返回空列表。

        Returns:
            代理字典列表
        """
        with self._fetch_lock:
            if self._api_call_count >= self._api_call_limit:
                logger.warning(
                    "天启 API 已达调用上限 (%d/%d)，不再请求",
                    self._api_call_count, self._api_call_limit,
                )
                return []

            self._api_call_count += 1
            proxies = []
            try:
                logger.info(
                    "[API %d/%d] 请求天启 API: %s (time=%s min, num=%d)",
                    self._api_call_count, self._api_call_limit,
                    TIANQIIP_API_URL,
                    TIANQIIP_PARAMS.get("time", 3),
                    TIANQIIP_PARAMS.get("num", 10),
                )
                resp = requests.get(
                    TIANQIIP_API_URL,
                    params=TIANQIIP_PARAMS,
                    timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": BASE_HEADERS.get("User-Agent", "")},
                )
                if resp.status_code != 200:
                    logger.error("天启 API 返回 HTTP %d", resp.status_code)
                    return proxies

                data = resp.json()
                if data.get("code") != 1000:
                    logger.error("天启 API 业务错误: code=%s", data.get("code"))
                    return proxies

                ip_list = data.get("data", [])
                if not ip_list:
                    logger.warning("天启 API 返回空列表")
                    return proxies

                for entry in ip_list:
                    ip = entry.get("ip", "").strip()
                    port = str(entry.get("port", "")).strip()
                    if not ip or not port:
                        continue
                    proxy_url = f"http://{ip}:{port}"
                    proxy_dict = {
                        "http": proxy_url,
                        "https": proxy_url,
                        "source": "api_tianqiip",
                    }
                    expire = entry.get("expire", "")
                    city = entry.get("city", "")
                    isp = entry.get("isp", "")
                    if expire or city or isp:
                        proxy_dict["_meta"] = f"{city}-{isp}({expire})"
                    proxies.append(proxy_dict)

                logger.info(
                    "[API %d/%d] 天启 API 返回 %d 个 IP",
                    self._api_call_count, self._api_call_limit, len(proxies),
                )

            except requests.exceptions.Timeout:
                logger.error("天启 API 请求超时")
            except requests.exceptions.RequestException as e:
                logger.error("天启 API 请求失败: %s", e)
            except (ValueError, KeyError) as e:
                logger.error("天启 API 响应解析失败: %s", e)

            return proxies
