"""独立、可恢复地下载国信 e 采公开公告 PDF。"""

from __future__ import annotations

from pathlib import Path

import requests

from crawler_scrapy.sites.gxebidding.reparse_output import main as reparse_main
from crawler_scrapy.sites.sxjm.download_attachments import (
    AttachmentDownloader,
    build_argument_parser,
    run_downloader,
)


class GxebiddingAttachmentDownloader(AttachmentDownloader):
    site_code = "gxebidding"
    site_label = "GXEBIDDING"
    allowed_attachment_hosts = {"gx.e-bidding.org"}
    allowed_attachment_schemes = {"https"}

    def _download_once(
        self,
        url: str,
        final_path: Path,
        referer: str,
    ) -> tuple[requests.Response, int, float]:
        response, size, elapsed = super()._download_once(url, final_path, referer)
        with final_path.open("rb") as file_object:
            signature = file_object.read(5)
        if signature != b"%PDF-":
            response.close()
            final_path.unlink(missing_ok=True)
            raise ValueError(f"附件响应不是 PDF：{url}")
        return response, size, elapsed


def main(argv: list[str] | None = None) -> int:
    options = build_argument_parser().parse_args(argv)
    result = run_downloader(GxebiddingAttachmentDownloader, argv)
    if result != 0:
        return result
    return reparse_main(["--output-root", str(options.output_root)])


if __name__ == "__main__":
    raise SystemExit(main())

