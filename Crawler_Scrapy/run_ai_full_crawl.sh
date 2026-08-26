#!/usr/bin/env bash
set -Eeuo pipefail

# 9 个已接入混合 AI 解析的网站统一全量任务入口。
# 三个 Key 各自使用独立队列；同一 Key 内顺序执行，避免共享限流额度。
# 默认只展示计划；只有显式执行 `start` 才会创建后台任务。

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_ROOT="${FULL_AI_OUTPUT_ROOT:-${ROOT}/output}"
CONTROL_DIR="${OUTPUT_ROOT}/_ai_full_control"
ENV_FILE="${CRAWLER_AI_ENV_FILE:-${ROOT}/.env}"
MODEL="${FULL_AI_MODEL:-Qwen/Qwen3-8B}"
AI_INTERVAL="${FULL_AI_MIN_INTERVAL:-1.8}"
MIN_FREE_GB="${FULL_AI_MIN_FREE_GB:-15}"
MAX_SITE_ATTEMPTS="${FULL_AI_MAX_SITE_ATTEMPTS:-3}"
ERROR_RETRY_SECONDS="${FULL_AI_ERROR_RETRY_SECONDS:-60}"
SESSION_PREFIX="${FULL_AI_SESSION_PREFIX:-crawler_ai_full}"

KEY1_SITES=(huaxin jiubang trade365 sxbid)
KEY2_SITES=(bitbid qianji sxxindian)
KEY3_SITES=(sxjm sxzwfw)
ALL_SITES=(huaxin jiubang trade365 sxbid bitbid qianji sxxindian sxjm sxzwfw)

usage() {
  cat <<'EOF'
用法：
  ./run_ai_full_crawl.sh plan     # 只检查配置并展示任务，不运行（默认）
  ./run_ai_full_crawl.sh start    # 启动尚未运行的三条 Key 队列
  ./run_ai_full_crawl.sh start-key3 # 只启动 SXJM、SXZWFW 的 Key3 队列
  ./run_ai_full_crawl.sh status   # 查看三条队列、阶段和输出占用
  ./run_ai_full_crawl.sh attach [key1|key2|key3] # 查看指定队列实时日志
  ./run_ai_full_crawl.sh stop     # 向三条任务发送 Ctrl-C，保留断点

可选环境变量：
  FULL_AI_OUTPUT_ROOT       输出根目录，默认 /home/intsig/Crawler_Scrapy/output
  FULL_AI_MIN_INTERVAL      每个 Key 的 AI 请求最小间隔，默认 1.8 秒
  FULL_AI_MIN_FREE_GB       每个网站开始前要求的最小可用空间，默认 15GB
  FULL_AI_MAX_SITE_ATTEMPTS 单站异常重试次数，默认 3
EOF
}

load_env() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "未找到环境文件：${ENV_FILE}" >&2
    return 2
  fi
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
}

python_command() {
  local candidate
  for candidate in \
    "${CRAWLER_PYTHON_COMMAND:-}" \
    "${ROOT}/.venv/bin/python" \
    "/home/vipuser/miniconda3/envs/myenv/bin/python"
  do
    if [[ -n "${candidate}" ]] && [[ -x "${candidate}" ]] \
      && "${candidate}" -c 'import scrapy' >/dev/null 2>&1
    then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  echo "找不到安装了 Scrapy 的 Python；请设置 CRAWLER_PYTHON_COMMAND" >&2
  return 2
}

free_gb() {
  df -Pk "${ROOT}" | awk 'NR==2 {printf "%d", $4 / 1024 / 1024}'
}

disk_guard() {
  local available
  available="$(free_gb)"
  if ((available < MIN_FREE_GB)); then
    echo "可用空间 ${available}GB，低于安全阈值 ${MIN_FREE_GB}GB，停止队列并保留断点。" >&2
    return 1
  fi
}

key_state() {
  local name="$1" value="${2:-}"
  if [[ -n "${value}" ]]; then
    printf '%s=已配置' "${name}"
  else
    printf '%s=未配置' "${name}"
  fi
}

show_plan() {
  local available key1="" key2="" key3=""
  available="$(free_gb)"
  if [[ -f "${ENV_FILE}" ]]; then
    load_env
    key1="${SILICONFLOW_API_KEY:-}"
    key2="${SILICONFLOW_API_KEY2:-}"
    key3="${SILICONFLOW_API_KEY3:-}"
  fi
  cat <<EOF
任务状态：仅配置，尚未启动
输出目录：${OUTPUT_ROOT}/<网站>/{json,snapshots,payloads,attachments,state,logs}（不生成CSV）
模型：${MODEL}（关闭 thinking，严格 JSON Schema）
三 Key：$(key_state SILICONFLOW_API_KEY "${key1}")，$(key_state SILICONFLOW_API_KEY2 "${key2}")，$(key_state SILICONFLOW_API_KEY3 "${key3}")
AI 节奏：每 Key 最短 ${AI_INTERVAL}s/次；按历史约 1,200 token/次估算约 40,000 TPM（额度的 80%）
网站节奏：每域并发 2（sxbid 强制 1），请求间隔 3~5 秒，AutoThrottle 开启
批次策略：每 400 响应保存断点并立即续跑；不做周期冷却；403/429/异常仍会退避并停止当前站点
Key1 队列：${KEY1_SITES[*]}
Key2 队列：${KEY2_SITES[*]}
Key3 队列：${KEY3_SITES[*]}
阶段顺序：三条队列并行；各队列先完成所有公告，再逐站下载附件
当前可用空间：${available}GB；单站启动安全阈值：${MIN_FREE_GB}GB
EOF
  if ((available < MIN_FREE_GB)); then
    echo "警告：空间低于启动阈值，start 会拒绝运行。" >&2
  fi
}

select_key() {
  local queue="$1"
  load_env
  case "${queue}" in
    key1)
      [[ -n "${SILICONFLOW_API_KEY:-}" ]] || {
        echo "SILICONFLOW_API_KEY 未配置" >&2
        return 2
      }
      ;;
    key2)
      [[ -n "${SILICONFLOW_API_KEY2:-}" ]] || {
        echo "SILICONFLOW_API_KEY2 未配置" >&2
        return 2
      }
      export SILICONFLOW_API_KEY="${SILICONFLOW_API_KEY2}"
      ;;
    key3)
      [[ -n "${SILICONFLOW_API_KEY3:-}" ]] || {
        echo "SILICONFLOW_API_KEY3 未配置" >&2
        return 2
      }
      export SILICONFLOW_API_KEY="${SILICONFLOW_API_KEY3}"
      ;;
    *)
      echo "未知队列：${queue}" >&2
      return 2
      ;;
  esac
  # 子进程只读取当前队列选中的 SILICONFLOW_API_KEY；禁止其重新加载 .env，
  # 避免选中的 Key 被默认 Key 覆盖。密钥不会进入命令行、日志或输出文件。
  unset SILICONFLOW_API_KEY2 SILICONFLOW_API_KEY3
  export CRAWLER_AI_ENV_FILE=/dev/null
  export QIANJI_AI_ENV_FILE=/dev/null
}

write_status() {
  local queue="$1" state="$2" site="${3:--}" phase="${4:--}" message="${5:-}"
  mkdir -p "${CONTROL_DIR}"
  printf 'updated_at=%s\nqueue=%s\nstate=%s\nsite=%s\nphase=%s\nmessage=%s\n' \
    "$(date '+%F %T')" "${queue}" "${state}" "${site}" "${phase}" "${message}" \
    >"${CONTROL_DIR}/${queue}.status"
}

run_site_phase() {
  local py="$1" queue="$2" site="$3" phase="$4"
  local attempt status=1
  local -a args

  if [[ "${phase}" == "notices" ]]; then
    args=(
      --phase notices
      --output-root "${OUTPUT_ROOT}"
      --all
      --page-size 100
      --max-records 1000000
      --max-pages 10000
      --concurrency 2
      --delay-min 3
      --delay-max 5
      --responses-per-chunk 400
      --cooldown-min 0
      --cooldown-max 0
      # 公告与附件已拆为两个阶段：网页/API 超过 90 秒通常是源站悬挂，
      # 不应继续占住并发槽；附件阶段仍单独保留 900 秒读取超时。
      --request-timeout 90
      --ai-extract
      --ai-provider siliconflow
      --ai-model "${MODEL}"
      --ai-max-calls 0
      --ai-min-interval "${AI_INTERVAL}"
    )
    # 千极链接口单页 20 条最稳定；不影响最终按公告类型合并保存。
    [[ "${site}" != "qianji" ]] || args+=(--page-size 20)
  else
    args=(
      --phase attachments
      --output-root "${OUTPUT_ROOT}"
      --all
      --outbound-mode direct
      --attachment-connect-timeout 30
      --attachment-read-timeout 900
      --attachment-retries 4
      --attachment-min-delay 2
      --attachment-max-delay 5
      --max-attachments 0
    )
  fi

  for ((attempt = 1; attempt <= MAX_SITE_ATTEMPTS; attempt++)); do
    disk_guard || return 75
    write_status "${queue}" running "${site}" "${phase}" "attempt=${attempt}/${MAX_SITE_ATTEMPTS}"
    echo "[$(date '+%F %T')] ${queue} ${site} ${phase} 第 ${attempt}/${MAX_SITE_ATTEMPTS} 次"
    if "${py}" -m crawler_scrapy.site_runner "${site}" "${args[@]}"; then
      write_status "${queue}" running "${site}" "${phase}" completed
      return 0
    else
      status=$?
    fi
    echo "[$(date '+%F %T')] ${queue} ${site} ${phase} 异常 status=${status}；断点已保留" >&2
    if ((attempt < MAX_SITE_ATTEMPTS)); then
      # 这里只处理网络/接口异常，不是固定批次冷却。
      sleep "${ERROR_RETRY_SECONDS}"
    fi
  done
  return "${status}"
}

worker() {
  local queue="$1" py site
  local -a sites failed=() completed_notices=()
  select_key "${queue}"
  py="$(python_command)"
  mkdir -p "${CONTROL_DIR}"
  exec > >(tee -a "${CONTROL_DIR}/${queue}.log") 2>&1
  case "${queue}" in
    key1) sites=("${KEY1_SITES[@]}") ;;
    key2) sites=("${KEY2_SITES[@]}") ;;
    key3) sites=("${KEY3_SITES[@]}") ;;
    *) echo "未知队列：${queue}" >&2; return 2 ;;
  esac

  write_status "${queue}" running - notices started
  for site in "${sites[@]}"; do
    if run_site_phase "${py}" "${queue}" "${site}" notices; then
      completed_notices+=("${site}")
    else
      failed+=("${site}:notices")
    fi
  done

  write_status "${queue}" running - attachments started
  for site in "${completed_notices[@]}"; do
    run_site_phase "${py}" "${queue}" "${site}" attachments \
      || failed+=("${site}:attachments")
  done

  if ((${#failed[@]})); then
    write_status "${queue}" incomplete - - "failed=${failed[*]}"
    echo "${queue} 队列未完全结束：${failed[*]}" >&2
    return 1
  fi
  write_status "${queue}" completed - - all_done
  echo "${queue} 队列公告与附件全部完成。"
}

start_tasks() {
  local session queue started=0
  command -v tmux >/dev/null 2>&1 || {
    echo "未安装 tmux，无法创建可脱离 SSH 的后台任务" >&2
    return 2
  }
  load_env
  [[ -n "${SILICONFLOW_API_KEY:-}" ]] || {
    echo "SILICONFLOW_API_KEY 未配置" >&2
    return 2
  }
  [[ -n "${SILICONFLOW_API_KEY2:-}" ]] || {
    echo "SILICONFLOW_API_KEY2 未配置" >&2
    return 2
  }
  [[ -n "${SILICONFLOW_API_KEY3:-}" ]] || {
    echo "SILICONFLOW_API_KEY3 未配置" >&2
    return 2
  }
  disk_guard
  mkdir -p "${CONTROL_DIR}"
  for queue in key1 key2 key3; do
    session="${SESSION_PREFIX}_${queue}"
    if tmux has-session -t "${session}" 2>/dev/null; then
      echo "已有任务会话：${session}；跳过，不重复启动。"
      continue
    fi
    tmux new-session -d -s "${session}" \
      "${ROOT}/run_ai_full_crawl.sh worker ${queue}"
    echo "已启动 ${queue} 后台队列：${session}"
    started=1
  done
  ((started)) || echo "三条后台队列均已存在，未重复启动。"
  echo "查看：${ROOT}/run_ai_full_crawl.sh status"
}

start_key3() {
  local session="${SESSION_PREFIX}_key3"
  command -v tmux >/dev/null 2>&1 || {
    echo "未安装 tmux，无法创建可脱离 SSH 的后台任务" >&2
    return 2
  }
  load_env
  [[ -n "${SILICONFLOW_API_KEY3:-}" ]] || {
    echo "SILICONFLOW_API_KEY3 未配置" >&2
    return 2
  }
  disk_guard
  mkdir -p "${CONTROL_DIR}"
  if tmux has-session -t "${session}" 2>/dev/null; then
    echo "已有任务会话：${session}；未重复启动。"
    return 0
  fi
  tmux new-session -d -s "${session}" \
    "${ROOT}/run_ai_full_crawl.sh worker key3"
  echo "已启动 Key3 队列（${KEY3_SITES[*]}）。"
  echo "查看：${ROOT}/run_ai_full_crawl.sh status"
  echo "实时日志：tmux attach -t ${session}"
}

show_status() {
  local queue session state_file site json_count snapshot_count attachment_count
  for queue in key1 key2 key3; do
    session="${SESSION_PREFIX}_${queue}"
    if tmux has-session -t "${session}" 2>/dev/null; then
      echo "${queue}: RUNNING (tmux=${session})"
    else
      echo "${queue}: NOT_RUNNING"
    fi
    state_file="${CONTROL_DIR}/${queue}.status"
    if [[ -f "${state_file}" ]]; then
      sed 's/^/  /' "${state_file}"
    fi
  done
  echo "站点落盘进度（JSON记录/快照/附件文件）："
  for site in "${ALL_SITES[@]}"; do
    json_count="$({
      rg -g '*.json' -c '^[[:space:]]*"公告ID"[[:space:]]*:' \
        "${OUTPUT_ROOT}/${site}/json" 2>/dev/null || true
    } | awk -F: '{total += $NF} END {print total + 0}')"
    snapshot_count="$({
      find "${OUTPUT_ROOT}/${site}/snapshots" -type f 2>/dev/null || true
    } | wc -l)"
    attachment_count="$({
      find "${OUTPUT_ROOT}/${site}/attachments" -type f ! -name '*.part' 2>/dev/null || true
    } | wc -l)"
    printf '  %-10s JSON=%s snapshots=%s attachments=%s\n' \
      "${site}" "${json_count}" "${snapshot_count}" "${attachment_count}"
  done
  echo "output_size=$(du -sh "${OUTPUT_ROOT}" 2>/dev/null | awk '{print $1}') free=$(free_gb)GB"
}

attach_task() {
  local requested="${1:-}" session
  if [[ -n "${requested}" ]]; then
    case "${requested}" in key1|key2|key3) ;; *) echo "未知队列：${requested}" >&2; return 2 ;; esac
    session="${SESSION_PREFIX}_${requested}"
    if tmux has-session -t "${session}" 2>/dev/null; then
      exec tmux attach -t "${session}"
    fi
    echo "队列 ${requested} 未运行。" >&2
    return 1
  fi
  for session in "${SESSION_PREFIX}_key1" "${SESSION_PREFIX}_key2" "${SESSION_PREFIX}_key3"; do
    if tmux has-session -t "${session}" 2>/dev/null; then
      exec tmux attach -t "${session}"
    fi
  done
  echo "没有正在运行的全量任务。" >&2
  return 1
}

stop_tasks() {
  local session found=0
  for session in "${SESSION_PREFIX}_key1" "${SESSION_PREFIX}_key2" "${SESSION_PREFIX}_key3"; do
    if tmux has-session -t "${session}" 2>/dev/null; then
      tmux send-keys -t "${session}" C-c
      echo "已向 ${session} 发送 Ctrl-C；Scrapy 将保存 JSON、快照和断点后退出。"
      found=1
    fi
  done
  ((found)) || echo "没有正在运行的全量任务。"
}

action="${1:-plan}"
case "${action}" in
  plan) show_plan ;;
  start) start_tasks ;;
  start-key3) start_key3 ;;
  status) show_status ;;
  attach) attach_task "${2:-}" ;;
  stop) stop_tasks ;;
  worker)
    [[ $# -eq 2 ]] || { usage >&2; exit 2; }
    worker "$2"
    ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
