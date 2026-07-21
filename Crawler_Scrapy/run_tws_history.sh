#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/home/intsig/Crawler_Scrapy"
CONDA_BIN="/home/vipuser/miniconda3/bin/conda"
OUTPUT_ROOT="${PROJECT_DIR}/output"
LOOKBACK_DAYS="180"
DAYS_EXPLICIT="false"
ALL_HISTORY="false"
START_DATE=""
END_DATE=""
SITES="huaxin,jiubang"
SECTIONS="zbgg_zys,hxr,gs,zbjh"
PAGE_SIZE="100"
MAX_RECORDS="1000000"
MAX_PAGES="10000"

usage() {
    echo "用法：$0 [--all | --days N | --start-date YYYY-MM-DD [--end-date YYYY-MM-DD]]"
    echo "          [--sites huaxin,jiubang] [--sections 栏目列表] [--output-root 路径]"
    echo
    echo "默认并行采集华新和玖邦最近180天的四个栏目。"
    echo "--all 从最新页一直翻到各栏目最后一页，补采源站全部历史公告。"
    echo "显式指定 --start-date 后，以起始日期为准，--days 不再决定开始时间。"
}

require_value() {
    if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "参数 $1 缺少值" >&2
        exit 2
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            ALL_HISTORY="true"
            shift
            ;;
        --days)
            require_value "$@"
            LOOKBACK_DAYS="$2"
            DAYS_EXPLICIT="true"
            shift 2
            ;;
        --start-date)
            require_value "$@"
            START_DATE="$2"
            shift 2
            ;;
        --end-date)
            require_value "$@"
            END_DATE="$2"
            shift 2
            ;;
        --sites)
            require_value "$@"
            SITES="$2"
            shift 2
            ;;
        --sections)
            require_value "$@"
            SECTIONS="$2"
            shift 2
            ;;
        --output-root)
            require_value "$@"
            OUTPUT_ROOT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "未知参数：$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "${ALL_HISTORY}" == "true" ]] && \
   { [[ "${DAYS_EXPLICIT}" == "true" ]] || [[ -n "${START_DATE}" ]] || [[ -n "${END_DATE}" ]]; }; then
    echo "--all 不能与 --days、--start-date 或 --end-date 同时使用" >&2
    exit 2
fi

if [[ "${ALL_HISTORY}" != "true" ]] && ! [[ "${LOOKBACK_DAYS}" =~ ^[1-9][0-9]*$ ]]; then
    echo "--days 必须是正整数" >&2
    exit 2
fi

IFS=',' read -r -a SITE_ARRAY <<< "${SITES}"
if [[ ${#SITE_ARRAY[@]} -eq 0 ]]; then
    echo "--sites 不能为空" >&2
    exit 2
fi
for site in "${SITE_ARRAY[@]}"; do
    if [[ "${site}" != "huaxin" && "${site}" != "jiubang" ]]; then
        echo "不支持的网站：${site}；可选 huaxin,jiubang" >&2
        exit 2
    fi
done

# 当前固定认证代理；允许部署环境覆盖。代理不可用时Spider会立即停止，绝不直连。
export HUAXIN_PROXY_ENDPOINT="${HUAXIN_PROXY_ENDPOINT:-http://210.51.27.8:10000}"
export HUAXIN_PROXY_USERNAME="${HUAXIN_PROXY_USERNAME:-b88dff}"
export HUAXIN_PROXY_PASSWORD="${HUAXIN_PROXY_PASSWORD:-6dc46456}"
export PYTHONUNBUFFERED=1

RUN_ID="$(date '+%Y%m%d_%H%M%S')"
LOG_ROOT="${OUTPUT_ROOT}/logs/${RUN_ID}"
mkdir -p "${LOG_ROOT}"
cd "${PROJECT_DIR}"

WINDOW_ARGS=(-a "days=${LOOKBACK_DAYS}")
WINDOW_TEXT="最近${LOOKBACK_DAYS}天"
WINDOW_KEY="days_${LOOKBACK_DAYS}"
if [[ "${ALL_HISTORY}" == "true" ]]; then
    WINDOW_ARGS=()
    WINDOW_TEXT="源站全部历史（直到各栏目最后一页）"
    WINDOW_KEY="all"
elif [[ -n "${START_DATE}" ]]; then
    WINDOW_ARGS=(-a "start_date=${START_DATE}")
    WINDOW_TEXT="从${START_DATE}开始"
    WINDOW_KEY="from_${START_DATE}"
fi
if [[ -n "${END_DATE}" ]]; then
    WINDOW_ARGS+=(-a "end_date=${END_DATE}")
    WINDOW_TEXT="${WINDOW_TEXT}，截至${END_DATE}"
    WINDOW_KEY="${WINDOW_KEY}_to_${END_DATE}"
fi
WINDOW_KEY="${WINDOW_KEY// /_}"

run_site() {
    local site="$1"
    local log_file="${LOG_ROOT}/${site}.log"
    local runner_status
    local tee_status
    local -a pipeline_status
    # 同一时间窗口复用队列以支持中断续跑；不同时间窗口相互隔离。
    local job_dir="${OUTPUT_ROOT}/${site}/state/jobs/${WINDOW_KEY}"

    set +e
    {
        echo "[$(date '+%F %T')] 启动 ${site} 历史采集"
        echo "时间范围：${WINDOW_TEXT}"
        echo "栏目：${SECTIONS}"
        echo "代理出口：${HUAXIN_PROXY_ENDPOINT}（禁止服务器直连）"
        echo "并发：站点总并发4，单域名并发2，随机基础延迟2秒"
        echo "输出：${OUTPUT_ROOT}/${site}"
        echo "保存策略：复用去重索引，跳过未变化旧公告，只追加新增或变化版本"
        echo "日志：${log_file}"

        "${CONDA_BIN}" run --no-capture-output -n myenv \
            scrapy crawl "${site}" \
            -a "sections=${SECTIONS}" \
            -a "max_records=${MAX_RECORDS}" \
            -a "page_size=${PAGE_SIZE}" \
            -a "max_pages=${MAX_PAGES}" \
            "${WINDOW_ARGS[@]}" \
            -s CRAWLER_OUTBOUND_MODE=static \
            -s STATIC_PROXY_REQUIRED=True \
            -s STATIC_PROXY_AUTH_REQUIRED=True \
            -s STATIC_PROXY_RETRY_TIMES=2 \
            -s NOTICE_DEDUP_ENABLED=True \
            -s NOTICE_DEDUP_SKIP_KNOWN_IDENTITIES=False \
            -s NOTICE_AI_ENABLED=False \
            -s NOTICE_OUTPUT_ROOT="${OUTPUT_ROOT}" \
            -s FILES_STORE="${OUTPUT_ROOT}" \
            -s JOBDIR="${job_dir}" \
            -s HTTPCACHE_ENABLED=False \
            -s DIRECT_CONCURRENT_REQUESTS=4 \
            -s DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN=2 \
            -s DIRECT_DOWNLOAD_DELAY=2.0 \
            -s DIRECT_AUTOTHROTTLE_START_DELAY=3.0 \
            -s DIRECT_AUTOTHROTTLE_TARGET_CONCURRENCY=1.0 \
            -s DIRECT_AUTOTHROTTLE_MAX_DELAY=60.0 \
            -s DIRECT_RETRY_TIMES=2 \
            -s DIRECT_MAX_RESPONSES_PER_RUN=1000000 \
            -s DIRECT_GUARD_CONSECUTIVE_LIMIT=2 \
            -s DIRECT_GUARD_TOTAL_LIMIT=4 \
            -s DIRECT_GUARD_BASE_BACKOFF=60.0 \
            -s DIRECT_GUARD_MAX_BACKOFF=600.0 \
            -s LOG_LEVEL=INFO
    } 2>&1 | tee -a "${log_file}"
    pipeline_status=("${PIPESTATUS[@]}")
    runner_status=${pipeline_status[0]}
    tee_status=${pipeline_status[1]}
    set -e

    if [[ ${runner_status} -ne 0 || ${tee_status} -ne 0 ]]; then
        return 1
    fi
    # Scrapy 主动保护性关停时进程可能仍返回0；只有 finished 才算完整采集。
    if ! grep -Fq "固定代理关闭状态：reason=finished " "${log_file}"; then
        echo "[$(date '+%F %T')] ${site} 未以 finished 正常结束，请检查日志中的关闭原因" \
            | tee -a "${log_file}" >&2
        return 1
    fi
}

declare -a PIDS=()
declare -a RUN_SITES=()

stop_children() {
    echo "收到终止信号，正在停止站点采集进程..." >&2
    for pid in "${PIDS[@]:-}"; do
        kill -INT "${pid}" 2>/dev/null || true
    done
    wait || true
    exit 130
}
trap stop_children INT TERM

for site in "${SITE_ARRAY[@]}"; do
    run_site "${site}" &
    PIDS+=("$!")
    RUN_SITES+=("${site}")
done

set +e
overall_status=0
for index in "${!PIDS[@]}"; do
    wait "${PIDS[$index]}"
    status=$?
    if [[ ${status} -eq 0 ]]; then
        echo "[$(date '+%F %T')] ${RUN_SITES[$index]} 采集完成"
    else
        echo "[$(date '+%F %T')] ${RUN_SITES[$index]} 采集失败，退出码=${status}" >&2
        overall_status=1
    fi
done
set -e

echo "本次日志目录：${LOG_ROOT}"
echo "JSON/CSV和附件目录：${OUTPUT_ROOT}/<网站代码>/"
exit "${overall_status}"
