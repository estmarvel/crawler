"""五站统一的公告/附件分阶段运行器。

站点 shell 入口只负责选择站点；所有可配置项、断点、限速、快照、日志和
附件回写逻辑集中在这里，避免五份脚本逐渐产生不一致行为。
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class SiteProfile:
    sections_arg: str
    default_sections: str
    default_outbound: str
    attachment_module: str
    supports_channels: bool = False
    parse_pdf_arg: bool = False


PROFILES = {
    "sxjm": SiteProfile(
        "sections",
        "zbgg,cggg,hxr,cjhxr,zbjg,cjgg,zbjh,zzgg",
        "direct",
        "crawler_scrapy.sites.sxjm.download_attachments",
        supports_channels=True,
    ),
    "sxzwfw": SiteProfile(
        "sections",
        "zbjh,zbgg_zys,bg,hxr,gs,qt",
        "direct",
        "crawler_scrapy.sites.sxzwfw.download_attachments",
    ),
    "bitbid": SiteProfile(
        "categories",
        "plan,tender,candidate,award",
        "direct",
        "crawler_scrapy.sites.bitbid.download_attachments",
        parse_pdf_arg=True,
    ),
    "huaxin": SiteProfile(
        "sections",
        "zbgg_zys,hxr,gs,zbjh",
        "static",
        "crawler_scrapy.sites.huaxin.download_attachments",
    ),
    "jiubang": SiteProfile(
        "sections",
        "zbgg_zys,hxr,gs,zbjh",
        "static",
        "crawler_scrapy.sites.jiubang.download_attachments",
    ),
}


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("不能小于 0")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("不能小于 0")
    return parsed


def build_parser(site: str) -> argparse.ArgumentParser:
    profile = PROFILES[site]
    parser = argparse.ArgumentParser(
        description=f"{site} 公告与附件统一可恢复运行入口"
    )
    parser.add_argument("--phase", choices=("all", "notices", "attachments"), default="all")
    parser.add_argument("--output-root", type=Path, default=Path("new_output"))
    parser.add_argument("--days", type=_positive_int, default=180)
    parser.add_argument("--all-history", "--all", action="store_true")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--sections", default=profile.default_sections)
    parser.add_argument("--channels", default="yfxm,zbxm,fzxm,jycg")
    parser.add_argument("--page-size", type=_positive_int, default=100)
    parser.add_argument("--max-records", type=_positive_int, default=1_000_000)
    parser.add_argument("--max-pages", type=_positive_int, default=10_000)
    parser.add_argument("--outbound-mode", choices=("direct", "static"), default=profile.default_outbound)
    parser.add_argument("--concurrency", type=_positive_int, default=2)
    parser.add_argument("--delay-min", type=_nonnegative_float, default=3.0)
    parser.add_argument("--delay-max", type=_nonnegative_float, default=5.0)
    parser.add_argument("--responses-per-chunk", type=_positive_int, default=400)
    parser.add_argument("--cooldown-min", type=_positive_int, default=180)
    parser.add_argument("--cooldown-max", type=_positive_int, default=300)
    parser.add_argument("--request-timeout", type=_positive_int, default=300)
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--refresh-notices", action="store_true")
    parser.add_argument("--attachment-connect-timeout", type=_positive_int, default=30)
    parser.add_argument("--attachment-read-timeout", type=_positive_int, default=900)
    parser.add_argument("--attachment-retries", type=_nonnegative_int, default=4)
    parser.add_argument("--attachment-min-delay", type=_nonnegative_float, default=2.0)
    parser.add_argument("--attachment-max-delay", type=_nonnegative_float, default=5.0)
    parser.add_argument("--max-attachments", type=_nonnegative_int, default=0)
    return parser


class SiteRunner:
    def __init__(self, site: str, args: argparse.Namespace) -> None:
        self.site = site
        self.profile = PROFILES[site]
        self.args = args
        self.project_dir = Path(__file__).resolve().parents[1]
        self.output_root = args.output_root.expanduser()
        if not self.output_root.is_absolute():
            self.output_root = (self.project_dir / self.output_root).resolve()
        else:
            self.output_root = self.output_root.resolve()
        self.python = self._python_command()
        self.start_date, self.end_date = self._date_window()
        self.scope_material = json.dumps(
            {
                "site": site,
                "start": self.start_date,
                "end": self.end_date,
                "all": args.all_history,
                "sections": args.sections,
                "channels": args.channels if self.profile.supports_channels else None,
                "page_size": args.page_size,
                "max_records": args.max_records,
                "max_pages": args.max_pages,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(self.scope_material.encode()).hexdigest()[:12]
        window = "all" if args.all_history else f"{self.start_date}_to_{self.end_date}"
        self.scope_key = f"{window}_{digest}"
        self.site_dir = self.output_root / site
        # 兼容 2026-08-04/05 已由旧 SXJM 可恢复入口创建的全历史断点。
        # 仅在当前同样请求全历史、且恰好存在一个未完成断点时接管，避免重扫
        # 已排队列表；公告级去重索引仍是第二道保护。
        if site == "sxjm" and args.all_history:
            legacy_root = self.site_dir / "state" / "runner"
            candidates = [
                path
                for path in legacy_root.glob("all_*")
                if (path / "chunk").exists() and not (path / "notices_complete").exists()
            ]
            if len(candidates) == 1:
                self.scope_key = candidates[0].name
        self.log_dir = self.site_dir / "logs"
        self.state_dir = self.site_dir / "state" / "runner" / self.scope_key
        self.job_dir = self.site_dir / "state" / "jobs" / "notices" / self.scope_key
        self.lock_path = self.site_dir / "state" / "resumable.lock"
        for path in (self.log_dir, self.state_dir, self.job_dir):
            path.mkdir(parents=True, exist_ok=True)

    def _python_command(self) -> str:
        configured = os.environ.get("CRAWLER_PYTHON_COMMAND", "").strip()
        candidates = [
            configured,
            str(self.project_dir / ".venv" / "bin" / "python"),
            "/home/vipuser/miniconda3/envs/myenv/bin/python",
            sys.executable,
        ]
        for candidate in candidates:
            try:
                if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
                    return candidate
            except OSError:
                continue
        raise RuntimeError("找不到可执行 Python；请设置 CRAWLER_PYTHON_COMMAND")

    def _date_window(self) -> tuple[str, str]:
        if self.args.all_history:
            if self.args.start_date or self.args.end_date:
                raise ValueError("--all/--all-history 不能和显式日期同时使用")
            # SXZWFW 列表接口必须提交日期；其余站点不传日期即可表示全部历史。
            return ("2020-01-01", date.today().isoformat()) if self.site == "sxzwfw" else ("", "")
        end = date.fromisoformat(self.args.end_date) if self.args.end_date else date.today()
        start = (
            date.fromisoformat(self.args.start_date)
            if self.args.start_date
            else end - timedelta(days=self.args.days - 1)
        )
        if start > end:
            raise ValueError("start-date 不能晚于 end-date")
        return start.isoformat(), end.isoformat()

    def _spider_args(self) -> list[str]:
        values = [
            "-a", f"{self.profile.sections_arg}={self.args.sections}",
            "-a", f"page_size={min(self.args.page_size, 100)}",
            "-a", f"max_records={self.args.max_records}",
            "-a", f"max_pages={self.args.max_pages}",
        ]
        if self.profile.supports_channels:
            values += ["-a", f"channels={self.args.channels}"]
        if self.profile.parse_pdf_arg:
            # 签章 PDF 作为附件在第二阶段下载；公告阶段使用 API/HTML 正文。
            values += ["-a", "parse_pdf=false"]
        if self.args.all_history and self.site != "sxzwfw":
            if self.site == "sxjm":
                values += ["-a", "days="]
            return values
        return values + ["-a", f"start_date={self.start_date}", "-a", f"end_date={self.end_date}"]

    def _settings(self, request_delay: float) -> list[str]:
        check_updates = "False" if self.args.check_updates else "True"
        values = {
            "CRAWLER_OUTBOUND_MODE": self.args.outbound_mode,
            "NOTICE_OUTPUT_ROOT": str(self.output_root),
            "NOTICE_DEDUP_ROOT": str(self.output_root),
            "NOTICE_DEDUP_ENABLED": "True",
            "NOTICE_DEDUP_SKIP_KNOWN_IDENTITIES": check_updates,
            "NOTICE_SNAPSHOT_ENABLED": "True",
            "NOTICE_SNAPSHOT_REQUIRED": "False",
            "NOTICE_AI_ENABLED": "False",
            "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": "False",
            "NOTICE_RESOLVE_ATTACHMENT_URLS": "True",
            "FILES_STORE": str(self.output_root),
            "JOBDIR": str(self.job_dir),
            "HTTPCACHE_ENABLED": "False",
            "CONCURRENT_REQUESTS": str(self.args.concurrency),
            "CONCURRENT_REQUESTS_PER_DOMAIN": str(self.args.concurrency),
            "DOWNLOAD_DELAY": f"{request_delay:.3f}",
            # 每批从 3~5 秒中抽一个间隔；关闭 Scrapy 的 0.5~1.5 倍扩散，
            # 避免实际落到 3 秒以下。AutoThrottle 遇到延迟升高时仍可主动降速。
            "RANDOMIZE_DOWNLOAD_DELAY": "False",
            "AUTOTHROTTLE_ENABLED": "True",
            "AUTOTHROTTLE_START_DELAY": f"{request_delay:.3f}",
            "AUTOTHROTTLE_TARGET_CONCURRENCY": "0.5",
            "AUTOTHROTTLE_MAX_DELAY": "180",
            "RETRY_TIMES": "1",
            "DOWNLOAD_TIMEOUT": str(self.args.request_timeout),
            "CLOSESPIDER_PAGECOUNT": str(self.args.responses_per_chunk),
            "DIRECT_CONCURRENT_REQUESTS": str(self.args.concurrency),
            "DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN": str(self.args.concurrency),
            "DIRECT_DOWNLOAD_DELAY": f"{request_delay:.3f}",
            "DIRECT_AUTOTHROTTLE_START_DELAY": f"{request_delay:.3f}",
            "DIRECT_AUTOTHROTTLE_TARGET_CONCURRENCY": "0.5",
            "DIRECT_AUTOTHROTTLE_MAX_DELAY": "180",
            "DIRECT_RETRY_TIMES": "1",
            "DIRECT_DOWNLOAD_TIMEOUT": str(self.args.request_timeout),
            "DIRECT_MAX_RESPONSES_PER_RUN": str(self.args.responses_per_chunk),
            "DIRECT_GUARD_CONSECUTIVE_LIMIT": "1",
            "DIRECT_GUARD_TOTAL_LIMIT": "1",
            "DIRECT_GUARD_BASE_BACKOFF": "180",
            "DIRECT_GUARD_MAX_BACKOFF": "300",
            "LOGSTATS_INTERVAL": "15",
            "LOG_LEVEL": "INFO",
        }
        result: list[str] = []
        for key, value in values.items():
            result += ["-s", f"{key}={value}"]
        return result

    def _run_logged(self, command: Sequence[str], log_path: Path) -> int:
        with log_path.open("a", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                cwd=self.project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    log_file.write(line)
                    log_file.flush()
                return process.wait()
            except KeyboardInterrupt:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.terminate()
                raise

    def run_notices(self) -> int:
        complete_path = self.state_dir / "notices_complete"
        if complete_path.exists() and not self.args.refresh_notices and not self.args.check_updates:
            print("同一范围公告阶段已完成；加 --refresh-notices 可重新扫描新公告。", flush=True)
            return 0
        with self.lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"已有 {self.site} 任务运行：{self.lock_path}", file=sys.stderr)
                return 5
            chunk_path = self.state_dir / "chunk"
            chunk = int(chunk_path.read_text().strip() or "0") if chunk_path.exists() else 0
            while True:
                chunk += 1
                chunk_path.write_text(f"{chunk}\n", encoding="utf-8")
                delay = random.uniform(self.args.delay_min, self.args.delay_max)
                stamp = time.strftime("%Y%m%d_%H%M%S")
                log_path = self.log_dir / f"{self.scope_key}_chunk_{chunk:05d}_{stamp}.log"
                print(
                    f"[{time.strftime('%F %T')}] {self.site} 公告第{chunk}批："
                    f"并发={self.args.concurrency} 间隔={delay:.2f}s "
                    f"响应预算={self.args.responses_per_chunk} 日志={log_path}",
                    flush=True,
                )
                command = [self.python, "-m", "scrapy", "crawl", self.site]
                command += self._spider_args() + self._settings(delay)
                try:
                    status = self._run_logged(command, log_path)
                except KeyboardInterrupt:
                    print("公告阶段已停止；JSON、快照、去重索引与 JOBDIR 均已保留。", flush=True)
                    return 130
                content = log_path.read_text(encoding="utf-8", errors="replace")
                if status != 0:
                    print(f"公告阶段异常退出 code={status}，检查 {log_path}", file=sys.stderr)
                    return status
                if any(marker in content for marker in ("direct_access_blocked", "static_proxy_auth_failed")):
                    print("出口被限制或代理认证失败，已停止自动请求。", file=sys.stderr)
                    return 3
                if "'finish_reason': 'closespider_pagecount'" in content:
                    cooldown = random.randint(self.args.cooldown_min, self.args.cooldown_max)
                    print(f"达到响应预算，冷却 {cooldown} 秒；Ctrl-C 可安全停止。", flush=True)
                    try:
                        time.sleep(cooldown)
                    except KeyboardInterrupt:
                        print("已在冷却期停止；下次从断点继续。", flush=True)
                        return 130
                    continue
                if "'finish_reason': 'finished'" not in content:
                    print(f"没有识别到正常结束原因：{log_path}", file=sys.stderr)
                    return 1
                complete_path.write_text(
                    f"completed_at={time.strftime('%F %T')}\nscope={self.scope_material}\n",
                    encoding="utf-8",
                )
                print(f"{self.site} 公告阶段完成。", flush=True)
                return 0

    def run_attachments(self) -> int:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        log_path = self.log_dir / f"attachments_{stamp}.log"
        command = [
            self.python,
            "-m",
            self.profile.attachment_module,
            "--output-root", str(self.output_root),
            "--outbound-mode", self.args.outbound_mode,
            "--connect-timeout", str(self.args.attachment_connect_timeout),
            "--read-timeout", str(self.args.attachment_read_timeout),
            "--retries", str(self.args.attachment_retries),
            "--min-delay", str(self.args.attachment_min_delay),
            "--max-delay", str(self.args.attachment_max_delay),
            "--max-attachments", str(self.args.max_attachments),
        ]
        print(f"[{time.strftime('%F %T')}] {self.site} 附件阶段，日志={log_path}", flush=True)
        try:
            return self._run_logged(command, log_path)
        except KeyboardInterrupt:
            return 130

    def run(self) -> int:
        if self.args.delay_max < self.args.delay_min:
            raise ValueError("delay-max 不能小于 delay-min")
        if self.args.cooldown_max < self.args.cooldown_min:
            raise ValueError("cooldown-max 不能小于 cooldown-min")
        if self.args.attachment_max_delay < self.args.attachment_min_delay:
            raise ValueError("attachment-max-delay 不能小于 attachment-min-delay")
        if self.args.outbound_mode == "static":
            username = os.environ.get("HUAXIN_PROXY_USERNAME", "")
            password = os.environ.get("HUAXIN_PROXY_PASSWORD", "")
            if (not username or not password) and sys.stdin.isatty():
                if not username:
                    username = input("固定代理用户名: ").strip()
                if not password:
                    password = getpass.getpass("固定代理密码: ")
                os.environ["HUAXIN_PROXY_USERNAME"] = username
                os.environ["HUAXIN_PROXY_PASSWORD"] = password
            if not username or not password:
                raise ValueError(
                    "static 出口需要 HUAXIN_PROXY_USERNAME/HUAXIN_PROXY_PASSWORD；"
                    "交互终端会自动提示输入"
                )
        if self.args.phase in {"all", "notices"}:
            status = self.run_notices()
            if status:
                return status
        if self.args.phase in {"all", "attachments"}:
            return self.run_attachments()
        return 0


def main(site: str, argv: list[str] | None = None) -> int:
    if site not in PROFILES:
        print(f"不支持站点：{site}", file=sys.stderr)
        return 2
    try:
        args = build_parser(site).parse_args(argv)
        return SiteRunner(site, args).run()
    except (RuntimeError, ValueError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in PROFILES:
        print(
            f"用法：python -m crawler_scrapy.site_runner <{'|'.join(PROFILES)}> [选项]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
