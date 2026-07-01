#!/bin/bash
set -Eeuo pipefail

PROJECT_DIR="/home/intsig/zwx/shanxi/spider"
ROOT_DIR="/home/intsig/zwx/shanxi"
PYTHON_BIN="/home/vipuser/miniconda3/envs/myenv/bin/python"

# 读取环境变量
source "$ROOT_DIR/.env"

mkdir -p "$ROOT_DIR/output"
mkdir -p "$ROOT_DIR/log"

cd "$PROJECT_DIR"

echo "============================================================"
echo "开始运行山西爬虫: $(date '+%Y-%m-%d %H:%M:%S')"
echo "当前目录: $(pwd)"
echo "Python: $PYTHON_BIN"
echo "脚本: $PROJECT_DIR/shanxi_regex_ai.py"
echo "============================================================"

"$PYTHON_BIN" "$PROJECT_DIR/shanxi_regex_ai.py"

echo "============================================================"
echo "运行结束: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"