#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${PYTHON_BIN:-python3}"
output_root="${SXJM_OUTPUT_ROOT:-${project_dir}/output}"
log_dir="${SXJM_LOG_DIR:-${project_dir}/logs}"
lock_file="${SXJM_LOCK_FILE:-/tmp/sxjm_daily.lock}"

mkdir -p "${log_dir}"

exec flock -n "${lock_file}" \
  "${python_bin}" "${project_dir}/run_sxjm_daily.py" \
  --output-root "${output_root}" \
  >> "${log_dir}/sxjm_daily.log" 2>&1
