"""
=============================================================================
国信e采购平台 (gx.e-bidding.org) - 爬虫入口
=============================================================================
用法:
  cd /home/intsig/sjq/Crawler/guoxin
  python main.py

输出目录:
  results/yifa/    - 依法招标
  results/feiyifa/ - 非依法招标
    每个目录下按子栏目输出 JSON + CSV:
      zbgg_招标公告.json / .csv
      zbys_变更公告.json / .csv
      zbjg_中标结果公告.json / .csv
      hxr_中标候选人公告.json / .csv
      zzgg_终止公告.json / .csv
=============================================================================
"""

import logging
import os
import sys

_crawler_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_this_dir = os.path.dirname(os.path.abspath(__file__))

# ── 1. 加载父项目 config, 限制天启 API 调用 ──
sys.path.insert(0, _crawler_root)
import config as _parent_config
_parent_config.TIANQIIP_MAX_API_CALLS = 2

# ── 2. 加载本模块 ──
sys.path.insert(0, _this_dir)
from settings import LOG_LEVEL
from crawler import GuoxinCrawler

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("guoxin")


def main():
    output_dir = os.path.join(_this_dir, "results")
    crawler = GuoxinCrawler(output_dir=output_dir)
    crawler.run()


if __name__ == "__main__":
    main()