#!/usr/bin/env python3
"""
=============================================================================
全国公共资源交易平台（山西省）- 爬虫入口脚本
=============================================================================

功能:
  爬取 prec.sxzwfw.gov.cn "交易信息 > 工程建设" 下4个栏目的公告:
    1. 招标计划       → zbjh_招标计划.{json,csv}
    2. 招标资审公告   → gczb_招标资审公告.{json,csv}
    3. 中标候选人公示 → gchxr_中标候选人公示.{json,csv}
    4. 中标结果公示   → gcgs_中标结果公示.{json,csv}

输出字段 (21个): 公共类型/项目名称/所属行业/组织形式/开标时间/标书发售时间/
  公告内容/招标人地址/招标人联系人/招标人联系方式/招标代理机构/招标代理机构地址/
  招标代理机构联系人/招标代理机构联系方式/监督部门地址/监督部门联系人/
  监督部门联系方式/依据文件/依据文号/发布日期/发布网站

架构:
  列表页: 顺序请求
  详情页: ThreadPoolExecutor(10线程) 并发, 每线程绑定一个代理IP
  IP策略: 天启API每次提取10个IP×3分钟, 最多调用2次API(=20个IP)

使用方式:
  # 默认模式(代理+最近2天)
  python3 main.py

  # 指定天数和栏目
  python3 main.py --days 1 --sections zbjh,gczb

  # 调整日志级别
  python3 main.py --log-level DEBUG
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
    DAYS_LOOKBACK, PROXY_FILE, SECTION_DEFS, SECTION_COOLDOWN,
)
from proxy_pool import ProxyPool, ProxyPoolEmptyError
from crawler import SectionCrawler, OUTPUT_FIELDS

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
)
logger = logging.getLogger("tender_crawler")


# ──────────────────── 输出 ────────────────────

def save_section_results(data: List[Dict], section_def: Dict, output_dir: str = "."):
    """保存单个栏目的结果为 JSON 和 CSV（字段按 OUTPUT_FIELDS 排序）。"""
    name = section_def["name"]
    json_file = os.path.join(output_dir, section_def["output_json"])
    csv_file = os.path.join(output_dir, section_def["output_csv"])

    # JSON（含元数据和全文文本）
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "source": "全国公共资源交易平台（山西省）",
                "section": name,
                "total_count": len(data),
                "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "days_lookback": DAYS_LOOKBACK,
                "fields": OUTPUT_FIELDS,
            },
            "data": data,
        }, f, ensure_ascii=False, indent=2)
    logger.info("[%s] JSON 已导出: %s (%d 条)", name, json_file, len(data))

    # CSV（按 OUTPUT_FIELDS 排序 + 辅助字段）
    if not data:
        return
    csv_fields = list(OUTPUT_FIELDS) + ["详情页链接", "详情ID", "全文文本", "交易场所", "列表发布日期"]
    with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    logger.info("[%s] CSV 已导出: %s (%d 条)", name, csv_file, len(data))


def print_section_summary(data: List[Dict], section_name: str):
    """打印单个栏目数据摘要。"""
    if not data:
        print("\n  [%s] 无数据" % section_name)
        return

    print("\n  [%s] 共 %d 条" % (section_name, len(data)))

    # 公共类型分布
    type_counts = {}
    for item in data:
        t = item.get("公共类型", "") or "未识别"
        type_counts[t] = type_counts.get(t, 0) + 1
    if type_counts:
        print("    公共类型分布:")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            print("      %s: %d" % (t, c))

    # 交易场所分布
    place_counts = {}
    for item in data:
        place = item.get("交易场所", "未知")
        place_counts[place] = place_counts.get(place, 0) + 1
    if place_counts:
        print("    交易场所分布:")
        for place, c in sorted(place_counts.items(), key=lambda x: -x[1]):
            print("      %s: %d" % (place, c))

    # 前3条预览
    print("    最新 3 条:")
    for i, item in enumerate(data[:3]):
        name = item.get("项目名称", "N/A")[:50]
        pub_time = item.get("发布日期") or item.get("列表发布日期", "N/A")
        ntype = item.get("公共类型", "")
        print("      [%d] %s" % (i + 1, name))
        print("          类型: %s | 日期: %s" % (ntype, pub_time))

    # 21字段完整性统计
    non_empty = {f: 0 for f in OUTPUT_FIELDS}
    for item in data:
        for f in OUTPUT_FIELDS:
            if item.get(f):
                non_empty[f] += 1
    total = len(data)
    # 只显示有缺失的字段
    incomplete = {k: v for k, v in non_empty.items() if v < total}
    if incomplete:
        print("    字段填充率 (<100%):")
        for f, c in sorted(incomplete.items(), key=lambda x: x[1]):
            print("      %s: %d/%d (%.0f%%)" % (f, c, total, c / total * 100))


# ──────────────────── 主流程 ────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="全国公共资源交易平台(山西省) - 爬虫 (21字段·多线程·IP池)",
    )
    parser.add_argument("--days", type=int, default=DAYS_LOOKBACK,
                        help="爬取最近N天 (默认: %d)" % DAYS_LOOKBACK)
    parser.add_argument("--no-proxy", action="store_true",
                        help="禁用代理, 直连模式 (仅用于测试)")
    parser.add_argument("--min-delay", type=float, default=None)
    parser.add_argument("--max-delay", type=float, default=None)
    parser.add_argument("--log-level", type=str, default=LOG_LEVEL,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--sections", type=str, default=None,
                        help="指定栏目(逗号分隔): zbjh,gczb,gchxr,gcgs (默认全部)")

    args = parser.parse_args()

    import config
    config.DAYS_LOOKBACK = args.days
    if args.min_delay is not None:
        config.REQUEST_DELAY_MIN = args.min_delay
    if args.max_delay is not None:
        config.REQUEST_DELAY_MAX = args.max_delay
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    logger.info("=" * 60)
    logger.info("  全国公共资源交易平台(山西省) - 爬虫 (21字段·多线程)")
    logger.info("=" * 60)
    logger.info("  日期范围: 最近 %d 天", args.days)
    logger.info("  代理模式: %s", "禁用(直连)" if args.no_proxy else "启用(天启API)")
    logger.info("  并发线程: %d | API上限: %d 次",
                 config.CONCURRENT_WORKERS, config.TIANQIIP_MAX_API_CALLS)
    logger.info("=" * 60)

    # 初始化代理池
    proxy_pool = None
    if not args.no_proxy:
        proxy_pool = ProxyPool(proxy_file=PROXY_FILE)
        ps = proxy_pool.stats()
        logger.info("代理池状态: 可用 %d 个 (最低 %d, API 上限 %d)",
                     ps["available"], ps["min_required"], ps["api_calls_limit"])
        if ps["available"] == 0:
            logger.critical("代理池为空！无法获取代理 IP，爬虫终止。")
            logger.critical(
                "请检查: 1) 天启 API 秘钥/sign 是否正确 "
                "2) 网络是否可达 3) proxies.txt 中是否有备用 IP"
            )
            sys.exit(1)
    else:
        logger.warning("代理已禁用，使用直连模式（仅用于测试）")

    # 筛选栏目
    if args.sections:
        selected_keys = set(args.sections.split(","))
        sections_to_crawl = [s for s in SECTION_DEFS if s["key"] in selected_keys]
    else:
        sections_to_crawl = list(SECTION_DEFS)

    logger.info("将爬取 %d 个栏目: %s", len(sections_to_crawl),
                 ", ".join(s["name"] for s in sections_to_crawl))
    logger.info("")

    # 逐个栏目爬取
    all_results = {}
    total = 0

    for i, section_def in enumerate(sections_to_crawl):
        if i > 0:
            cooldown = random.uniform(*SECTION_COOLDOWN)
            logger.info("栏目间冷却 %.0f 秒...", cooldown)
            time.sleep(cooldown)

        try:
            crawler = SectionCrawler(section_def, proxy_pool=proxy_pool)
            results = crawler.crawl()
            all_results[section_def["key"]] = results
            total += len(results)
        except ProxyPoolEmptyError as e:
            logger.warning("[%s] 代理池枯竭(API已达上限): %s", section_def["name"], e)
            logger.warning("剩余栏目将跳过，已爬取 %d 条", total)
            all_results[section_def["key"]] = []
        except Exception as e:
            logger.error("[%s] 爬取异常: %s", section_def["name"], e, exc_info=True)

        save_section_results(all_results.get(section_def["key"], []), section_def)

    # 总摘要
    print("\n" + "=" * 60)
    print("  爬取完成 - 总计 %d 条" % total)
    if proxy_pool:
        ps = proxy_pool.stats()
        print("  API 调用: %d/%d 次" % (ps["api_calls_used"], ps["api_calls_limit"]))
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
    logger.info("爬取结束! 总计 %d 条公告", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
