"""
=============================================================================
代理IP池管理模块
=============================================================================
功能:
  - 从本地文件加载代理列表 (proxies.txt)
  - 从免费代理API自动补充代理
  - 代理可用性验证(通过请求目标站点)
  - 自动剔除失效代理
  - 线程安全的代理轮换

使用方式:
  from proxy_pool import ProxyPool
  pool = ProxyPool()
  proxy = pool.get_proxy()   # 获取一个可用代理 {"http": "...", "https": "..."}
  pool.mark_bad(proxy)        # 标记代理失效
  pool.stats()                # 查看代理池状态

配置文件 (proxies.txt):
  每行一个代理, 支持以下格式:
    192.168.1.1:8080
    192.168.1.2:3128
    192.168.1.3:8080:username:password
=============================================================================
"""

import logging
import os
import random
import threading
import time
from typing import Optional, Dict, List

import requests

from config import (
    FREE_PROXY_APIS,
    PROXY_FILE,
    PROXY_MAX_FAILURES,
    PROXY_POOL_MIN_SIZE,
    PROXY_POOL_TARGET_SIZE,
    PROXY_VALIDATE_TIMEOUT,
    PROXY_VALIDATE_URL,
    REQUEST_TIMEOUT,
    BASE_HEADERS,
)

logger = logging.getLogger(__name__)


class ProxyPool:
    """IP代理池管理器.

    维护一个可用的HTTP/HTTPS代理列表, 支持:
      - 从本地文件加载代理
      - 从免费API自动获取代理
      - 代理有效性验证
      - 自动故障转移(失效代理剔除)
      - 统计信息跟踪
    """

    def __init__(self, proxy_file: str = None):
        """
        Args:
            proxy_file: 代理配置文件路径, 默认使用 config.PROXY_FILE
        """
        self._proxy_file = proxy_file or PROXY_FILE
        # 可用代理列表: [{"http": "...", "https": "...", "source": "file/api"}]
        self._proxies: List[Dict[str, str]] = []
        # 已失效代理(避免重复验证): {proxy_str: fail_count}
        self._bad_proxies: Dict[str, int] = {}
        # 每个代理的失败计数
        self._fail_counts: Dict[str, int] = {}
        # 线程安全锁
        self._lock = threading.Lock()

        # 初始化时加载代理
        self._init_proxies()

    # ======================== 公开接口 ========================

    def get_proxy(self) -> Optional[Dict[str, str]]:
        """获取一个可用代理.

        从代理池中随机选择一个已验证的代理返回。
        如果代理池为空且自动补充开关已开, 则尝试从免费源获取。

        Returns:
            proxies字典 (用于 requests.get(proxies=proxy)), 无可用代理时返回 None
        """
        with self._lock:
            if not self._proxies:
                logger.warning("代理池为空, 尝试从免费源补充...")
                self._fetch_free_proxies()
                if not self._proxies:
                    logger.warning("无法获取可用代理, 将使用直连模式")
                    return None

            proxy = random.choice(self._proxies)
            logger.debug(f"分配代理: {proxy['http'][:50]}...")
            return proxy

    def mark_bad(self, proxy: Dict[str, str]):
        """标记代理失效.

        当使用某个代理请求失败时调用此方法。
        如果同一代理连续失败超过阈值, 自动从池中移除。

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
                # 从可用列表中移除
                before = len(self._proxies)
                self._proxies = [p for p in self._proxies if p.get("http") != proxy_key]
                removed = before - len(self._proxies)
                if removed > 0:
                    logger.warning(f"代理已移除 (连续失败{PROXY_MAX_FAILURES}次): {proxy_key[:60]}...")

                # 如果池子太小, 尝试补充
                if len(self._proxies) < PROXY_POOL_MIN_SIZE:
                    logger.info("代理池低于最小阈值, 触发补充...")
                    self._fetch_free_proxies()

    def stats(self) -> Dict:
        """返回代理池统计信息."""
        with self._lock:
            return {
                "available": len(self._proxies),
                "bad_total": len(self._bad_proxies),
                "min_required": PROXY_POOL_MIN_SIZE,
                "target_size": PROXY_POOL_TARGET_SIZE,
            }

    def refresh(self):
        """强制刷新代理池: 清空后重新加载."""
        with self._lock:
            self._proxies.clear()
            self._bad_proxies.clear()
            self._fail_counts.clear()
        self._init_proxies()

    # ======================== 内部方法 ========================

    def _init_proxies(self):
        """初始化代理池: 先从文件加载, 不足时从免费源补充."""
        # 1. 从本地文件加载
        file_proxies = self._load_from_file()
        valid_file_proxies = self._validate_proxies(file_proxies)
        self._proxies.extend(valid_file_proxies)
        logger.info(f"从文件加载代理: {len(valid_file_proxies)}/{len(file_proxies)} 个可用")

        # 2. 如果不足目标数量, 从免费源补充
        if len(self._proxies) < PROXY_POOL_TARGET_SIZE:
            logger.info(f"代理池当前 {len(self._proxies)} 个, 目标 {PROXY_POOL_TARGET_SIZE} 个, 从免费源补充...")
            free_proxies = self._fetch_free_proxies()
            valid_free_proxies = self._validate_proxies(free_proxies)
            # 去重后加入
            existing = {p["http"] for p in self._proxies}
            for p in valid_free_proxies:
                if p["http"] not in existing:
                    self._proxies.append(p)
            logger.info(f"从免费源补充代理: {len(valid_free_proxies)} 个可用")

        logger.info(f"代理池初始化完成: 共 {len(self._proxies)} 个可用代理")

    def _load_from_file(self) -> List[Dict[str, str]]:
        """从 proxies.txt 加载代理列表.

        文件格式(每行一个):
          192.168.1.1:8080
          192.168.1.2:3128:user:pass

        Returns:
            代理字典列表
        """
        proxies = []
        if not os.path.exists(self._proxy_file):
            logger.info(f"代理文件不存在 ({self._proxy_file}), 跳过文件加载")
            return proxies

        try:
            with open(self._proxy_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    proxy_dict = self._parse_proxy_line(line)
                    if proxy_dict:
                        proxy_dict["source"] = "file"
                        proxies.append(proxy_dict)

        except Exception as e:
            logger.error(f"读取代理文件失败: {e}")

        return proxies

    def _fetch_free_proxies(self) -> List[Dict[str, str]]:
        """从免费代理API获取代理列表.

        Returns:
            代理字典列表
        """
        proxies = []
        for api_url in FREE_PROXY_APIS:
            try:
                resp = requests.get(
                    api_url,
                    timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": random.choice([
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0",
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126.0.0.0",
                    ])},
                )
                if resp.status_code == 200:
                    for line in resp.text.strip().split("\n"):
                        line = line.strip()
                        if line and ":" in line:
                            proxy_dict = self._parse_proxy_line(line)
                            if proxy_dict:
                                proxy_dict["source"] = "api"
                                proxies.append(proxy_dict)
                    logger.debug(f"从 {api_url[:50]}... 获取到 {len(proxies)} 个代理")
            except Exception as e:
                logger.debug(f"免费代理API请求失败 ({api_url[:50]}...): {e}")

        return proxies

    @staticmethod
    def _parse_proxy_line(line: str) -> Optional[Dict[str, str]]:
        """解析单行代理配置.

        支持格式:
          ip:port            → http://ip:port
          ip:port:user:pass  → http://user:pass@ip:port

        Returns:
            代理字典, 解析失败返回 None
        """
        try:
            parts = line.split(":")
            if len(parts) == 2:
                ip, port = parts
                proxy_url = f"http://{ip}:{port}"
                return {"http": proxy_url, "https": proxy_url}
            elif len(parts) == 4:
                ip, port, user, pwd = parts
                proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
                return {"http": proxy_url, "https": proxy_url}
        except Exception:
            pass
        return None

    def _validate_proxies(self, proxies: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """验证代理列表的可用性.

        通过请求目标站点来测试每个代理是否可用。
        验证是并发进行的, 以避免阻塞过久。

        Args:
            proxies: 待验证的代理列表

        Returns:
            可用代理列表
        """
        if not proxies:
            return []

        valid_proxies = []
        # 使用线程池并发验证(限制并发数以避免资源耗尽)
        threads = []
        results = {}  # proxy_key → bool

        def _validate_one(proxy: Dict[str, str]):
            proxy_key = proxy.get("http", "")
            try:
                resp = requests.get(
                    PROXY_VALIDATE_URL,
                    proxies=proxy,
                    timeout=PROXY_VALIDATE_TIMEOUT,
                    headers={
                        "User-Agent": random.choice([
                            ua for ua in [
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0",
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126.0.0.0",
                            ]
                        ]),
                    },
                )
                results[proxy_key] = resp.status_code == 200
            except Exception:
                results[proxy_key] = False

        # 限制并发验证数量
        batch_size = 10
        for i in range(0, len(proxies), batch_size):
            batch = proxies[i:i + batch_size]
            batch_threads = []
            for proxy in batch:
                t = threading.Thread(target=_validate_one, args=(proxy,), daemon=True)
                t.start()
                batch_threads.append(t)

            for t in batch_threads:
                t.join(timeout=PROXY_VALIDATE_TIMEOUT + 5)

        # 收集有效代理
        for proxy in proxies:
            if results.get(proxy.get("http", ""), False):
                valid_proxies.append(proxy)

        return valid_proxies
