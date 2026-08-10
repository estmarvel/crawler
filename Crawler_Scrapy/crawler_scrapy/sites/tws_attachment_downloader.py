"""TWS 系平台附件下载器的签名 URL 刷新逻辑。"""

from urllib.parse import urljoin

from crawler_scrapy.sites.sxjm.download_attachments import (
    AccessBlocked,
    AttachmentDownloader,
)


class TwsAttachmentDownloader(AttachmentDownloader):
    """下载前通过 file/query 刷新短期有效的 CDN 签名地址。"""

    site_config = None

    def _refresh_attachment_url(self, attachment: dict) -> str:
        file_id = str(attachment.get("source_file_id") or "").strip()
        if not file_id:
            url = str(attachment.get("file_url") or "").strip()
            self._validate_url(url)
            return url

        if self.site_config is None:
            raise RuntimeError(f"{self.site_label} 缺少附件站点配置")
        response = self.session.get(
            f"{self.site_config.BIDDING_FILE_QUERY_URL}/{file_id}",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": self.site_config.WEB_BASE_URL,
                "Referer": f"{self.site_config.WEB_BASE_URL}/",
            },
            timeout=(self.config.connect_timeout, self.config.read_timeout),
        )
        try:
            if response.status_code in {403, 429}:
                raise AccessBlocked(
                    f"{self.site_label}附件元数据接口返回 "
                    f"{response.status_code}：file_id={file_id}"
                )
            response.raise_for_status()
            payload = response.json()
        finally:
            response.close()

        info = payload.get("data") if str(payload.get("code")) == "200" else None
        if not isinstance(info, dict):
            raise RuntimeError(
                f"{self.site_label}附件元数据不可用：file_id={file_id}"
            )
        raw_url = str(info.get("url") or info.get("downloadUrl") or "").strip()
        if not raw_url:
            raise RuntimeError(
                f"{self.site_label}附件元数据没有下载地址：file_id={file_id}"
            )
        url = urljoin(self.site_config.API_ORIGIN + "/", raw_url)
        self._validate_url(url)
        attachment["file_url"] = url
        if not attachment.get("file_name"):
            attachment["file_name"] = str(
                info.get("fileName") or info.get("name") or ""
            ).strip() or None
        return url

    def _download(self, row, attachment, final_path):
        refreshed_url = self._refresh_attachment_url(attachment)
        metadata = super()._download(row, attachment, final_path)
        # 公共下载器会把 metadata 同步到顶层附件及 trace 中。
        metadata["file_url"] = refreshed_url
        return metadata
