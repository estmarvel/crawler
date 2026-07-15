#!/usr/bin/env bash
set -e
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
mkdir -p logs output pdf_cache ocr_cache
if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi
PYTHON_BIN="${PYTHON_BIN:-python}"
$PYTHON_BIN -m scrapy crawl sxbid_notice \
  -a crawl_days=1 \
  -a max_pages=5 \
  --logfile "logs/sxbid_$(date +%Y%m%d).log"
