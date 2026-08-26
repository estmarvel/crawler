#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

# 本机 .env 已由 .gitignore 排除；密钥只在 --ai-extract 时由 Scrapy 使用，
# 不会进入命令行参数或运行日志。QIANJI_AI_ENV_FILE 可覆盖默认文件位置。
ENV_FILE="${QIANJI_AI_ENV_FILE:-${ROOT}/.env}"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  source "${ENV_FILE}"
  set +a
fi
PY="${CRAWLER_PYTHON_COMMAND:-}"
[[ -n "${PY}" ]] || [[ ! -x "${ROOT}/.venv/bin/python" ]] || PY="${ROOT}/.venv/bin/python"
[[ -n "${PY}" ]] || [[ ! -x "/home/vipuser/miniconda3/envs/myenv/bin/python" ]] || PY="/home/vipuser/miniconda3/envs/myenv/bin/python"
exec "${PY:-python3}" -m crawler_scrapy.site_runner qianji "$@"
