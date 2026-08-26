#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"
PY="${CRAWLER_PYTHON_COMMAND:-}"
[[ -n "${PY}" ]] || [[ ! -x "${ROOT}/.venv/bin/python" ]] || PY="${ROOT}/.venv/bin/python"
[[ -n "${PY}" ]] || [[ ! -x "/home/vipuser/miniconda3/envs/myenv/bin/python" ]] || PY="/home/vipuser/miniconda3/envs/myenv/bin/python"
PY="${PY:-python3}"
exec "${PY}" -m scrapy crawl llggzy \
  -a start_date="${START_DATE:-2020-01-01}" \
  -a end_date="${END_DATE:-$(date +%F)}" \
  -a max_records="${MAX_RECORDS:-100000}" \
  "$@"

