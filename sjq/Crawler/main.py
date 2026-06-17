#!/usr/bin/env python3
"""
=============================================================================
全国公共资源交易平台（山西省）- 爬虫入口脚本
=============================================================================

功能:
  爬取 prec.sxzwfw.gov.cn "交易信息 > 工程建设" 下4个栏目的公告:
    1. 招标计划       → zbjh_招标计划.json
    2. 招标资审公告   → gczb_招标资审公告.json
    3. 中标候选人公示 → gchxr_中标候选人公示.json
    4. 中标结果公示   → gcgs_中标结果公示.json

使用方式:
  # 测试: 直连爬取最近1天
  python3 main.py --no-proxy --days 1

  # 代理模式(生产)
  python3 main.py --proxy-file proxies.txt --days 7

  # 只爬某几个栏目
  python3 main.py --no-proxy --days 1 --sections zbjh,gcgs

  # 调试
  python3 main.py --no-proxy --days 1 --log-level DEBUG
=============================================================================
"""

import argparse
import csv
import json
import logging
import os
import random
import sys
import time
from datetime import datetime
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    LOG_LEVEL, LOG_FORMAT, LOG_DATE_FORMAT,
    DAYS_LOOKBACK, REQUEST_DELAY_MIN, REQUEST_DELAY_MAX,
    PROXY_FILE, SECTION_DEFS, SECTION_COOLDOWN,
)
from proxy_pool import ProxyPool
from crawler import SectionCrawler

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
)
logger = logging.getLogger("tender_crawler")


def save_section_results(data: List[Dict], section_def: Dict, output_dir: str = "."):
    """保存单个栏目的结果为JSON和CSV."""
    name = section_def["name"]
    json_file = os.path.join(output_dir, section_def["output_json"])
    csv_file = os.path.join(output_dir, section_def["output_csv"])

    # JSON
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "source": "全国公共资源交易平台（山西省）",
                "section": name,
                "total_count": len(data),
                "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "days_lookback": DAYS_LOOKBACK,
            },
            "data": data,
        }, f, ensure_ascii=False, indent=2)
    logger.info("[%s] JSON已导出: %s (%d 条)", name, json_file, len(data))

    # CSV
    if not data:
        return
    fields = list(data[0].keys())
    with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    logger.info("[%s] CSV已导出: %s (%d 条)", name, csv_file, len(data))


def print_section_summary(data: List[Dict], section_name: str):
    """打印单个栏目数据摘要."""
    if not data:
        print("\n  [%s] 无数据" % section_name)
        return

    print("\n  [%s] 共 %d 条" % (section_name, len(data)))

    # 交易场所分布
    place_counts = {}
    for item in data:
        place = item.get("交易场所", "未知")
        place_counts[place] = place_counts.get(place, 0) + 1

    if place_counts:
        print("    交易场所分布:")
        for place, count in sorted(place_counts.items(), key=lambda x: -x[1]):
            print("      %s: %d" % (place, count))

    # 前3条预览
    print("    最新 3 条:")
    for i, item in enumerate(data[:3]):
        name = item.get("项目名称", "N/A")[:50]
        pub_time = item.get("发布时间") or item.get("列表发布日期", "N/A")
        print("      [%d] %s" % (i + 1, name))
        print("          时间: %s" % pub_time)

    # 字段完整性
    empty_fields = {f: 0 for f in data[0].keys()
                    if f not in ("全文文本", "详情ID", "详情页链接", "项目总投资原始文本")}
    for item in data:
        for f in empty_fields:
            if not item.get(f):
                empty_fields[f] += 1
    missing = {k: v for k, v in empty_fields.items() if v > 0}
    if missing:
        print("    字段缺失:")
        for f, c in sorted(missing.items(), key=lambda x: -x[1]):
            print("      %s: %d/%d" % (f, c, len(data)))


def main():
    parser = argparse.ArgumentParser(
        description="全国公共资源交易平台(山西省) - 爬虫",
    )
    parser.add_argument("--days", type=int, default=DAYS_LOOKBACK,
                        help="爬取最近N天 (默认: %d)" % DAYS_LOOKBACK)
    parser.add_argument("--proxy-file", type=str, default=PROXY_FILE)
    parser.add_argument("--no-proxy", action="store_true",
                        help="禁用代理, 直连模式")
    parser.add_argument("--no-free-proxy", action="store_true")
    parser.add_argument("--min-delay", type=float, default=REQUEST_DELAY_MIN)
    parser.add_argument("--max-delay", type=float, default=REQUEST_DELAY_MAX)
    parser.add_argument("--log-level", type=str, default=LOG_LEVEL,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--sections", type=str, default=None,
                        help="指定栏目(逗号分隔): zbjh,gczb,gchxr,gcgs (默认全部)")

    args = parser.parse_args()

    import config
    config.DAYS_LOOKBACK = args.days
    config.REQUEST_DELAY_MIN = args.min_delay
    config.REQUEST_DELAY_MAX = args.max_delay
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    logger.info("=" * 60)
    logger.info("  全国公共资源交易平台(山西省) - 爬虫")
    logger.info("=" * 60)
    logger.info("  日期范围: 最近 %d 天", args.days)
    logger.info("  代理模式: %s", "禁用" if args.no_proxy else "启用")
    logger.info("=" * 60)

    # 初始化代理池
    proxy_pool = None
    if not args.no_proxy:
        if args.no_free_proxy:
            config.FREE_PROXY_APIS = []
        proxy_pool = ProxyPool(proxy_file=args.proxy_file)
        ps = proxy_pool.stats()
        logger.info("代理池状态: 可用 %d 个", ps["available"])
        if ps["available"] == 0:
            logger.warning("代理池为空! 使用直连模式。")
            proxy_pool = None
    else:
        logger.info("代理已禁用, 使用直连模式")

    # 筛选要爬取的栏目
    if args.sections:
        selected_keys = set(args.sections.split(","))
        sections_to_crawl = [s for s in SECTION_DEFS if s["key"] in selected_keys]
    else:
        sections_to_crawl = list(SECTION_DEFS)

    logger.info("将爬取 %d 个栏目: %s", len(sections_to_crawl),
                 ", ".join(s["name"] for s in sections_to_crawl))
    logger.info("")

    # 逐个栏目爬取（随机打乱顺序，模拟非规律性浏览）
    random.shuffle(sections_to_crawl)
    all_results = {}
    total = 0

    for i, section_def in enumerate(sections_to_crawl):
        # 栏目间冷却间隔（非首个栏目时）
        if i > 0:
            cooldown = random.uniform(*SECTION_COOLDOWN)
            logger.info("栏目间冷却 %.0f 秒...", cooldown)
            time.sleep(cooldown)

        crawler = SectionCrawler(section_def, proxy_pool=proxy_pool)
        results = crawler.crawl()
        all_results[section_def["key"]] = results
        total += len(results)

        save_section_results(results, section_def)

    # 总摘要
    print("\n" + "=" * 60)
    print("  爬取完成 - 总计 %d 条" % total)
    print("=" * 60)
    for section_def in sections_to_crawl:
        key = section_def["key"]
        results = all_results.get(key, [])
        print_section_summary(results, section_def["name"])

    # 输出文件列表
    print("\n  输出文件:")
    for section_def in sections_to_crawl:
        print("    %s" % section_def["output_json"])
        print("    %s" % section_def["output_csv"])

    print("\n" + "=" * 60)
    logger.info("爬取结束! 总计 %d 条公告。", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
