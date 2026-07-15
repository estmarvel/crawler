import logging
import random
from sxbid_crawler.proxy_pool import create_proxy_adapter

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
]


class RandomUserAgentMiddleware:
    def process_request(self, request):
        request.headers.setdefault("User-Agent", random.choice(USER_AGENTS))


logger = logging.getLogger(__name__)


class ProxyMiddleware:
    def __init__(self, use_proxy=False, direct_fallback=True):
        self.use_proxy = use_proxy
        self.direct_fallback = direct_fallback
        self.adapter = create_proxy_adapter() if use_proxy else None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            use_proxy=crawler.settings.getbool("USE_PROXY", False),
            direct_fallback=crawler.settings.getbool("PROXY_DIRECT_FALLBACK", True),
        )

    def process_request(self, request):
        if not self.use_proxy:
            return None
        proxy, raw = self.adapter.get_proxy()
        if proxy:
            request.meta["proxy"] = proxy
            request.meta["raw_proxy"] = raw
            return None
        if self.direct_fallback:
            return None
        raise RuntimeError("代理池为空，且禁止直连")

    def process_response(self, request, response):
        raw = request.meta.get("raw_proxy")

        if raw and response.status in [
            403, 407, 408, 429, 500, 502, 503, 504
        ]:
            self.adapter.mark_bad(raw)

        def retry_invalid(meta_key, reason):
            if raw:
                self.adapter.mark_bad(raw)

            retry_count = request.meta.get(meta_key, 0)
            max_retries = (
                15 if meta_key == "sxbid_pdf_retry" else 5
            )

            if retry_count >= max_retries:
                logger.error(
                    "%s，重试次数已用完: url=%s",
                    reason,
                    response.url,
                )
                return None

            logger.warning(
                "%s，换代理重试: url=%s retry=%s/%s",
                reason,
                response.url,
                retry_count + 1,
                max_retries,
            )

            new_request = request.replace(dont_filter=True)
            new_request.meta[meta_key] = retry_count + 1
            new_request.meta.pop("proxy", None)
            new_request.meta.pop("raw_proxy", None)

            return new_request

        is_pdf_download = "/f/downloadByFileName" in response.url
        is_invalid_pdf = (
            is_pdf_download
            and response.status == 200
            and (
                len(response.body) < 1000
                or not response.body.startswith(b"%PDF")
            )
        )

        if is_invalid_pdf:
            reason = f"PDF响应无效(bytes={len(response.body)})"
            retry_request = retry_invalid(
                "sxbid_pdf_retry",
                reason,
            )
            if retry_request is not None:
                return retry_request

        is_notice_detail = (
            "/f/new/notice/0/" in response.url
            or "/f/new/notice/1/" in response.url
        )
        is_empty_shell = (
            is_notice_detail
            and response.status == 200
            and (
                "所属行业" not in response.text
                or "依据文件" not in response.text
                or "<table" not in response.text.lower()
            )
        )

        if is_empty_shell:
            retry_request = retry_invalid(
                "sxbid_empty_retry",
                "详情页为空壳响应",
            )
            if retry_request is not None:
                return retry_request

        return response

    def process_exception(self, request, exception):
        raw = request.meta.get("raw_proxy")
        if raw:
            self.adapter.mark_bad(raw)
        return None
