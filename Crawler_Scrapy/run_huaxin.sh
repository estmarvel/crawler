#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"
PY="${CRAWLER_PYTHON_COMMAND:-}"
[[ -n "${PY}" ]] || [[ ! -x "${ROOT}/.venv/bin/python" ]] || PY="${ROOT}/.venv/bin/python"
[[ -n "${PY}" ]] || [[ ! -x "/home/vipuser/miniconda3/envs/myenv/bin/python" ]] || PY="/home/vipuser/miniconda3/envs/myenv/bin/python"
exec "${PY:-python3}" -m crawler_scrapy.site_runner huaxin "$@"
