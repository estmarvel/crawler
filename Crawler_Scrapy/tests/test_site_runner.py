import pytest

from crawler_scrapy.site_runner import PROFILES, SiteRunner, build_parser


def test_all_supported_sites_default_to_guarded_direct_access():
    assert set(PROFILES) == {
        "sxjm", "sxzwfw", "bitbid", "huaxin", "jiubang", "qianji",
        "sxjkzcpt", "trade365",
        "sxbid", "sxxindian", "runshihua", "gxebidding", "lfggzyjy", "sxzfcg",
        "sxty_ebidding",
    }
    for site, profile in PROFILES.items():
        args = build_parser(site).parse_args([])
        assert profile.default_outbound == "direct"
        assert args.outbound_mode == "direct"
        assert args.output_root.name == "output"
        assert args.concurrency == 2
        assert (args.delay_min, args.delay_max) == (3.0, 5.0)
        assert args.responses_per_chunk == 400
        assert (args.cooldown_min, args.cooldown_max) == (180, 300)


def test_ctrl_c_waits_for_scrapy_graceful_shutdown_without_second_sigint(
    tmp_path, monkeypatch
):
    class InterruptingStdout:
        def __iter__(self):
            return self

        def __next__(self):
            raise KeyboardInterrupt

    class FakeProcess:
        stdout = InterruptingStdout()
        wait_timeouts = []
        terminated = False

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            return 0

        def terminate(self):
            self.terminated = True

    process = FakeProcess()
    monkeypatch.setattr(
        "crawler_scrapy.site_runner.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    runner = SiteRunner(
        "bitbid",
        build_parser("bitbid").parse_args(["--output-root", str(tmp_path)]),
    )

    with pytest.raises(KeyboardInterrupt):
        runner._run_logged(["scrapy"], tmp_path / "interrupt.log")

    assert process.wait_timeouts == [30]
    assert process.terminated is False


def test_qianji_runner_passes_categories_project_types_and_disables_inline_pdf(tmp_path):
    args = build_parser("qianji").parse_args([
        "--output-root", str(tmp_path),
        "--sections", "tender,candidate",
        "--project-types", "engineering,service",
    ])
    runner = SiteRunner("qianji", args)
    spider_args = runner._spider_args()
    assert "categories=tender,candidate" in spider_args
    assert "project_types=engineering,service" in spider_args
    assert "parse_pdf=false" in spider_args


def test_qianji_runner_can_enable_glm_5_2_without_putting_key_in_command(tmp_path):
    args = build_parser("qianji").parse_args([
        "--output-root", str(tmp_path),
        "--ai-extract",
        "--ai-max-calls", "30",
    ])
    settings = SiteRunner("qianji", args)._settings(3.5)
    assert "NOTICE_AI_ENABLED=True" in settings
    assert "NOTICE_AI_MODEL=glm-5.2" in settings
    assert "NOTICE_AI_PROVIDER=zhipu" in settings
    assert "NOTICE_AI_API_KEY_ENV=ZHIPUAI_API_KEY" in settings
    assert "NOTICE_AI_BASE_URL=https://open.bigmodel.cn/api/paas/v4" in settings
    assert "NOTICE_AI_RESPONSE_FORMAT=json_object" in settings
    assert "NOTICE_AI_MAX_CALLS=30" in settings
    assert not any("sk-" in value for value in settings)


def test_full_runner_can_select_siliconflow_qwen3_without_changing_glm(tmp_path):
    args = build_parser("sxjm").parse_args([
        "--output-root", str(tmp_path),
        "--all",
        "--ai-extract",
        "--ai-provider", "siliconflow",
        "--ai-model", "Qwen/Qwen3-8B",
        "--ai-max-calls", "0",
    ])

    settings = SiteRunner("sxjm", args)._settings(3.5)

    assert "NOTICE_AI_ENABLED=True" in settings
    assert "NOTICE_AI_PROVIDER=siliconflow" in settings
    assert "NOTICE_AI_API_KEY_ENV=SILICONFLOW_API_KEY" in settings
    assert "NOTICE_AI_BASE_URL=https://api.siliconflow.cn/v1" in settings
    assert "NOTICE_AI_MODEL=Qwen/Qwen3-8B" in settings
    assert "NOTICE_AI_RESPONSE_FORMAT=json_schema" in settings
    assert "NOTICE_AI_ENABLE_THINKING=False" in settings
    assert "NOTICE_AI_RETRY_TIMES=1" in settings
    assert "NOTICE_AI_TIMEOUT=180.0" in settings
    assert "NOTICE_AI_MAX_OUTPUT_TOKENS=2200" in settings
    assert "NOTICE_AI_MAX_CALLS=0" in settings
    assert "NOTICE_EXPORT_CSV_ENABLED=False" in settings
    assert not any("sk-" in value for value in settings)


def test_json_only_sites_disable_csv_output(tmp_path):
    json_only_sites = {
        "qianji", "sxjm", "bitbid", "sxzwfw", "trade365",
        "huaxin", "jiubang", "sxbid", "sxxindian", "sxty_ebidding",
    }
    for site in PROFILES:
        args = build_parser(site).parse_args(["--output-root", str(tmp_path)])
        settings = SiteRunner(site, args)._settings(3.5)
        expected = (
            "NOTICE_EXPORT_CSV_ENABLED=False"
            if site in json_only_sites
            else "NOTICE_EXPORT_CSV_ENABLED=True"
        )
        assert expected in settings


def test_json_only_site_attachment_phase_does_not_sync_csv(tmp_path, monkeypatch):
    args = build_parser("sxjm").parse_args([
        "--output-root", str(tmp_path),
        "--phase", "attachments",
    ])
    runner = SiteRunner("sxjm", args)
    captured = []

    def fake_run(command, _log_path):
        captured.extend(command)
        return 0

    monkeypatch.setattr(runner, "_run_logged", fake_run)
    assert runner.run_attachments() == 0
    assert "--no-sync-csv" in captured


def test_qwen_model_name_selects_siliconflow_in_auto_mode(tmp_path):
    args = build_parser("bitbid").parse_args([
        "--output-root", str(tmp_path),
        "--ai-extract",
        "--ai-model", "Qwen/Qwen3-8B",
    ])
    settings = SiteRunner("bitbid", args)._settings(3.5)
    assert "NOTICE_AI_PROVIDER=siliconflow" in settings
    assert "NOTICE_AI_API_KEY_ENV=SILICONFLOW_API_KEY" in settings
    assert "NOTICE_AI_MIN_INTERVAL=6.0" in settings
    assert "NOTICE_AI_MAX_CALLS=0" in settings


def test_full_queue_can_disable_chunk_cooldown_and_override_ai_interval(tmp_path):
    args = build_parser("qianji").parse_args([
        "--output-root", str(tmp_path),
        "--all",
        "--cooldown-min", "0",
        "--cooldown-max", "0",
        "--ai-extract",
        "--ai-provider", "siliconflow",
        "--ai-model", "Qwen/Qwen3-8B",
        "--ai-min-interval", "1.9",
    ])

    assert args.cooldown_min == 0
    assert args.cooldown_max == 0
    settings = SiteRunner("qianji", args)._settings(3.5)
    assert "NOTICE_AI_MIN_INTERVAL=1.9" in settings


def test_sxxindian_runner_uses_all_feeds_and_separate_attachments(tmp_path):
    args = build_parser("sxxindian").parse_args([
        "--output-root", str(tmp_path),
        "--sections", "bidding.tender.engineering,purchase.notice.all",
    ])
    runner = SiteRunner("sxxindian", args)
    assert "feeds=bidding.tender.engineering,purchase.notice.all" in runner._spider_args()
    assert PROFILES["sxxindian"].attachment_module.endswith(
        "sxxindian.download_attachments"
    )


def test_sxxindian_runner_passes_global_notice_type_quota(tmp_path):
    args = build_parser("sxxindian").parse_args([
        "--output-root", str(tmp_path),
        "--max-records", "1000",
        "--max-records-per-notice-type", "200",
    ])
    spider_args = SiteRunner("sxxindian", args)._spider_args()
    assert "max_records=1000" in spider_args
    assert "max_records_per_notice_type=200" in spider_args
    settings = SiteRunner("sxxindian", args)._settings(3.5)
    assert "NOTICE_VALIDATION_MAX_PER_TYPE=200" in settings


def test_validation_notice_type_quota_is_available_to_every_site(tmp_path):
    args = build_parser("sxjm").parse_args([
        "--output-root", str(tmp_path),
        "--max-records-per-notice-type", "50",
    ])
    settings = SiteRunner("sxjm", args)._settings(3.5)
    assert "NOTICE_VALIDATION_MAX_PER_TYPE=50" in settings


def test_sxxindian_all_history_supplies_required_date_range(tmp_path):
    args = build_parser("sxxindian").parse_args([
        "--output-root", str(tmp_path), "--all"
    ])
    spider_args = SiteRunner("sxxindian", args)._spider_args()
    assert "start_date=2000-01-01" in spider_args
    assert any(value.startswith("end_date=") for value in spider_args)


def test_sxjkzcpt_runner_uses_two_public_channels(tmp_path):
    args = build_parser("sxjkzcpt").parse_args([
        "--output-root", str(tmp_path),
        "--sections", "tender,candidate",
        "--sample-mode", "random",
        "--sample-seed", "42",
    ])
    runner = SiteRunner("sxjkzcpt", args)
    spider_args = runner._spider_args()
    assert "categories=tender,candidate" in spider_args
    assert "channels=zbcg,qzbcg" in spider_args
    assert "sample_mode=random" in spider_args
    assert "sample_seed=42" in spider_args


def test_trade365_runner_passes_categories_and_project_types(tmp_path):
    args = build_parser("trade365").parse_args([
        "--output-root", str(tmp_path),
        "--sections", "tender,candidate,award",
        "--project-types", "engineering,service",
    ])
    runner = SiteRunner("trade365", args)
    spider_args = runner._spider_args()
    assert "categories=tender,candidate,award" in spider_args
    assert "project_types=engineering,service" in spider_args


def test_runshihua_runner_separates_notice_pdf_download(tmp_path):
    args = build_parser("runshihua").parse_args([
        "--output-root", str(tmp_path),
        "--sections", "tender,candidate,award",
    ])
    runner = SiteRunner("runshihua", args)
    spider_args = runner._spider_args()
    assert "categories=tender,candidate,award" in spider_args
    assert "parse_pdf=false" in spider_args
    assert PROFILES["runshihua"].attachment_module.endswith(
        "runshihua.download_attachments"
    )


def test_gxebidding_runner_passes_three_channels_and_separates_pdf(tmp_path):
    args = build_parser("gxebidding").parse_args([
        "--output-root", str(tmp_path),
        "--sections", "tender,candidate,award",
        "--channels", "lawful,purchase",
    ])
    runner = SiteRunner("gxebidding", args)
    spider_args = runner._spider_args()
    assert "categories=tender,candidate,award" in spider_args
    assert "channels=lawful,purchase" in spider_args
    assert "parse_pdf=false" in spider_args
    settings = runner._settings(3.5)
    assert "CONCURRENT_REQUESTS=1" in settings
    assert PROFILES["gxebidding"].attachment_module.endswith(
        "gxebidding.download_attachments"
    )


def test_sxty_ebidding_runner_uses_feeds_json_and_single_concurrency(tmp_path):
    args = build_parser("sxty_ebidding").parse_args([
        "--output-root", str(tmp_path),
        "--sections", "engineering.plan,enterprise.award",
    ])
    runner = SiteRunner("sxty_ebidding", args)
    assert "feeds=engineering.plan,enterprise.award" in runner._spider_args()
    settings = runner._settings(3.5)
    assert "CONCURRENT_REQUESTS=1" in settings
    assert "NOTICE_EXPORT_CSV_ENABLED=False" in settings
    assert PROFILES["sxty_ebidding"].attachment_module.endswith(
        "sxty_ebidding.download_attachments"
    )


def test_sxzwfw_all_history_adopts_deepest_legacy_checkpoint(tmp_path):
    runner_root = tmp_path / "sxzwfw" / "state" / "runner"
    job_root = tmp_path / "sxzwfw" / "state" / "jobs" / "notices"
    for name, chunk in (("all_old", 12), ("all_new", 1)):
        (runner_root / name).mkdir(parents=True)
        (runner_root / name / "chunk").write_text(str(chunk), encoding="utf-8")
        (job_root / name).mkdir(parents=True)
        (job_root / name / "marker").write_text(name, encoding="utf-8")

    args = build_parser("sxzwfw").parse_args(
        ["--all", "--output-root", str(tmp_path)]
    )
    runner = SiteRunner("sxzwfw", args)

    assert runner.scope_key not in {"all_old", "all_new"}
    assert (runner.state_dir / "chunk").read_text(encoding="utf-8") == "12"
    assert (runner.job_dir / "marker").read_text(encoding="utf-8") == "all_old"
    assert not (runner_root / "all_old").exists()
    assert (runner_root / "all_new").exists()
