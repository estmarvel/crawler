#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/home/intsig/Crawler_Scrapy"
CONDA_BIN="/home/vipuser/miniconda3/bin/conda"
OUTPUT_ROOT="${PROJECT_DIR}/output"
LOOKBACK_DAYS="180"
START_DATE=""
END_DATE=""
SECTIONS="zbjh,zbgg_zys,bg,hxr,gs,qt"
MAX_RECORDS="1000000"
MAX_PAGES="10000"

usage() {
    echo "用法：$0 [--days N] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]"
    echo "          [--sections 栏目列表] [--max-records N] [--max-pages N]"
    echo "          [--output-root 路径]"
    echo
    echo "默认采集山西省公共资源交易平台工程建设六个栏目最近180天的数据。"
    echo "显式指定 --start-date 后，以起始日期为准；可同时用 --end-date 指定结束日期。"
}

require_value() {
    if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "参数 $1 缺少值" >&2
        exit 2
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --days)
            require_value "$@"
            LOOKBACK_DAYS="$2"
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
        --sections)
            require_value "$@"
            SECTIONS="$2"
            shift 2
            ;;
        --max-records)
            require_value "$@"
            MAX_RECORDS="$2"
            shift 2
            ;;
        --max-pages)
            require_value "$@"
            MAX_PAGES="$2"
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

for value_name in LOOKBACK_DAYS MAX_RECORDS MAX_PAGES; do
    value="${!value_name}"
    if ! [[ "${value}" =~ ^[1-9][0-9]*$ ]]; then
        echo "${value_name} 必须是正整数" >&2
        exit 2
    fi
done

# 当前临时固定认证代理；代理不可用或认证失败时 Spider 立即停止，绝不直连。
export HUAXIN_PROXY_ENDPOINT="${HUAXIN_PROXY_ENDPOINT:-http://210.51.27.8:10000}"
export HUAXIN_PROXY_USERNAME="${HUAXIN_PROXY_USERNAME:-b88dff}"
export HUAXIN_PROXY_PASSWORD="${HUAXIN_PROXY_PASSWORD:-6dc46456}"
export PYTHONUNBUFFERED=1

RUN_ID="$(date '+%Y%m%d_%H%M%S')"
LOG_DIR="${OUTPUT_ROOT}/logs/${RUN_ID}"
LOG_FILE="${LOG_DIR}/sxzwfw.log"
mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}"

WINDOW_ARGS=(-a "days=${LOOKBACK_DAYS}")
WINDOW_TEXT="最近${LOOKBACK_DAYS}天"
WINDOW_KEY="days_${LOOKBACK_DAYS}"
if [[ -n "${START_DATE}" ]]; then
    WINDOW_ARGS=(-a "start_date=${START_DATE}")
    WINDOW_TEXT="从${START_DATE}开始"
    WINDOW_KEY="from_${START_DATE}"
fi
if [[ -n "${END_DATE}" ]]; then
    WINDOW_ARGS+=(-a "end_date=${END_DATE}")
    WINDOW_TEXT="${WINDOW_TEXT}，截至${END_DATE}"
    WINDOW_KEY="${WINDOW_KEY}_to_${END_DATE}"
fi
JOB_DIR="${OUTPUT_ROOT}/sxzwfw/state/jobs/${WINDOW_KEY}"

set +e
{
    echo "[$(date '+%F %T')] 启动山西省公共资源交易平台工程建设公告采集"
    echo "时间范围：${WINDOW_TEXT}"
    echo "栏目：${SECTIONS}"
    echo "固定代理：${HUAXIN_PROXY_ENDPOINT}（禁止服务器直连）"
    echo "并发：总并发4、单域名2、随机基础延迟2.5秒、AutoThrottle目标0.75"
    echo "结果目录：${OUTPUT_ROOT}/sxzwfw"
    echo "日志文件：${LOG_FILE}"

    "${CONDA_BIN}" run --no-capture-output -n myenv \
        scrapy crawl sxzwfw \
        -a "sections=${SECTIONS}" \
        -a "max_records=${MAX_RECORDS}" \
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
        -s JOBDIR="${JOB_DIR}" \
        -s HTTPCACHE_ENABLED=False \
        -s DIRECT_CONCURRENT_REQUESTS=4 \
        -s DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN=2 \
        -s DIRECT_DOWNLOAD_DELAY=2.5 \
        -s DIRECT_AUTOTHROTTLE_START_DELAY=3.0 \
        -s DIRECT_AUTOTHROTTLE_TARGET_CONCURRENCY=0.75 \
        -s DIRECT_AUTOTHROTTLE_MAX_DELAY=60.0 \
        -s DIRECT_RETRY_TIMES=2 \
        -s DIRECT_MAX_RESPONSES_PER_RUN=1000000 \
        -s DIRECT_GUARD_CONSECUTIVE_LIMIT=2 \
        -s DIRECT_GUARD_TOTAL_LIMIT=4 \
        -s DIRECT_GUARD_BASE_BACKOFF=60.0 \
        -s DIRECT_GUARD_MAX_BACKOFF=600.0 \
        -s LOG_LEVEL=INFO
} 2>&1 | tee -a "${LOG_FILE}"
pipeline_status=("${PIPESTATUS[@]}")
runner_status=${pipeline_status[0]}
tee_status=${pipeline_status[1]}
set -e

if [[ ${runner_status} -ne 0 || ${tee_status} -ne 0 ]]; then
    echo "采集失败；请查看 ${LOG_FILE}" >&2
    exit 1
fi
if ! grep -Fq "固定代理关闭状态：reason=finished " "${LOG_FILE}"; then
    echo "任务未以 finished 正常结束，可能由代理或访问限制保护性终止；请查看 ${LOG_FILE}" >&2
    exit 1
fi

echo "采集完成。JSON/CSV、HTML快照和附件：${OUTPUT_ROOT}/sxzwfw/"
echo "日志：${LOG_FILE}"
