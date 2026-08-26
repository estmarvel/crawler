#!/usr/bin/env bash
set -Eeuo pipefail

# 招采进宝电子招标投标交易平台（山西）选择性 AI 验证任务。
# 13 个源栏目各最多调度 100 条；栏目总量不足 100 条时采完已有数据。
# 输出与生产 output 完全隔离，重复执行依靠 JOBDIR 和去重状态断点续跑。

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_ROOT="${SXTY_VALIDATION_OUTPUT_ROOT:-${ROOT}/validation_output/sxty_ebidding_ai_validation_100}"
MODEL="${SXTY_VALIDATION_AI_MODEL:-Qwen/Qwen3-8B}"
AI_INTERVAL="${SXTY_VALIDATION_AI_INTERVAL:-6}"
ENV_FILE="${CRAWLER_AI_ENV_FILE:-${ROOT}/.env}"
KEY_ENV="${SXTY_VALIDATION_API_KEY_ENV:-SILICONFLOW_API_KEY}"

cd "${ROOT}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "未找到 AI 环境文件：${ENV_FILE}" >&2
  exit 2
fi
case "${KEY_ENV}" in
  SILICONFLOW_API_KEY|SILICONFLOW_API_KEY2|SILICONFLOW_API_KEY3) ;;
  *)
    echo "SXTY_VALIDATION_API_KEY_ENV 只允许三个 SILICONFLOW_API_KEY 变量" >&2
    exit 2
    ;;
esac
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a
SELECTED_KEY="${!KEY_ENV:-}"
if [[ -z "${SELECTED_KEY}" ]]; then
  echo "${KEY_ENV} 未配置" >&2
  exit 2
fi
# 底层统一读取 SILICONFLOW_API_KEY；只在内存中映射，不把密钥写入命令或日志。
export SILICONFLOW_API_KEY="${SELECTED_KEY}"
unset SILICONFLOW_API_KEY2 SILICONFLOW_API_KEY3 SELECTED_KEY

exec "${ROOT}/run_sxty_ebidding.sh" \
  --phase notices \
  --output-root "${OUTPUT_ROOT}" \
  --all \
  --page-size 50 \
  --max-records 100 \
  --max-pages 100 \
  --concurrency 1 \
  --delay-min 4 \
  --delay-max 6 \
  --responses-per-chunk 400 \
  --cooldown-min 180 \
  --cooldown-max 300 \
  --request-timeout 300 \
  --ai-extract \
  --ai-provider siliconflow \
  --ai-model "${MODEL}" \
  --ai-max-calls 0 \
  --ai-min-interval "${AI_INTERVAL}"
