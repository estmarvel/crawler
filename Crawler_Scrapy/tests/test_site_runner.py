from crawler_scrapy.site_runner import PROFILES, SiteRunner, build_parser


def test_all_supported_sites_default_to_guarded_direct_access():
    assert set(PROFILES) == {
        "sxjm", "sxzwfw", "bitbid", "huaxin", "jiubang", "qianji",
        "sxjkzcpt", "trade365",
        "sxbid",
    }
    for site, profile in PROFILES.items():
        args = build_parser(site).parse_args([])
        assert profile.default_outbound == "direct"
        assert args.outbound_mode == "direct"
        assert args.concurrency == 2
        assert (args.delay_min, args.delay_max) == (3.0, 5.0)
        assert args.responses_per_chunk == 400
        assert (args.cooldown_min, args.cooldown_max) == (180, 300)


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
