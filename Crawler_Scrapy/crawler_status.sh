#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${ROOT_DIR}/new_output"
ALL_SITES=(sxjm sxzwfw bitbid huaxin jiubang qianji sxjkzcpt trade365 sxbid sxxindian runshihua gxebidding)

WATCH_INTERVAL=0
SELECTED_SITE=""

usage() {
    cat <<'EOF'
用法：
  ./crawler_status.sh                 查看全部站点一次
  ./crawler_status.sh sxjm            查看指定站点，并显示各 JSON 文件数量
  ./crawler_status.sh --watch 10      每 10 秒刷新一次
  ./crawler_status.sh sxjm --watch 5  每 5 秒刷新指定站点

说明：
  - 运行状态以实际进程为准，不以 resumable.lock 是否存在为准。
  - “公告数”是 new_output/<站点>/json 中已保存记录的累计数量。
  - “当前批次”来自最新日志，只表示本批次进度，不是全站完成百分比。
EOF
}

is_known_site() {
    local wanted="$1" site
    for site in "${ALL_SITES[@]}"; do
        [[ "$site" == "$wanted" ]] && return 0
    done
    return 1
}

while (($# > 0)); do
    case "$1" in
        --watch|-w)
            [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || {
                echo "错误：--watch 后面必须是刷新秒数。" >&2
                usage
                exit 2
            }
            WATCH_INTERVAL="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --*)
            echo "错误：未知参数 $1" >&2
            usage
            exit 2
            ;;
        *)
            [[ -z "$SELECTED_SITE" ]] || {
                echo "错误：一次只能指定一个站点。" >&2
                exit 2
            }
            SELECTED_SITE="$1"
            shift
            ;;
    esac
done

if [[ -n "$SELECTED_SITE" ]] && ! is_known_site "$SELECTED_SITE"; then
    echo "错误：未知站点 $SELECTED_SITE" >&2
    echo "可用站点：${ALL_SITES[*]}" >&2
    exit 2
fi

if ((WATCH_INTERVAL > 0 && WATCH_INTERVAL < 5)); then
    echo "刷新间隔过短，已自动调整为 5 秒。" >&2
    WATCH_INTERVAL=5
fi

format_elapsed() {
    local seconds="${1:-0}" days hours minutes
    ((seconds < 0)) && seconds=0
    days=$((seconds / 86400))
    hours=$(((seconds % 86400) / 3600))
    minutes=$(((seconds % 3600) / 60))
    if ((days > 0)); then
        printf '%dd%02dh%02dm' "$days" "$hours" "$minutes"
    elif ((hours > 0)); then
        printf '%dh%02dm' "$hours" "$minutes"
    else
        printf '%dm' "$minutes"
    fi
}

count_files() {
    local directory="$1" name_pattern="$2"
    [[ -d "$directory" ]] || {
        printf '0'
        return
    }
    find "$directory" -type f -name "$name_pattern" -printf '.' 2>/dev/null | wc -c
}

json_notice_count() {
    local json_dir="$1"
    local -a files=()
    [[ -d "$json_dir" ]] || {
        printf '0'
        return
    }
    mapfile -d '' files < <(find "$json_dir" -maxdepth 1 -type f -name '*.json' -print0 2>/dev/null)
    ((${#files[@]} > 0)) || {
        printf '0'
        return
    }
    rg --no-filename -c '^[[:space:]]*"平台代码"[[:space:]]*:' "${files[@]}" 2>/dev/null \
        | awk '{sum += $1} END {print sum + 0}'
}

latest_log_path() {
    local log_dir="$1"
    [[ -d "$log_dir" ]] || return 1
    find "$log_dir" -maxdepth 1 -type f -name '*.log' -printf '%T@\t%p\n' 2>/dev/null \
        | sort -nr \
        | head -n 1 \
        | cut -f2-
}

latest_log_progress() {
    local log_file="$1" progress scheduled
    [[ -n "$log_file" && -f "$log_file" ]] || {
        printf '暂无日志'
        return
    }

    progress="$(rg 'Crawled [0-9]+ pages .*scraped [0-9]+ items' "$log_file" 2>/dev/null | tail -n 1 || true)"
    if [[ -n "$progress" ]]; then
        progress="${progress#*INFO: }"
        printf '%s' "$progress"
        return
    fi

    scheduled="$(rg 'Resuming crawl \([0-9]+ requests scheduled\)' "$log_file" 2>/dev/null | tail -n 1 || true)"
    if [[ -n "$scheduled" ]]; then
        scheduled="${scheduled#*INFO: }"
        printf '%s' "$scheduled"
    else
        printf '日志已创建，尚无统计信息'
    fi
}

chunk_from_log() {
    local name
    name="$(basename "$1" 2>/dev/null || true)"
    if [[ "$name" =~ _chunk_([0-9]+)_ ]]; then
        printf '%d' "$((10#${BASH_REMATCH[1]}))"
    else
        printf '-'
    fi
}

site_process_info() {
    local site="$1" line status

    line="$(printf '%s\n' "$PROCESS_SNAPSHOT" | awk -v s="$site" '
        $0 ~ ("scrapy crawl " s "([[:space:]]|$)") {print; exit}
    ')"
    if [[ -n "$line" ]]; then
        status="公告采集中"
    else
        line="$(printf '%s\n' "$PROCESS_SNAPSHOT" | awk -v s="$site" '
            $0 ~ ("crawler_scrapy\\.sites\\." s "\\.download_attachments") {print; exit}
        ')"
        if [[ -n "$line" ]]; then
            status="附件下载中"
        else
            line="$(printf '%s\n' "$PROCESS_SNAPSHOT" | awk -v s="$site" '
                $0 ~ ("crawler_scrapy\\.site_runner " s "([[:space:]]|$)") {print; exit}
            ')"
            if [[ -n "$line" ]]; then
                status="冷却/切换中"
            elif printf '%s\n' "$TMUX_WINDOWS" | rg -qx "$site" >/dev/null 2>&1; then
                status="tmux空闲"
            else
                status="已停止"
            fi
        fi
    fi

    if [[ -n "$line" ]]; then
        read -r SITE_PID SITE_ELAPSED _ <<< "$line"
    else
        SITE_PID="-"
        SITE_ELAPSED="0"
    fi
    SITE_STATUS="$status"
}

show_file_breakdown() {
    local site="$1" json_dir="${OUTPUT_DIR}/${site}/json" file count
    local -a files=()
    [[ -d "$json_dir" ]] || return
    mapfile -d '' files < <(find "$json_dir" -maxdepth 1 -type f -name '*.json' -print0 2>/dev/null | sort -z)
    ((${#files[@]} > 0)) || return

    echo
    echo "${site} 各 JSON 文件："
    for file in "${files[@]}"; do
        count="$(rg --no-filename -c '^[[:space:]]*"平台代码"[[:space:]]*:' "$file" 2>/dev/null || true)"
        printf '  %-8s %s\n' "${count:-0} 条" "$(basename "$file")"
    done
}

show_status_once() {
    local site site_dir latest_log chunk progress notices snapshots attachments lock_note elapsed
    local -a sites

    PROCESS_SNAPSHOT="$(ps -eo pid=,etimes=,args= 2>/dev/null || true)"
    TMUX_WINDOWS="$(tmux list-windows -a -F '#{window_name}' 2>/dev/null || true)"
    if [[ -n "$SELECTED_SITE" ]]; then
        sites=("$SELECTED_SITE")
    else
        sites=("${ALL_SITES[@]}")
    fi

    echo "爬虫状态  $(date '+%F %T %Z')"
    printf '%-11s %-15s %-8s %-9s %10s %9s %10s %8s %-10s\n' \
        "站点" "状态" "PID" "已运行" "公告数" "HTML快照" "附件文件" "当前批" "锁文件"

    for site in "${sites[@]}"; do
        site_dir="${OUTPUT_DIR}/${site}"
        site_process_info "$site"
        notices="$(json_notice_count "${site_dir}/json")"
        snapshots="$(count_files "${site_dir}/snapshots" '*.html')"
        attachments="$(count_files "${site_dir}/attachments" '*')"
        latest_log="$(latest_log_path "${site_dir}/logs" || true)"
        chunk="$(chunk_from_log "$latest_log")"
        [[ -e "${site_dir}/state/resumable.lock" ]] && lock_note="存在" || lock_note="无"
        [[ "$SITE_PID" == "-" ]] && elapsed="-" || elapsed="$(format_elapsed "$SITE_ELAPSED")"

        printf '%-11s %-15s %-8s %-9s %10s %9s %10s %8s %-10s\n' \
            "$site" "$SITE_STATUS" "$SITE_PID" "$elapsed" "$notices" "$snapshots" "$attachments" "$chunk" "$lock_note"

        if [[ "$SITE_STATUS" != "已停止" && "$SITE_STATUS" != "tmux空闲" ]]; then
            progress="$(latest_log_progress "$latest_log")"
            printf '  最新进度：%s\n' "$progress"
            [[ -n "$latest_log" ]] && printf '  最新日志：%s\n' "${latest_log#"${ROOT_DIR}/"}"
        fi
    done

    if [[ -n "$SELECTED_SITE" ]]; then
        show_file_breakdown "$SELECTED_SITE"
    fi

    echo
    echo "说明：锁文件只用于断点任务互斥；状态为“已停止/tmux空闲”时，即使锁存在也不代表爬虫正在运行。"
    echo "      当前批来自最新日志；全站历史总量由分页动态发现，因此这里不虚构完成百分比。"
}

if ((WATCH_INTERVAL == 0)); then
    show_status_once
else
    while true; do
        [[ -t 1 ]] && clear
        show_status_once
        echo
        echo "每 ${WATCH_INTERVAL} 秒刷新；按 Ctrl+C 退出。"
        sleep "$WATCH_INTERVAL"
    done
fi
