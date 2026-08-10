"""独立、可恢复下载山西交控公开详情中由 fileId 标识的附件。"""

from __future__ import annotations

import mimetypes
import os
import re
import time
from pathlib import Path

import requests

from crawler_scrapy.sites.sxjkzcpt import config
from crawler_scrapy.sites.sxjm.download_attachments import (
    AccessBlocked,
    AttachmentDownloader,
    BLOCK_STATUSES,
    run_downloader,
)


class SxjkzcptAttachmentDownloader(AttachmentDownloader):
    site_code = "sxjkzcpt"
    site_label = "SXJKZCPT"
    allowed_attachment_hosts = {"www.sxjkzcpt.com.cn"}
    allowed_attachment_schemes = {"https"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._csrf = ""

    @staticmethod
    def _file_id(url: str) -> str:
        matched = re.search(r"/downloadFile/([A-Za-z0-9_-]+)(?:[/?#]|$)", url)
        if not matched:
            raise ValueError(f"无法从山西交控附件URL解析fileId：{url}")
        return matched.group(1)

    def _bootstrap(self) -> None:
        if self._csrf:
            return
        response = self.session.get(
            config.BOOTSTRAP_URL,
            timeout=(self.config.connect_timeout, self.config.read_timeout),
        )
        if response.status_code in BLOCK_STATUSES:
            response.close()
            raise AccessBlocked(f"附件会话入口返回 {response.status_code}")
        response.raise_for_status()
        matched = re.search(
            r"name=['\"]_csrf['\"][^>]*value=['\"]([^'\"]+)",
            response.text,
            re.I,
        )
        response.close()
        if not matched:
            raise requests.RequestException("山西交控附件会话未返回CSRF")
        self._csrf = matched.group(1)

    def _download_once(
        self,
        url: str,
        final_path: Path,
        referer: str,
    ) -> tuple[requests.Response, int, float]:
        self._bootstrap()
        file_id = self._file_id(url)
        check = self.session.post(
            f"{config.FILE_CHECK_BASE_URL}/{file_id}",
            data={"fileId": file_id, "_csrf": self._csrf},
            headers={"Referer": referer or config.BOOTSTRAP_URL},
            timeout=(self.config.connect_timeout, self.config.read_timeout),
        )
        if check.status_code in BLOCK_STATUSES:
            check.close()
            raise AccessBlocked(f"附件检查接口返回 {check.status_code}：{file_id}")
        check.raise_for_status()
        try:
            payload = check.json()
        except ValueError as exc:
            check.close()
            raise requests.RequestException("附件检查接口未返回JSON") from exc
        check.close()
        if int(payload.get("code", -1)) != 0:
            raise requests.RequestException(
                f"附件不可下载：fileId={file_id} msg={payload.get('msg') or payload}"
            )

        # 源站下载接口是 POST 表单，未声明支持 Range；保留 .part 文件用于
        # 异常诊断，但重试必须从头覆盖，避免把完整响应追加为损坏文件。
        part_path = final_path.with_name(f"{final_path.name}.part")
        started = time.monotonic()
        response = self.session.post(
            url,
            data={"fileId": file_id, "_csrf": self._csrf},
            headers={"Referer": referer or config.BOOTSTRAP_URL},
            stream=True,
            timeout=(self.config.connect_timeout, self.config.read_timeout),
            allow_redirects=True,
        )
        try:
            self._validate_url(response.url)
        except ValueError:
            response.close()
            raise
        if response.status_code in BLOCK_STATUSES:
            response.close()
            raise AccessBlocked(f"附件服务器返回 {response.status_code}：{url}")
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type:
            response.close()
            raise requests.RequestException("附件下载接口返回HTML而非文件")
        expected = response.headers.get("Content-Length")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with part_path.open("wb") as file_object:
                for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                    if chunk:
                        file_object.write(chunk)
                file_object.flush()
                os.fsync(file_object.fileno())
            size = part_path.stat().st_size
            if expected and expected.isdigit() and size != int(expected):
                raise IOError(f"附件长度不完整：expected={expected} actual={size}")
            if size <= 0:
                raise IOError("附件响应为空")
            part_path.replace(final_path)
        except Exception:
            response.close()
            raise
        if not response.headers.get("Content-Type"):
            response.headers["Content-Type"] = (
                mimetypes.guess_type(final_path.name)[0] or "application/octet-stream"
            )
        return response, size, max(time.monotonic() - started, 0.001)


def main(argv: list[str] | None = None) -> int:
    return run_downloader(SxjkzcptAttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
