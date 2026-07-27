#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
runner="${project_dir}/run_sxjm_daily.sh"
cron_hour="${1:-1}"
python_bin="${PYTHON_BIN:-python3}"
output_root="${SXJM_OUTPUT_ROOT:-${project_dir}/output}"

if ! [[ "${cron_hour}" =~ ^([0-9]|1[0-9]|2[0-3])$ ]]; then
  echo "小时必须是 0-23，当前值：${cron_hour}" >&2
  exit 2
fi

chmod +x "${runner}"
cron_line="0 ${cron_hour} * * * PYTHON_BIN='${python_bin}' SXJM_OUTPUT_ROOT='${output_root}' '${runner}'"
marker="# sxjm-daily-crawler"
existing="$(crontab -l 2>/dev/null || true)"
filtered="$(printf '%s\n' "${existing}" | grep -vF "${marker}" | grep -vF "${runner}" || true)"
{
  printf '%s\n' "${filtered}"
  printf '%s %s\n' "${cron_line}" "${marker}"
} | sed '/^[[:space:]]*$/d' | crontab -

echo "Linux cron 已安装：每天 ${cron_hour}:00 执行。"
echo "运行脚本：${runner}"
echo "运行日志：${project_dir}/logs/sxjm_daily.log"
echo "输出目录：${output_root}"
