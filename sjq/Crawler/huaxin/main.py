"""
=============================================================================
华新阳光采购平台 (ygcgpt.com) - 爬虫入口
=============================================================================
用法:
  cd /home/intsig/sjq/Crawler/huaxin
  python main.py

测试模式 (当前):
  - 天启 API 仅调用 1 次 → 10 个 IP, 有效期 3 分钟
  - 每栏目最多 3 页, 每页 20 条 → 约 3 分钟完成
  - 正式爬取时改 settings.py: MAX_LIST_PAGES=50, DETAIL_PAGE_SIZE=100
                               TIANQIIP_MAX_API_CALLS=2

输出目录: results/
  - zbjh_招标计划.json / .csv
  - gczb_招标公告.json / .csv
  - gchxr_候选公告.json / .csv
  - gcgs_结果公示.json / .csv
=============================================================================
"""

import logging
import os
import sys

_crawler_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_this_dir = os.path.dirname(os.path.abspath(__file__))

# ── 1. 加载父项目 config, 限制 API 调用 ──
sys.path.insert(0, _crawler_root)
import config as _parent_config
_parent_config.TIANQIIP_MAX_API_CALLS = 1

# ── 2. 加载本模块 (settings.py 不会与父 config.py 冲突) ──
sys.path.insert(0, _this_dir)
from settings import AUTH_TOKEN, LOG_LEVEL
from crawler import HuaxinCrawler

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("huaxin")


def main():
    output_dir = os.path.join(_this_dir, "results")
    crawler = HuaxinCrawler(token=AUTH_TOKEN, output_dir=output_dir)
    crawler.run()


if __name__ == "__main__":
    main()
