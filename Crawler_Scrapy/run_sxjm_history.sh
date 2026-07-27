#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

scrapy crawl sxjm \
  -a sections="${SECTIONS:-zbgg,hxr,zbjg,zzgg}" \
  -a start_date="${START_DATE:-2020-01-01}" \
  -a max_records="${MAX_RECORDS:-100000}" \
  -a max_pages="${MAX_PAGES:-10000}" \
  -a page_size="${PAGE_SIZE:-50}"

