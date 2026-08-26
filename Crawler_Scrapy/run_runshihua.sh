#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"
PY=""
for candidate in \
  "${CRAWLER_PYTHON_COMMAND:-}" \
  "${ROOT}/.venv/bin/python" \
  "/home/vipuser/miniconda3/envs/myenv/bin/python"
do
  if [[ -n "${candidate}" ]] && [[ -x "${candidate}" ]] \
    && "${candidate}" -c 'import scrapy' >/dev/null 2>&1
  then
    PY="${candidate}"
    break
  fi
done
if [[ -z "${PY}" ]]; then
  echo "找不到安装了 Scrapy 的 Python；请设置 CRAWLER_PYTHON_COMMAND" >&2
  exit 2
fi
exec "${PY}" -m crawler_scrapy.site_runner runshihua "$@"
