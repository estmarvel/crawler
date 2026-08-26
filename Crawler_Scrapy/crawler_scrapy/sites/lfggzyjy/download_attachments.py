"""临汾公共资源公告附件阶段占位。

当前接入范围先验证公开公告列表和详情正文；附件下载后续单独核验。
"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default="new_output")
    parser.add_argument("--outbound-mode", default="direct")
    parser.add_argument("--connect-timeout", default="30")
    parser.add_argument("--read-timeout", default="900")
    parser.add_argument("--retries", default="4")
    parser.add_argument("--min-delay", default="2.0")
    parser.add_argument("--max-delay", default="5.0")
    parser.add_argument("--max-attachments", default="0")
    parser.parse_args(argv)
    print("lfggzyjy 当前版本未启用附件下载阶段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
