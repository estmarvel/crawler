#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"
PY="${CRAWLER_PYTHON_COMMAND:-}"
[[ -n "${PY}" ]] || [[ ! -x "${ROOT}/.venv/bin/python" ]] || PY="${ROOT}/.venv/bin/python"
[[ -n "${PY}" ]] || [[ ! -x "/home/vipuser/miniconda3/envs/myenv/bin/python" ]] || PY="/home/vipuser/miniconda3/envs/myenv/bin/python"
PY="${PY:-python3}"

ARGS=(crawl ccgp_shanxi)
[[ -n "${CATEGORIES:-}" ]] && ARGS+=(-a "categories=${CATEGORIES}")
[[ -n "${MAX_RECORDS:-}" ]] && ARGS+=(-a "max_records=${MAX_RECORDS}")
[[ -n "${PAGE_SIZE:-}" ]] && ARGS+=(-a "page_size=${PAGE_SIZE}")
[[ -n "${MAX_PAGES:-}" ]] && ARGS+=(-a "max_pages=${MAX_PAGES}")
[[ -n "${DAYS:-}" ]] && ARGS+=(-a "days=${DAYS}")
[[ -n "${START_DATE:-}" ]] && ARGS+=(-a "start_date=${START_DATE}")
[[ -n "${END_DATE:-}" ]] && ARGS+=(-a "end_date=${END_DATE}")

ARGS+=(-s "NOTICE_AI_ENABLED=${NOTICE_AI_ENABLED:-False}")
ARGS+=(-s "NOTICE_AI_MAX_CALLS=${NOTICE_AI_MAX_CALLS:-100}")
ARGS+=(-s "NOTICE_AI_BASE_URL=${NOTICE_AI_BASE_URL:-https://api.siliconflow.cn/v1}")
ARGS+=(-s "NOTICE_AI_MODEL=${NOTICE_AI_MODEL:-Qwen/Qwen3-8B}")
ARGS+=(-s "NOTICE_AI_API_KEY_ENV=${NOTICE_AI_API_KEY_ENV:-SILICONFLOW_API_KEY}")
ARGS+=(-s "NOTICE_AI_ENABLE_THINKING=${NOTICE_AI_ENABLE_THINKING:-False}")

exec "${PY}" -m scrapy "${ARGS[@]}" "$@"
