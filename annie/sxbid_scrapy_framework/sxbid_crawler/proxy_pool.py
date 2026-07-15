import importlib.util
import inspect
import os
import time
from typing import Optional, Tuple, List

import requests


class BaseProxyAdapter:
    def get_proxy(self) -> Tuple[Optional[str], object]:
        return None, None

    def mark_bad(self, raw_proxy):
        pass

    def stats(self):
        return {}


class LegacyProxyPoolAdapter(BaseProxyAdapter):
    """复用旧项目 proxy_pool.py 中的 ProxyPool。"""

    def __init__(self):
        path = os.getenv("LEGACY_PROXY_POOL_PATH", "/home/intsig/zwx/sxbid/proxy_pool.py")
        if not os.path.exists(path):
            raise FileNotFoundError(f"旧代理池文件不存在: {path}")
        spec = importlib.util.spec_from_file_location("legacy_proxy_pool", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ProxyPool = getattr(mod, "ProxyPool")
        kwargs = {
            "num": int(os.getenv("PROXY_NUM", "30")),
            "time": int(os.getenv("PROXY_TIME", "5")),
            "port": int(os.getenv("PROXY_PORT", "2")),
            "min_size": int(os.getenv("PROXY_MIN_SIZE", "8")),
            "max_fail_count": int(os.getenv("PROXY_MAX_FAIL_COUNT", "1")),
        }
        sig = inspect.signature(ProxyPool)
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        self.pool = ProxyPool(**filtered)

    def get_proxy(self):
        raw = self.pool.get_proxy()
        return self._to_scrapy_proxy(raw), raw

    def mark_bad(self, raw_proxy):
        try:
            self.pool.mark_bad(raw_proxy)
        except Exception:
            pass

    def stats(self):
        try:
            return self.pool.stats()
        except Exception:
            return {}

    def _to_scrapy_proxy(self, raw):
        if raw is None:
            return None
        if isinstance(raw, str):
            proxy = raw
        elif isinstance(raw, dict):
            proxy = raw.get("http") or raw.get("https") or raw.get("proxy") or ""
        else:
            proxy = str(raw)
        if not proxy:
            return None
        if not proxy.startswith("http"):
            proxy = "http://" + proxy
        return proxy


class ApiProxyPoolAdapter(BaseProxyAdapter):
    def __init__(self):
        self.api_url = os.getenv("PROXY_API_URL", "")
        self.ttl = int(os.getenv("PROXY_TTL_SECONDS", "180"))
        self.proxies: List[str] = []
        self.bad = set()
        self.last_fetch = 0

    def get_proxy(self):
        if not self.api_url:
            return None, None
        now = time.time()
        if not self.proxies or now - self.last_fetch > self.ttl:
            self.fetch()
        while self.proxies:
            p = self.proxies.pop(0)
            if p not in self.bad:
                self.proxies.append(p)
                return p, p
        return None, None

    def mark_bad(self, raw_proxy):
        if raw_proxy:
            self.bad.add(raw_proxy)

    def fetch(self):
        try:
            resp = requests.get(self.api_url, timeout=15)
            resp.raise_for_status()
            text = resp.text.strip()
            proxies = []
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("{"):
                    continue
                if ":" in line:
                    proxies.append(line if line.startswith("http") else "http://" + line)
            if not proxies:
                import json
                data = json.loads(text)
                raw_list = data.get("data") or data.get("proxies") or []
                for item in raw_list:
                    if isinstance(item, str):
                        proxy = item
                    else:
                        ip = item.get("ip") or item.get("host")
                        port = item.get("port")
                        proxy = f"{ip}:{port}" if ip and port else ""
                    if proxy:
                        proxies.append(proxy if proxy.startswith("http") else "http://" + proxy)
            self.proxies = [p for p in proxies if p not in self.bad]
            self.last_fetch = time.time()
        except Exception:
            return

    def stats(self):
        return {"available": len(self.proxies), "bad_unique": len(self.bad), "provider": "api"}


def create_proxy_adapter():
    provider = os.getenv("PROXY_PROVIDER", "legacy").lower()
    if provider == "api":
        return ApiProxyPoolAdapter()
    return LegacyProxyPoolAdapter()
