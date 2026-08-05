"""Download SXJM attachments independently from notice collection.

The notice spider first exports attachment metadata to JSON.  This module then
downloads those URLs to deterministic paths and writes the resulting metadata
back to the matching JSON record (and its CSV row).  A ``.part`` file is kept
for interrupted transfers so a later run can resume with an HTTP Range request.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import mimetypes
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

import requests


ALLOWED_ATTACHMENT_HOSTS = {"www.sxccdzzcpt.cn"}
BLOCK_STATUSES = {403, 429}


class AccessBlocked(RuntimeError):
    """The source explicitly rejected or rate-limited the current IP."""


def _safe_path_part(value: Any, fallback: str, max_length: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return (text or fallback)[:max_length]


def _truncate_path_component(value: str, max_bytes: int = 240) -> str:
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    suffix = Path(value).suffix
    marker = f"_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"
    budget = max(1, max_bytes - len((marker + suffix).encode("utf-8")))
    prefix: list[str] = []
    used = 0
    body = value[: -len(suffix)] if suffix else value
    for character in body:
        size = len(character.encode("utf-8"))
        if used + size > budget:
            break
        prefix.append(character)
        used += size
    return f"{''.join(prefix).rstrip('._ ')}{marker}{suffix}"


def attachment_storage_path(record: Mapping[str, Any], attachment: Mapping[str, Any]) -> str:
    """Return the same stable relative layout used by NoticeFilesPipeline."""

    url = str(attachment.get("file_url") or "").strip()
    platform = _safe_path_part(record.get("平台代码"), "sxjm", 80)
    notice_type = _safe_path_part(record.get("公告类型"), "unknown_type", 80)
    notice_id = _safe_path_part(record.get("公告ID"), "unknown_notice", 120)
    source_id = _safe_path_part(
        attachment.get("source_file_id")
        or hashlib.sha256(url.encode("utf-8")).hexdigest()[:20],
        "unknown_file",
        120,
    )
    file_name = str(attachment.get("file_name") or "").strip()
    if not file_name:
        suffix = Path(urlsplit(url).path).suffix
        file_name = f"attachment{suffix or '.bin'}"
    file_name = _safe_path_part(
        Path(file_name.replace("\\", "/")).name,
        "attachment.bin",
        10_000,
    )
    component = _truncate_path_component(f"{source_id}_{file_name}")
    return f"{platform}/attachments/{notice_type}/{notice_id}/{component}"


def _file_md5(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as file_object:
        while chunk := file_object.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _content_type(response: requests.Response, path: Path) -> str | None:
    header = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
    return header or mimetypes.guess_type(path.name)[0]


def _existing_metadata(path: Path) -> dict[str, Any]:
    return {
        "storage_path": None,
        "file_hash": _file_md5(path),
        "file_size_bytes": path.stat().st_size,
        "file_type": mimetypes.guess_type(path.name)[0],
        "parse_status": "CACHED_NO_OCR",
    }


@dataclass
class DownloadConfig:
    output_root: Path
    connect_timeout: float = 30.0
    read_timeout: float = 900.0
    retries: int = 4
    retry_base_delay: float = 15.0
    retry_max_delay: float = 300.0
    min_delay: float = 2.0
    max_delay: float = 5.0
    chunk_size: int = 1024 * 1024
    max_attachments: int = 0
    outbound_mode: str = "direct"


class AttachmentDownloader:
    site_code = "sxjm"
    site_label = "SXJM"
    allowed_attachment_hosts = ALLOWED_ATTACHMENT_HOSTS
    allowed_attachment_schemes = {"https"}

    def __init__(self, config: DownloadConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        # Match the spider's verified direct-only transport.  Ambient shell
        # proxy variables must not silently change the crawler's public exit.
        self.session.trust_env = False
        if self.config.outbound_mode == "static":
            endpoint = os.environ.get(
                "HUAXIN_PROXY_ENDPOINT", "http://210.51.27.8:10000"
            ).strip()
            username = os.environ.get("HUAXIN_PROXY_USERNAME", "")
            password = os.environ.get("HUAXIN_PROXY_PASSWORD", "")
            parsed = urlsplit(endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("固定代理地址必须是 http(s)://host:port")
            if not username or not password:
                raise ValueError(
                    "附件使用 static 出口时必须设置 HUAXIN_PROXY_USERNAME/"
                    "HUAXIN_PROXY_PASSWORD"
                )
            host = parsed.hostname
            if parsed.port:
                host = f"{host}:{parsed.port}"
            proxy_url = urlunsplit(
                (
                    parsed.scheme,
                    f"{quote(username, safe='')}:{quote(password, safe='')}@{host}",
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
            }
        )
        self.total = 0
        self.processed = 0
        self.downloaded = 0
        self.cached = 0
        self.failed = 0
        self.missing_url = 0
        self._stop_after_current = False

    @property
    def json_dir(self) -> Path:
        return self.config.output_root / self.site_code / "json"

    def _json_paths(self) -> list[Path]:
        return sorted(self.json_dir.glob("*.json"))

    @staticmethod
    def _load_rows(path: Path) -> list[dict[str, Any]]:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"JSON 不是公告对象数组：{path}")
        return rows

    def count_tasks(self, paths: Iterable[Path]) -> int:
        total = 0
        for path in paths:
            for row in self._load_rows(path):
                attachments = row.get("附件") or []
                if isinstance(attachments, list):
                    total += sum(isinstance(value, dict) for value in attachments)
        if self.config.max_attachments > 0:
            return min(total, self.config.max_attachments)
        return total

    def _apply_metadata(
        self,
        row: dict[str, Any],
        index: int,
        metadata: Mapping[str, Any],
    ) -> None:
        attachments = row.get("附件")
        if not isinstance(attachments, list) or index >= len(attachments):
            return
        attachment = attachments[index]
        if not isinstance(attachment, dict):
            return
        attachment.update(metadata)
        trace = row.get("_trace")
        if not isinstance(trace, dict):
            return
        export_metadata = trace.get("exportMetadata")
        if not isinstance(export_metadata, dict):
            return
        trace_attachments = export_metadata.get("attachments")
        if not isinstance(trace_attachments, list) or index >= len(trace_attachments):
            export_metadata["attachments"] = [
                dict(value) for value in attachments if isinstance(value, dict)
            ]
            return
        if isinstance(trace_attachments[index], dict):
            trace_attachments[index].update(metadata)

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in self.allowed_attachment_schemes
            or (parsed.hostname or "").lower() not in self.allowed_attachment_hosts
        ):
            raise ValueError(
                f"拒绝下载非 {self.site_label} 允许域名/协议的附件地址：{url}"
            )

    def _download_once(
        self,
        url: str,
        final_path: Path,
        referer: str,
    ) -> tuple[requests.Response, int, float]:
        part_path = final_path.with_name(f"{final_path.name}.part")
        start_at = part_path.stat().st_size if part_path.exists() else 0
        headers = {"Referer": referer} if referer else {}
        if start_at > 0:
            headers["Range"] = f"bytes={start_at}-"

        started = time.monotonic()
        response = self.session.get(
            url,
            headers=headers,
            stream=True,
            timeout=(self.config.connect_timeout, self.config.read_timeout),
            allow_redirects=True,
        )
        # Do not silently follow a source-controlled redirect to an unrelated
        # host.  Current SXJM attachment URLs remain on this verified host.
        try:
            self._validate_url(response.url)
        except ValueError:
            response.close()
            raise
        if response.status_code in BLOCK_STATUSES:
            response.close()
            raise AccessBlocked(f"附件服务器返回 {response.status_code}：{url}")
        if response.status_code == 416 and start_at > 0:
            total_text = str(response.headers.get("Content-Range") or "").rsplit("/", 1)[-1]
            if total_text.isdigit() and int(total_text) == start_at:
                response.close()
                part_path.replace(final_path)
                completed = requests.Response()
                completed.status_code = 206
                completed.url = url
                completed.headers["Content-Type"] = mimetypes.guess_type(final_path.name)[0] or ""
                return completed, start_at, max(time.monotonic() - started, 0.001)
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise

        resumed = start_at > 0 and response.status_code == 206
        mode = "ab" if resumed else "wb"
        if not resumed:
            start_at = 0
        expected_chunk = response.headers.get("Content-Length")
        expected_total = (
            start_at + int(expected_chunk)
            if expected_chunk and expected_chunk.isdigit()
            else None
        )
        final_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with part_path.open(mode) as file_object:
                for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                    if chunk:
                        file_object.write(chunk)
                file_object.flush()
                os.fsync(file_object.fileno())
            actual_total = part_path.stat().st_size
            if expected_total is not None and actual_total != expected_total:
                raise IOError(
                    f"附件长度不完整：expected={expected_total} "
                    f"actual={actual_total} url={url}"
                )
            part_path.replace(final_path)
        except Exception:
            response.close()
            raise
        return response, actual_total, max(time.monotonic() - started, 0.001)

    def _download(
        self,
        row: Mapping[str, Any],
        attachment: Mapping[str, Any],
        final_path: Path,
    ) -> dict[str, Any]:
        url = str(attachment.get("file_url") or "").strip()
        self._validate_url(url)
        referer = str(row.get("详情页链接") or "").strip()
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            response: requests.Response | None = None
            try:
                response, size, elapsed = self._download_once(url, final_path, referer)
                metadata = {
                    "storage_path": final_path.relative_to(self.config.output_root).as_posix(),
                    "file_hash": _file_md5(final_path),
                    "file_size_bytes": size,
                    "file_type": _content_type(response, final_path),
                    "parse_status": "DOWNLOADED_NO_OCR",
                    "_elapsed": elapsed,
                }
                response.close()
                return metadata
            except AccessBlocked:
                raise
            except (requests.RequestException, OSError, ValueError) as exc:
                last_error = exc
                if response is not None:
                    response.close()
                if attempt >= self.config.retries:
                    break
                delay = min(
                    self.config.retry_base_delay * (2 ** attempt),
                    self.config.retry_max_delay,
                ) + random.uniform(0, min(3.0, self.config.retry_base_delay))
                print(
                    f"[附件重试] attempt={attempt + 1}/{self.config.retries} "
                    f"等待={delay:.1f}s error={exc}",
                    flush=True,
                )
                time.sleep(delay)
        raise RuntimeError(str(last_error or "附件下载失败"))

    @staticmethod
    def _write_json_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temp_path.open("w", encoding="utf-8") as file_object:
            json.dump(rows, file_object, ensure_ascii=False, indent=2)
            file_object.write("\n")
            file_object.flush()
            os.fsync(file_object.fileno())
        temp_path.replace(path)

    @staticmethod
    def _sync_csv(json_path: Path, rows: list[dict[str, Any]]) -> None:
        csv_path = json_path.parent.parent / "csv" / f"{json_path.stem}.csv"
        if not csv_path.exists():
            return
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file_object:
            reader = csv.DictReader(file_object)
            fieldnames = list(reader.fieldnames or [])
            csv_rows = list(reader)
        if "附件" not in fieldnames:
            print(f"[附件警告] CSV 没有附件列，未同步：{csv_path}", flush=True)
            return
        if len(csv_rows) != len(rows):
            print(
                f"[附件警告] JSON/CSV 行数不同，未同步 CSV："
                f"json={len(rows)} csv={len(csv_rows)} file={csv_path}",
                flush=True,
            )
            return
        for csv_row, json_row in zip(csv_rows, rows):
            csv_row["附件"] = json.dumps(
                json_row.get("附件") or [], ensure_ascii=False, separators=(",", ":")
            )
        temp_path = csv_path.with_name(f".{csv_path.name}.{os.getpid()}.tmp")
        with temp_path.open("w", encoding="utf-8-sig", newline="") as file_object:
            writer = csv.DictWriter(file_object, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(csv_rows)
            file_object.flush()
            os.fsync(file_object.fileno())
        temp_path.replace(csv_path)

    def _flush(self, path: Path, rows: list[dict[str, Any]]) -> None:
        self._write_json_atomic(path, rows)
        self._sync_csv(path, rows)

    def _progress(self, row: Mapping[str, Any], attachment: Mapping[str, Any], status: str) -> None:
        print(
            f"[附件进度] {self.processed}/{self.total} "
            f"下载={self.downloaded} 已有={self.cached} 失败={self.failed} "
            f"公告ID={row.get('公告ID', '')} 状态={status} "
            f"文件={attachment.get('file_name', '')}",
            flush=True,
        )

    def run(self) -> int:
        paths = self._json_paths()
        if not paths:
            print(
                f"没有找到 {self.site_label} JSON：{self.json_dir}",
                file=sys.stderr,
                flush=True,
            )
            return 2
        self.total = self.count_tasks(paths)
        print(
            f"[附件启动] JSON文件={len(paths)} 附件={self.total} "
            f"连接超时={self.config.connect_timeout}s 读取超时={self.config.read_timeout}s",
            flush=True,
        )
        if self.total == 0:
            return 0

        for path in paths:
            rows = self._load_rows(path)
            dirty = False
            try:
                for row in rows:
                    attachments = row.get("附件") or []
                    if not isinstance(attachments, list):
                        continue
                    for index, attachment in enumerate(attachments):
                        if not isinstance(attachment, dict):
                            continue
                        if self.config.max_attachments > 0 and self.processed >= self.config.max_attachments:
                            self._stop_after_current = True
                            break
                        self.processed += 1
                        url = str(attachment.get("file_url") or "").strip()
                        relative_path = attachment_storage_path(row, attachment)
                        final_path = self.config.output_root / relative_path
                        made_request = False
                        try:
                            if not url:
                                metadata = {
                                    "storage_path": None,
                                    "file_hash": None,
                                    "file_size_bytes": None,
                                    "parse_status": "MISSING_URL",
                                }
                                self.missing_url += 1
                                status = "缺少URL"
                            elif final_path.is_file() and final_path.stat().st_size > 0:
                                metadata = _existing_metadata(final_path)
                                metadata["storage_path"] = relative_path
                                self.cached += 1
                                status = "已存在，跳过下载"
                            else:
                                metadata = self._download(row, attachment, final_path)
                                made_request = True
                                elapsed = float(metadata.pop("_elapsed"))
                                self.downloaded += 1
                                speed = int(int(metadata["file_size_bytes"]) / elapsed / 1024)
                                status = f"下载完成 {speed}KiB/s"
                            self._apply_metadata(row, index, metadata)
                            dirty = True
                        except AccessBlocked:
                            raise
                        except Exception as exc:  # keep other attachments recoverable
                            self.failed += 1
                            self._apply_metadata(
                                row,
                                index,
                                {
                                    "storage_path": None,
                                    "file_hash": None,
                                    "file_size_bytes": None,
                                    "parse_status": "DOWNLOAD_FAILED",
                                },
                            )
                            dirty = True
                            status = f"失败 {exc}"
                        self._progress(row, attachment, status)
                        if made_request and self.processed < self.total:
                            time.sleep(random.uniform(self.config.min_delay, self.config.max_delay))
                    if self._stop_after_current:
                        break
            except (KeyboardInterrupt, AccessBlocked):
                if dirty:
                    self._flush(path, rows)
                raise
            if dirty:
                self._flush(path, rows)
                print(f"[附件落盘] 已同步 JSON/CSV：{path.name}", flush=True)
            if self._stop_after_current:
                break

        print(
            f"[附件完成] 总计={self.processed} 下载={self.downloaded} "
            f"已有={self.cached} 缺URL={self.missing_url} 失败={self.failed}",
            flush=True,
        )
        return 4 if self.failed else 0


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立、可恢复地下载 SXJM JSON 中的附件")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    parser.add_argument("--connect-timeout", type=_positive_float, default=30.0)
    parser.add_argument("--read-timeout", type=_positive_float, default=900.0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--retry-base-delay", type=_positive_float, default=15.0)
    parser.add_argument("--retry-max-delay", type=_positive_float, default=300.0)
    parser.add_argument("--min-delay", type=float, default=2.0)
    parser.add_argument("--max-delay", type=float, default=5.0)
    parser.add_argument("--max-attachments", type=int, default=0)
    parser.add_argument(
        "--outbound-mode", choices=("direct", "static"), default="direct"
    )
    return parser


def run_downloader(
    downloader_class: type[AttachmentDownloader],
    argv: list[str] | None = None,
) -> int:
    """共享五站附件命令行、锁、断点与退出码处理。"""

    parser = build_argument_parser()
    parser.description = f"独立、可恢复地下载 {downloader_class.site_label} JSON 中的附件"
    args = parser.parse_args(argv)
    if args.retries < 0 or args.min_delay < 0 or args.max_delay < args.min_delay:
        print("重试次数/下载间隔参数无效", file=sys.stderr)
        return 2

    output_root = args.output_root.expanduser().resolve()
    state_dir = output_root / downloader_class.site_code / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "resumable.lock"
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                f"已有 {downloader_class.site_label} 采集/附件任务在运行：{lock_path}",
                file=sys.stderr,
            )
            return 5
        try:
            downloader = downloader_class(
                DownloadConfig(
                    output_root=output_root,
                    connect_timeout=args.connect_timeout,
                    read_timeout=args.read_timeout,
                    retries=args.retries,
                    retry_base_delay=args.retry_base_delay,
                    retry_max_delay=args.retry_max_delay,
                    min_delay=args.min_delay,
                    max_delay=args.max_delay,
                    max_attachments=args.max_attachments,
                    outbound_mode=args.outbound_mode,
                )
            )
            return downloader.run()
        except KeyboardInterrupt:
            print(
                "\n[附件停止] 已完成文件已保留；再次运行会跳过，.part 将断点续传。",
                flush=True,
            )
            return 130
        except AccessBlocked as exc:
            print(
                f"[附件停止] {exc}。为保护出口 IP 不自动重试，请稍后再次运行。",
                file=sys.stderr,
                flush=True,
            )
            return 3
        except ValueError as exc:
            print(f"附件配置错误：{exc}", file=sys.stderr, flush=True)
            return 2


def main(argv: list[str] | None = None) -> int:
    return run_downloader(AttachmentDownloader, argv)


if __name__ == "__main__":
    raise SystemExit(main())
