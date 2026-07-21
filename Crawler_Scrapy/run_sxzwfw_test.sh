#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/home/intsig/Crawler_Scrapy"
CONDA_BIN="/home/vipuser/miniconda3/bin/conda"
LOOKBACK_DAYS="365"
RECORDS_PER_SECTION="5"
MODULE="engineering"

usage() {
    echo "用法：$0 [--module engineering|government] [--days N]"
    echo
    echo "测试山西省公共资源交易平台指定业务模块，每种信息类型最多采集5条。"
    echo "engineering：工程建设六类；government：政府采购更正、结果两类。"
    echo "默认在最近365天内取各类型最新数据，并写入独立的时间戳测试目录。"
}

require_value() {
    if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "参数 $1 缺少值" >&2
        exit 2
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --module)
            require_value "$@"
            MODULE="$2"
            shift 2
            ;;
        --days)
            require_value "$@"
            LOOKBACK_DAYS="$2"
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

if ! [[ "${LOOKBACK_DAYS}" =~ ^[1-9][0-9]*$ ]]; then
    echo "--days 必须是正整数" >&2
    exit 2
fi

declare -a SECTION_LABELS
case "${MODULE}" in
    engineering)
        MODULE_LABEL="工程建设"
        SECTIONS="zbjh,zbgg_zys,bg,hxr,gs,qt"
        EXPECTED_TOTAL="30"
        OUTPUT_PREFIX="sxzwfw_5_each"
        SECTION_LABELS=(
            "zbjh:招标计划"
            "zbgg_zys:招标/资审公告"
            "bg:更正公告"
            "hxr:中标候选人公示"
            "gs:中标结果公示"
            "qt:其他公告"
        )
        ;;
    government)
        MODULE_LABEL="政府采购"
        # 采购公告 channelId=18 按当前要求保持禁用，只测试更正和结果。
        SECTIONS="zc_gz,zc_jg"
        EXPECTED_TOTAL="10"
        OUTPUT_PREFIX="sxzwfw_government_5_each"
        SECTION_LABELS=(
            "zc_gz:政府采购更正公告"
            "zc_jg:政府采购中标结果公告"
        )
        ;;
    *)
        echo "--module 只支持 engineering 或 government" >&2
        exit 2
        ;;
esac

# 当前临时固定认证代理。认证、连接或重试失败时 Spider 立即停止，绝不直连。
export HUAXIN_PROXY_ENDPOINT="${HUAXIN_PROXY_ENDPOINT:-http://210.51.27.8:10000}"
export HUAXIN_PROXY_USERNAME="${HUAXIN_PROXY_USERNAME:-b88dff}"
export HUAXIN_PROXY_PASSWORD="${HUAXIN_PROXY_PASSWORD:-6dc46456}"
export PYTHONUNBUFFERED=1

RUN_ID="$(date '+%Y%m%d_%H%M%S')"
TEST_ROOT="${PROJECT_DIR}/test_output/${OUTPUT_PREFIX}_${RUN_ID}"
LOG_FILE="${TEST_ROOT}/sxzwfw_test.log"
JOB_DIR="${TEST_ROOT}/sxzwfw/state/job"
mkdir -p "${TEST_ROOT}"
cd "${PROJECT_DIR}"

set +e
{
    echo "[$(date '+%F %T')] 启动山西${MODULE_LABEL}公告小批量测试"
    echo "类型：${SECTIONS}"
    echo "范围：最近${LOOKBACK_DAYS}天（单一查询窗口）"
    echo "目标：每种源站信息类型${RECORDS_PER_SECTION}条，共${EXPECTED_TOTAL}条"
    echo "固定代理：${HUAXIN_PROXY_ENDPOINT}（禁止服务器直连）"
    echo "访问频率：总并发2、单域名1、随机基础延迟3秒、AutoThrottle目标0.5"
    echo "测试结果：${TEST_ROOT}/sxzwfw"
    echo "测试日志：${LOG_FILE}"

    "${CONDA_BIN}" run --no-capture-output -n myenv \
        scrapy crawl sxzwfw \
        -a "sections=${SECTIONS}" \
        -a "days=${LOOKBACK_DAYS}" \
        -a "split_months=false" \
        -a "max_records=${RECORDS_PER_SECTION}" \
        -a "max_pages=10" \
        -s CRAWLER_OUTBOUND_MODE=static \
        -s STATIC_PROXY_REQUIRED=True \
        -s STATIC_PROXY_AUTH_REQUIRED=True \
        -s STATIC_PROXY_RETRY_TIMES=2 \
        -s NOTICE_DEDUP_ENABLED=True \
        -s NOTICE_DEDUP_SKIP_KNOWN_IDENTITIES=False \
        -s NOTICE_AI_ENABLED=False \
        -s NOTICE_SNAPSHOT_ENABLED=True \
        -s NOTICE_OUTPUT_ROOT="${TEST_ROOT}" \
        -s FILES_STORE="${TEST_ROOT}" \
        -s JOBDIR="${JOB_DIR}" \
        -s HTTPCACHE_ENABLED=False \
        -s DIRECT_CONCURRENT_REQUESTS=2 \
        -s DIRECT_CONCURRENT_REQUESTS_PER_DOMAIN=1 \
        -s DIRECT_DOWNLOAD_DELAY=3.0 \
        -s DIRECT_AUTOTHROTTLE_START_DELAY=3.0 \
        -s DIRECT_AUTOTHROTTLE_TARGET_CONCURRENCY=0.5 \
        -s DIRECT_AUTOTHROTTLE_MAX_DELAY=60.0 \
        -s DIRECT_RETRY_TIMES=2 \
        -s DIRECT_MAX_RESPONSES_PER_RUN=1000 \
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
    echo "测试运行失败，请查看：${LOG_FILE}" >&2
    exit 1
fi
if ! grep -Fq "固定代理关闭状态：reason=finished " "${LOG_FILE}"; then
    echo "测试未正常结束，可能由代理或访问限制保护性终止：${LOG_FILE}" >&2
    exit 1
fi

validation_status=0

echo
echo "各源站信息类型详情构建检查："
for entry in "${SECTION_LABELS[@]}"; do
    section="${entry%%:*}"
    label="${entry#*:}"
    built_count="$(
        sed -n "s/.*'sxzwfw\/items_built\/${section}': \([0-9][0-9]*\).*/\1/p" \
            "${LOG_FILE}" | tail -n 1
    )"
    built_count="${built_count:-0}"
    if [[ "${built_count}" == "${RECORDS_PER_SECTION}" ]]; then
        echo "  [通过] ${label}：已构建${RECORDS_PER_SECTION}条"
    else
        echo "  [注意] ${label}：实际构建${built_count}条，目标${RECORDS_PER_SECTION}条，请检查日志"
        validation_status=1
    fi
done

item_count="$(
    sed -n "s/.*'item_scraped_count': \([0-9][0-9]*\).*/\1/p" "${LOG_FILE}" \
        | tail -n 1
)"
item_count="${item_count:-0}"
if [[ "${item_count}" == "${EXPECTED_TOTAL}" ]]; then
    echo "导出检查：通过，共导出${EXPECTED_TOTAL}条公告。"
else
    echo "导出检查：实际导出${item_count}条，目标${EXPECTED_TOTAL}条；请检查详情解析或附件链路。"
    validation_status=1
fi

if [[ "${MODULE}" == "government" ]]; then
    REPORT_FILE="${TEST_ROOT}/government_validation_report.json"
    echo
    echo "政府采购字段、配对、快照和附件检查："
    set +e
    "${CONDA_BIN}" run --no-capture-output -n myenv \
        python -m crawler_scrapy.sites.sxzwfw.validate_government_output \
        "${TEST_ROOT}/sxzwfw" \
        --storage-root "${TEST_ROOT}" \
        --expected "${RECORDS_PER_SECTION}" \
        --report "${REPORT_FILE}" \
        2>&1 | tee -a "${LOG_FILE}"
    validator_status=${PIPESTATUS[0]}
    set -e
    if [[ ${validator_status} -ne 0 ]]; then
        validation_status=1
    fi
    echo "校验报告：${REPORT_FILE}"
fi

echo "JSON：${TEST_ROOT}/sxzwfw/json/"
echo "CSV：${TEST_ROOT}/sxzwfw/csv/"
echo "HTML快照：${TEST_ROOT}/sxzwfw/snapshots/"
echo "附件：${TEST_ROOT}/sxzwfw/attachments/"
echo "日志：${LOG_FILE}"
exit "${validation_status}"
