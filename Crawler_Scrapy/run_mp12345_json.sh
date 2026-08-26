#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-/home/vipuser/miniconda3/envs/myenv/bin/python}"
exec "$PYTHON_BIN" -m crawler_scrapy.sites.mp12345.export_all_json "$@"
