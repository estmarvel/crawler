"""Attachment stage placeholder for Shanxi government procurement.

The first crawler pass stores attachment metadata already exposed by
``/portal/detail``.  The sampled public notices observed so far did not expose a
stable separate JSON download API, so the unified runner keeps this module as a
safe no-op until attachment download rules are verified.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="sxzfcg attachment downloader")
    parser.add_argument("--output-root")
    parser.add_argument("--outbound-mode")
    parser.add_argument("--connect-timeout")
    parser.add_argument("--read-timeout")
    parser.add_argument("--retries")
    parser.add_argument("--min-delay")
    parser.add_argument("--max-delay")
    parser.add_argument("--max-attachments")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print("sxzfcg attachments: no verified public attachment download API yet; skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
