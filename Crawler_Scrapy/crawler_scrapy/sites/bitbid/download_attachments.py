"""独立下载比比网 JSON 中的公告附件。"""

from __future__ import annotations

import time
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests

from crawler_scrapy.sites.bitbid import config
from crawler_scrapy.sites.sxjm.download_attachments import (
    AccessBlocked,
    AttachmentDownloader,
    BLOCK_STATUSES,
    run_downloader,
)


class BitbidAttachmentDownloader(AttachmentDownloader):
    site_code = "bitbid"
    site_label = "BITBID"
    allowed_attachment_hosts = {"www.bitbid.cn", "xzb.bitbid.cn"}
    allowed_attachment_schemes = {"http", "https"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._access_cookie_at = 0.0

    @staticmethod
    def _current_url(url: str) -> str:
        """将历史 JSON 中的旧附件域名迁移到官网当前实际端点。"""

        parsed = urlsplit(url)
        if (parsed.hostname or "").lower() != "zb.bitbid.cn":
            return url
        host = (
            "xzb.bitbid.cn"
            if parsed.path.startswith("/tPlanProject!")
            else "www.bitbid.cn"
        )
        return urlunsplit((parsed.scheme or "http", host, parsed.path, parsed.query, ""))

    @staticmethod
    def _stable_source_file_id(url: str) -> str | None:
        params = parse_qs(urlsplit(url).query)
        for key, prefix in (
            ("zbGongGao.id", "tender"),
            ("dbZhongBiaoGongShi.id", "candidate"),
            ("dbZhongBiaoJieGuoGongGao.id", "award"),
            ("tPlanProject3.id", "plan_cert"),
        ):
            values = params.get(key)
            if values and values[0]:
                return f"{prefix}_{values[0]}"
        return None

    @classmethod
    def _normalize_attachment(cls, attachment: MutableMapping[str, object]) -> None:
        current_url = cls._current_url(
            str(attachment.get("file_url") or "").strip()
        )
        if current_url:
            attachment["file_url"] = current_url
        if not attachment.get("source_file_id"):
            source_file_id = cls._stable_source_file_id(current_url)
            if source_file_id:
                attachment["source_file_id"] = source_file_id

    @classmethod
    def _load_rows(cls, path: Path) -> list[dict[str, object]]:
        rows = AttachmentDownloader._load_rows(path)
        for row in rows:
            attachments = row.get("附件") or []
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                if isinstance(attachment, MutableMapping):
                    cls._normalize_attachment(attachment)

            trace = row.get("_trace")
            export = trace.get("exportMetadata") if isinstance(trace, Mapping) else None
            traced = export.get("attachments") if isinstance(export, Mapping) else None
            if not isinstance(traced, list):
                continue
            for attachment in traced:
                if isinstance(attachment, MutableMapping):
                    cls._normalize_attachment(attachment)
        return rows

    def _prime_access_cookie(self, referer: str, *, force: bool = False) -> None:
        # www.bitbid.cn 首次访问通过同地址 302 下发一小时 verify Cookie。
        # 提前取 Cookie，避免附件请求自动跟随相同 Location 直至重定向超限。
        if not force and time.monotonic() - self._access_cookie_at < 3000:
            return
        response = self.session.get(
            f"{config.WEB_BASE_URL}/",
            headers={"Referer": referer} if referer else None,
            stream=False,
            timeout=(self.config.connect_timeout, self.config.read_timeout),
            allow_redirects=False,
        )
        try:
            if response.status_code in BLOCK_STATUSES:
                raise AccessBlocked(
                    f"附件服务器返回 {response.status_code}：{config.WEB_BASE_URL}/"
                )
            if response.status_code not in {200, 301, 302, 303, 307, 308}:
                response.raise_for_status()
            self._access_cookie_at = time.monotonic()
        finally:
            response.close()

    def _download_once(
        self,
        url: str,
        final_path: Path,
        referer: str,
    ) -> tuple[requests.Response, int, float]:
        if (urlsplit(url).hostname or "").lower() == "www.bitbid.cn":
            self._prime_access_cookie(referer)
        try:
            result = super()._download_once(url, final_path, referer)
        except requests.TooManyRedirects:
            self._prime_access_cookie(referer, force=True)
            result = super()._download_once(url, final_path, referer)

        response, _, _ = result
        if final_path.suffix.lower() == ".pdf":
            with final_path.open("rb") as file_object:
                signature = file_object.read(5)
            if signature != b"%PDF-":
                response.close()
                final_path.unlink(missing_ok=True)
                raise ValueError(f"附件响应不是 PDF：{url}")
        return result

    def _download(
        self,
        row: Mapping[str, object],
        attachment: Mapping[str, object],
        final_path: Path,
    ) -> dict[str, object]:
        if isinstance(attachment, MutableMapping):
            self._normalize_attachment(attachment)
        current_url = str(attachment.get("file_url") or "").strip()
        metadata = super()._download(row, attachment, final_path)
        if current_url:
            metadata["file_url"] = current_url
        if attachment.get("source_file_id"):
            metadata["source_file_id"] = attachment["source_file_id"]
        return metadata


def main(argv: list[str] | None = None) -> int:
    return run_downloader(BitbidAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
