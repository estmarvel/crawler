"""多站统一的公告/附件分阶段运行器。

站点 shell 入口只负责选择站点；所有可配置项、断点、限速、快照、日志和
附件回写逻辑集中在这里，避免各站脚本逐渐产生不一致行为。
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from crawler_scrapy.ai.provider_profiles import (
    AUTO_PROVIDER,
    GLM52_MODEL,
    QWEN3_8B_MODEL,
    resolve_provider,
)


@dataclass(frozen=True)
class SiteProfile:
    sections_arg: str
    default_sections: str
    default_outbound: str
    attachment_module: str
    supports_channels: bool = False
    supports_project_types: bool = False
    parse_pdf_arg: bool = False
    default_channels: str = ""
    json_only: bool = False


PROFILES = {
    "sxjm": SiteProfile(
        "sections",
        "zbgg,cggg,hxr,cjhxr,zbjg,cjgg,zbjh,zzgg",
        "direct",
        "crawler_scrapy.sites.sxjm.download_attachments",
        supports_channels=True,
        default_channels="yfxm,zbxm,fzxm,jycg",
        json_only=True,
    ),
    "sxzwfw": SiteProfile(
        "sections",
        "zbjh,zbgg_zys,bg,hxr,gs,qt",
        "direct",
        "crawler_scrapy.sites.sxzwfw.download_attachments",
        json_only=True,
    ),
    "bitbid": SiteProfile(
        "categories",
        "plan,tender,candidate,award",
        "direct",
        "crawler_scrapy.sites.bitbid.download_attachments",
        parse_pdf_arg=True,
        json_only=True,
    ),
    "huaxin": SiteProfile(
        "sections",
        "zbgg_zys,hxr,gs,zbjh",
        "direct",
        "crawler_scrapy.sites.huaxin.download_attachments",
        json_only=True,
    ),
    "jiubang": SiteProfile(
        "sections",
        "zbgg_zys,hxr,gs,zbjh",
        "direct",
        "crawler_scrapy.sites.jiubang.download_attachments",
        json_only=True,
    ),
    "qianji": SiteProfile(
        "categories",
        "plan,tender,change,candidate,award",
        "direct",
        "crawler_scrapy.sites.qianji.download_attachments",
        supports_project_types=True,
        parse_pdf_arg=True,
        json_only=True,
    ),
    "sxjkzcpt": SiteProfile(
        "categories",
        "plan,tender,change,candidate,award,contract",
        "direct",
        "crawler_scrapy.sites.sxjkzcpt.download_attachments",
        supports_channels=True,
        default_channels="zbcg,qzbcg",
    ),
    "trade365": SiteProfile(
        "categories",
        "tender,change,candidate,award",
        "direct",
        "crawler_scrapy.sites.trade365.download_attachments",
        supports_project_types=True,
        json_only=True,
    ),
    "sxbid": SiteProfile(
        "categories",
        "plan,prequalification,tender,candidate,final_candidate,award,correction,contract",
        "direct",
        "crawler_scrapy.sites.sxbid.download_attachments",
        json_only=True,
    ),
    "sxxindian": SiteProfile(
        "feeds",
        (
            "bidding.plan.all,bidding.tender.engineering,bidding.tender.goods,"
            "bidding.tender.service,bidding.other.engineering,bidding.other.goods,"
            "bidding.other.service,bidding.prequalification.engineering,"
            "bidding.prequalification.goods,bidding.prequalification.service,"
            "bidding.change.engineering,bidding.change.goods,bidding.change.service,"
            "bidding.candidate.engineering,bidding.candidate.goods,"
            "bidding.candidate.service,bidding.award.engineering,bidding.award.goods,"
            "bidding.award.service,purchase.notice.all,purchase.change.all,"
            "purchase.award.all,purchase.contract.all,purchase.opinion.all,"
            "purchase.tender.all"
        ),
        "direct",
        "crawler_scrapy.sites.sxxindian.download_attachments",
        json_only=True,
    ),
    "runshihua": SiteProfile(
        "categories",
        (
            "prequalification,tender,purchase,prequalification_change,"
            "tender_change,purchase_change,candidate,award,"
            "candidate_correction,award_correction,control_price,"
            "control_price_change,cancellation,supplement,delay"
        ),
        "direct",
        "crawler_scrapy.sites.runshihua.download_attachments",
        parse_pdf_arg=True,
    ),
    "gxebidding": SiteProfile(
        "categories",
        "tender,change,candidate,award,termination",
        "direct",
        "crawler_scrapy.sites.gxebidding.download_attachments",
        supports_channels=True,
        parse_pdf_arg=True,
        default_channels="lawful,nonlawful,purchase",
    ),
    "lfggzyjy": SiteProfile(
        "tables",
        "gcjs_tender_plan,gcjs_notice,gcjs_zbhxrgs,gcjs_result_notice",
        "direct",
        "crawler_scrapy.sites.lfggzyjy.download_attachments",
    ),
    "sxzfcg": SiteProfile(
        "categories",
        "tender,award,change,contract",
        "direct",
        "crawler_scrapy.sites.sxzfcg.download_attachments",
    ),
    "sxty_ebidding": SiteProfile(
        "feeds",
        (
            "engineering.plan,engineering.tender,engineering.change,"
            "engineering.candidate,engineering.award,engineering.other,"
            "engineering.termination,enterprise.plan,enterprise.tender,"
            "enterprise.change,enterprise.candidate,enterprise.award,"
            "enterprise.other"
        ),
        "direct",
        "crawler_scrapy.sites.sxty_ebidding.download_attachments",
        json_only=True,
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
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--days", type=_positive_int, default=180)
    parser.add_argument("--all-history", "--all", action="store_true")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--sections", default=profile.default_sections)
    parser.add_argument(
        "--channels",
        default=profile.default_channels or "yfxm,zbxm,fzxm,jycg",
        help="站点频道；SXJKZCPT 支持 zbcg,qzbcg",
    )
    parser.add_argument(
        "--project-types",
        default="engineering,goods,service",
        help="Qianji/TRADE365 二级项目类型；其他网站忽略",
    )
    parser.add_argument("--page-size", type=_positive_int, default=100)
    parser.add_argument("--max-records", type=_positive_int, default=1_000_000)
    parser.add_argument(
        "--max-records-per-notice-type",
        type=_nonnegative_int,
        default=0,
        help=(
            "验收时按统一公告类型汇总限额；0 表示关闭。"
            "山西新点同时会在 Spider 调度层提前截止"
        ),
    )
    parser.add_argument("--max-pages", type=_positive_int, default=10_000)
    parser.add_argument(
        "--sample-mode",
        choices=("latest", "random"),
        default="latest",
        help="SXJKZCPT 抽样方式；latest=顺序，random=按历史页随机抽样",
    )
    parser.add_argument(
        "--sample-seed",
        type=int,
        default=20260806,
        help="SXJKZCPT 可复现随机抽样种子",
    )
    parser.add_argument("--outbound-mode", choices=("direct", "static"), default=profile.default_outbound)
    parser.add_argument("--concurrency", type=_positive_int, default=2)
    parser.add_argument("--delay-min", type=_nonnegative_float, default=3.0)
    parser.add_argument("--delay-max", type=_nonnegative_float, default=5.0)
    parser.add_argument("--responses-per-chunk", type=_positive_int, default=400)
    parser.add_argument(
        "--cooldown-min",
        type=_nonnegative_int,
        default=180,
        help="达到单批响应预算后的最短冷却秒数；0 可关闭批次冷却",
    )
    parser.add_argument(
        "--cooldown-max",
        type=_nonnegative_int,
        default=300,
        help="达到单批响应预算后的最长冷却秒数；0 可关闭批次冷却",
    )
    parser.add_argument("--request-timeout", type=_positive_int, default=300)
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--refresh-notices", action="store_true")
    parser.add_argument("--attachment-connect-timeout", type=_positive_int, default=30)
    parser.add_argument("--attachment-read-timeout", type=_positive_int, default=900)
    parser.add_argument("--attachment-retries", type=_nonnegative_int, default=4)
    parser.add_argument("--attachment-min-delay", type=_nonnegative_float, default=2.0)
    parser.add_argument("--attachment-max-delay", type=_nonnegative_float, default=5.0)
    parser.add_argument("--max-attachments", type=_nonnegative_int, default=0)
    parser.add_argument(
        "--ai-extract",
        action="store_true",
        help="启用网站限定的 AI 辅助抽取与证据裁决",
    )
    parser.add_argument(
        "--ai-provider",
        choices=(AUTO_PROVIDER, "zhipu", "siliconflow"),
        default=AUTO_PROVIDER,
        help=(
            "AI 提供方；auto 按模型名选择，GLM 默认智谱，"
            "Qwen/Qwen3-8B 默认硅基流动"
        ),
    )
    parser.add_argument(
        "--ai-model",
        default=(
            GLM52_MODEL
            if site in {"qianji", "sxjm", "bitbid", "trade365", "sxzwfw"}
            else QWEN3_8B_MODEL
        ),
    )
    parser.add_argument(
        "--ai-max-calls",
        type=_nonnegative_int,
        default=None,
        help="模型调用总上限；Qwen 默认 0（不限次数），GLM 默认 100",
    )
    parser.add_argument(
        "--ai-min-interval",
        type=_nonnegative_float,
        default=None,
        help=(
            "同一 API Key 两次模型请求的最小启动间隔；默认使用模型配置。"
            "全量双 Key 队列可按 TPM 预算显式设置"
        ),
    )
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
        scope_values = {
            "site": site,
            "start": self.start_date,
            "end": self.end_date,
            "all": args.all_history,
            "sections": args.sections,
            "channels": args.channels if self.profile.supports_channels else None,
            "project_types": (
                args.project_types if self.profile.supports_project_types else None
            ),
            "page_size": args.page_size,
            "max_records": args.max_records,
            "max_records_per_notice_type": args.max_records_per_notice_type,
            "max_pages": args.max_pages,
            "ai_extract": args.ai_extract,
            "ai_provider": args.ai_provider if args.ai_extract else None,
            "ai_model": args.ai_model if args.ai_extract else None,
            "ai_max_calls": args.ai_max_calls if args.ai_extract else None,
            "sample_mode": args.sample_mode if site == "sxjkzcpt" else None,
            "sample_seed": args.sample_seed if site == "sxjkzcpt" else None,
        }
        self.scope_material = json.dumps(
            scope_values,
            ensure_ascii=False,
            sort_keys=True,
        )
        # 全历史任务的实际请求截止日期需要随启动日期变化，但它不应改变任务
        # 身份和 JOBDIR，否则跨日重启会从 2020 年重新扫描。保留真实日期用于
        # 日志/完成记录，只在 scope 哈希中使用稳定哨兵。
        scope_identity = dict(scope_values)
        if args.all_history and site == "sxzwfw":
            scope_identity["start"] = "ALL_HISTORY"
            scope_identity["end"] = "ALL_HISTORY"
        digest = hashlib.sha256(
            json.dumps(
                scope_identity, ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()[:12]
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
        if site == "sxzwfw" and args.all_history:
            self._adopt_sxzwfw_legacy_history_state()
        self.log_dir = self.site_dir / "logs"
        self.state_dir = self.site_dir / "state" / "runner" / self.scope_key
        self.job_dir = self.site_dir / "state" / "jobs" / "notices" / self.scope_key
        self.lock_path = self.site_dir / "state" / "resumable.lock"
        for path in (self.log_dir, self.state_dir, self.job_dir):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _chunk_number(path: Path) -> int:
        try:
            return int((path / "chunk").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return -1

    def _adopt_sxzwfw_legacy_history_state(self) -> None:
        """把按旧版动态日期生成的最深全历史断点迁移到稳定 scope。"""

        runner_root = self.site_dir / "state" / "runner"
        job_root = self.site_dir / "state" / "jobs" / "notices"
        stable_runner = runner_root / self.scope_key
        stable_job = job_root / self.scope_key
        if stable_runner.exists() or stable_job.exists():
            return
        candidates = [
            path
            for path in runner_root.glob("all_*")
            if path.is_dir() and (path / "chunk").exists()
        ]
        if not candidates:
            return
        source_runner = max(
            candidates,
            key=lambda path: (self._chunk_number(path), path.stat().st_mtime),
        )
        source_job = job_root / source_runner.name
        runner_root.mkdir(parents=True, exist_ok=True)
        job_root.mkdir(parents=True, exist_ok=True)
        source_runner.rename(stable_runner)
        if source_job.exists():
            source_job.rename(stable_job)
        print(
            f"SXZWFW 已迁移旧全历史断点：{source_runner.name} -> "
            f"{self.scope_key}（chunk={self._chunk_number(stable_runner)}）",
            flush=True,
        )

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
        if self.site == "sxxindian":
            values += [
                "-a",
                "max_records_per_notice_type="
                f"{self.args.max_records_per_notice_type}",
            ]
        if self.site == "sxjkzcpt":
            values += [
                "-a", f"sample_mode={self.args.sample_mode}",
                "-a", f"sample_seed={self.args.sample_seed}",
            ]
        if self.profile.supports_project_types:
            values += ["-a", f"project_types={self.args.project_types}"]
        if self.profile.parse_pdf_arg:
            # 签章 PDF 作为附件在第二阶段下载；公告阶段使用 API/HTML 正文。
            values += ["-a", "parse_pdf=false"]
        if self.args.all_history and self.site != "sxzwfw":
            if self.site == "sxjm":
                values += ["-a", "days="]
            elif self.site == "sxxindian":
                # 新点列表接口强制要求日期。2000 年仅作为稳定的全历史下界，
                # 不参与公告字段，也避免 Spider 回退成默认近一年。
                values += [
                    "-a", "start_date=2000-01-01",
                    "-a", f"end_date={date.today().isoformat()}",
                ]
            return values
        return values + ["-a", f"start_date={self.start_date}", "-a", f"end_date={self.end_date}"]

    def _settings(self, request_delay: float) -> list[str]:
        check_updates = "False" if self.args.check_updates else "True"
        # sxbid 对短时间并行请求会偶发返回 nginx 错误；gxebidding 的公开
        # CMS/PDF 接口响应较慢且有明确限流；sxty_ebidding 的浏览器详情路由
        # 存在人机验证。三者都强制单域单并发，避免固定出口触发风控。
        concurrency = (
            1
            if self.site in {"sxbid", "gxebidding", "sxty_ebidding"}
            else self.args.concurrency
        )
        ai_profile, ai_model = resolve_provider(
            self.args.ai_provider, self.args.ai_model
        )
        ai_max_calls = self.args.ai_max_calls
        if ai_max_calls is None:
            ai_max_calls = 0 if ai_profile.name == "siliconflow" else 100
        values = {
            "CRAWLER_OUTBOUND_MODE": self.args.outbound_mode,
            "NOTICE_OUTPUT_ROOT": str(self.output_root),
            "NOTICE_DEDUP_ROOT": str(self.output_root),
            "NOTICE_DEDUP_ENABLED": "True",
            "NOTICE_DEDUP_SKIP_KNOWN_IDENTITIES": check_updates,
            "NOTICE_SNAPSHOT_ENABLED": "True",
            "NOTICE_SNAPSHOT_REQUIRED": "False",
            "NOTICE_EXPORT_CSV_ENABLED": (
                "False" if self.profile.json_only else "True"
            ),
            "NOTICE_AI_ENABLED": "True" if self.args.ai_extract else "False",
            "NOTICE_AI_MAX_CALLS": str(ai_max_calls),
            "NOTICE_VALIDATION_MAX_PER_TYPE": str(
                self.args.max_records_per_notice_type
            ),
            "NOTICE_ATTACHMENT_DOWNLOAD_ENABLED": "False",
            "NOTICE_RESOLVE_ATTACHMENT_URLS": "True",
            "FILES_STORE": str(self.output_root),
            "JOBDIR": str(self.job_dir),
            "HTTPCACHE_ENABLED": "False",
            "CONCURRENT_REQUESTS": str(concurrency),
            "CONCURRENT_REQUESTS_PER_DOMAIN": str(concurrency),
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
            "DIRECT_CONCURRENT_REQUESTS": str(concurrency),
            "DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN": str(concurrency),
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
        # 命令行设置优先级高于 Spider 的 GLM 默认配置，因此选择 Qwen 时会
        # 同时切换提供方、密钥环境变量和输出格式；真实密钥不会进入命令行。
        values.update(ai_profile.scrapy_settings(ai_model))
        if self.args.ai_min_interval is not None:
            values["NOTICE_AI_MIN_INTERVAL"] = str(
                self.args.ai_min_interval
            )
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
                # 交互终端/tmux 的 Ctrl-C 会同时发送给前台进程组中的
                # SiteRunner 和 Scrapy。这里若再补发一次 SIGINT，Scrapy 会把
                # 它视为“第二次强制停止”，可能在持久化 JOBDIR 队列头时被打断，
                # 留下 0 字节 LIFO 计数文件。先等待子进程完成第一次优雅退出；
                # 只有它没有响应时才用 SIGTERM 收尾。
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
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
                effective_concurrency = (
                    1
                    if self.site in {"sxbid", "gxebidding", "sxty_ebidding"}
                    else self.args.concurrency
                )
                stamp = time.strftime("%Y%m%d_%H%M%S")
                log_path = self.log_dir / f"{self.scope_key}_chunk_{chunk:05d}_{stamp}.log"
                print(
                    f"[{time.strftime('%F %T')}] {self.site} 公告第{chunk}批："
                    f"并发={effective_concurrency} 间隔={delay:.2f}s "
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
                if any(
                    marker in content
                    for marker in (
                        "sxty_captcha_detected",
                        "sxty_non_json_response",
                        "sxty_invalid_json_response",
                        "sxty_api_rejected",
                    )
                ):
                    print(
                        "易招标接口出现验证码或异常响应，已保护性停爬并保留断点。",
                        file=sys.stderr,
                    )
                    return 3
                if "'finish_reason': 'closespider_pagecount'" in content:
                    cooldown = random.randint(self.args.cooldown_min, self.args.cooldown_max)
                    if cooldown > 0:
                        print(f"达到响应预算，冷却 {cooldown} 秒；Ctrl-C 可安全停止。", flush=True)
                        try:
                            time.sleep(cooldown)
                        except KeyboardInterrupt:
                            print("已在冷却期停止；下次从断点继续。", flush=True)
                            return 130
                    else:
                        print("达到响应预算，已保存断点；不冷却，立即继续下一批。", flush=True)
                    continue
                normal_finish = any(
                    marker in content
                    for marker in (
                        "'finish_reason': 'finished'",
                        "'finish_reason': 'validation_type_quota_reached'",
                    )
                )
                if not normal_finish:
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
        if self.profile.json_only:
            command.append("--no-sync-csv")
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
        if self.args.ai_extract and self.args.phase in {"all", "notices"}:
            ai_profile, ai_model = resolve_provider(
                self.args.ai_provider, self.args.ai_model
            )
            key_status = (
                "已配置" if os.environ.get(ai_profile.api_key_env, "").strip()
                else "未配置（模型不可用时保留规则结果）"
            )
            print(
                f"AI辅助解析：provider={ai_profile.name} model={ai_model} "
                f"key_env={ai_profile.api_key_env} key={key_status}",
                flush=True,
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
