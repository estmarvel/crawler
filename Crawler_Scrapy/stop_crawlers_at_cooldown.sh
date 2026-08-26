#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${ROOT_DIR}/new_output"
CONTROL_DIR="${OUTPUT_DIR}/control"
LOG_FILE="${CONTROL_DIR}/stop_at_cooldown.log"
LOCK_FILE="${CONTROL_DIR}/stop_at_cooldown.lock"
KNOWN_SITES=(sxjm sxzwfw bitbid huaxin jiubang qianji sxjkzcpt trade365 sxbid runshihua gxebidding)

mkdir -p "$CONTROL_DIR"

usage() {
    cat <<'EOF'
用法：
  ./stop_crawlers_at_cooldown.sh sxjm sxzwfw bitbid
  ./stop_crawlers_at_cooldown.sh --all

作用：
  等待当前 Scrapy 公告批次正常结束并进入冷却期，然后仅向对应的
  site_runner 发送 SIGINT。JSON、快照、去重索引及 JOBDIR 会被保留。

注意：
  本脚本不会中断仍在运行的 Scrapy 子进程，也不会删除锁文件或断点。
EOF
}

is_known_site() {
    local wanted="$1" site
    for site in "${KNOWN_SITES[@]}"; do
        [[ "$site" == "$wanted" ]] && return 0
    done
    return 1
}

log() {
    local message="[$(date '+%F %T')] $*"
    printf '%s\n' "$message"
    printf '%s\n' "$message" >> "$LOG_FILE"
}

runner_pid() {
    local site="$1"
    ps -eo pid=,args= 2>/dev/null | awk -v s="$site" '
        $0 ~ ("[p]ython[^ ]* -m crawler_scrapy\\.site_runner " s "([[:space:]]|$)") {
            print $1
            exit
        }
    '
}

spider_pid() {
    local site="$1"
    ps -eo pid=,args= 2>/dev/null | awk -v s="$site" '
        $0 ~ ("[p]ython[^ ]* -m scrapy crawl " s "([[:space:]]|$)") {
            print $1
            exit
        }
    '
}

latest_notice_log() {
    local site="$1" log_dir="${OUTPUT_DIR}/${site}/logs"
    [[ -d "$log_dir" ]] || return 1
    find "$log_dir" -maxdepth 1 -type f -name '*_chunk_*.log' -printf '%T@\t%p\n' 2>/dev/null \
        | sort -nr \
        | head -n 1 \
        | cut -f2-
}

wait_for_cooldown() {
    local site="$1" runner spider latest seen_active=0

    runner="$(runner_pid "$site")"
    if [[ -z "$runner" ]]; then
        log "${site}: 没有 site_runner 进程，无需停止。"
        return 0
    fi

    log "${site}: 已监控 runner PID=${runner}，等待当前公告批次正常进入冷却。"
    while true; do
        runner="$(runner_pid "$site")"
        if [[ -z "$runner" ]]; then
            log "${site}: runner 已自行退出。"
            return 0
        fi

        spider="$(spider_pid "$site")"
        if [[ -n "$spider" ]]; then
            seen_active=1
            sleep 2
            continue
        fi

        latest="$(latest_notice_log "$site" || true)"
        if ((seen_active == 1)) \
            && [[ -n "$latest" ]] \
            && rg -q "'finish_reason': 'closespider_pagecount'" "$latest" 2>/dev/null; then
            log "${site}: 当前批次已完整关闭，正在冷却；向 runner PID=${runner} 发送 SIGINT。"
            if kill -INT "$runner" 2>/dev/null; then
                for _ in {1..15}; do
                    kill -0 "$runner" 2>/dev/null || break
                    sleep 1
                done
                if kill -0 "$runner" 2>/dev/null; then
                    log "${site}: SIGINT 已发送，但 runner 15秒后仍存在，请人工检查；未强制终止。"
                    return 1
                fi
                log "${site}: 已安全停止，断点保留。"
                return 0
            fi
            log "${site}: SIGINT 发送失败，runner 可能已自行退出。"
            return 1
        fi

        sleep 2
    done
}

sites=()
if (($# == 0)); then
    usage
    exit 2
fi
if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    usage
    exit 0
fi
if [[ "$1" == "--all" ]]; then
    sites=("${KNOWN_SITES[@]}")
else
    for site in "$@"; do
        if ! is_known_site "$site"; then
            echo "未知站点：$site" >&2
            usage
            exit 2
        fi
        sites+=("$site")
    done
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "已有安全停止监控正在运行：$LOCK_FILE" >&2
    exit 5
fi

log "启动下一冷却期安全停止监控：${sites[*]}"
pids=()
for site in "${sites[@]}"; do
    wait_for_cooldown "$site" &
    pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
    wait "$pid" || status=1
done
log "安全停止监控结束，status=${status}。"
exit "$status"
