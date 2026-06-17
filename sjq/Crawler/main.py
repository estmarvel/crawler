#!/usr/bin/env python3
"""
=============================================================================
全国公共资源交易平台（山西省）- 招标计划公告爬虫 入口脚本
=============================================================================

功能:
  爬取 prec.sxzwfw.gov.cn "交易信息 > 招标计划" 栏目下最近一周的招标计划公告,
  提取全部可用字段, 数据保存在内存中并输出为 JSON/CSV 文件。

使用方式:
  # 基本使用(使用直连模式)
  python main.py

  # 使用代理池(从 proxies.txt 加载)
  python main.py --proxy-file proxies.txt

  # 指定输出文件
  python main.py --output results.json --output-csv results.csv

  # 自定义日期范围和请求延迟
  python main.py --days 3 --min-delay 3.0 --max-delay 6.0

  # 仅XML/调试模式
  python main.py --log-level DEBUG

配置代理:
  在项目目录下创建 proxies.txt, 每行一个代理:
    192.168.1.1:8080
    192.168.1.2:3128
    192.168.1.3:8080:username:password

  推荐使用付费代理服务(如快代理、芝麻代理)获取稳定代理IP。
  免费代理可用性差, 生产环境不建议依赖免费代理。

项目结构:
  tender_crawler/
  ├── config.py        # 全局配置
  ├── proxy_pool.py    # 代理IP池管理
  ├── crawler.py       # 爬虫核心逻辑(请求/解析)
  ├── main.py          # 入口脚本(本文件)
  └── requirements.txt # Python依赖

=============================================================================
"""

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime
from typing import Dict, List

# 将当前目录加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    LOG_LEVEL,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    OUTPUT_JSON_FILE,
    OUTPUT_CSV_FILE,
    DAYS_LOOKBACK,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    PROXY_FILE,
)
from proxy_pool import ProxyPool
from crawler import TenderCrawler

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
)
logger = logging.getLogger("tender_crawler")


# ======================== 数据导出 ========================

# CSV输出字段顺序(与详情页字段一致)
CSV_FIELDS = [
    "detail_id",
    "detail_url",
    "project_name",
    "project_code",
    "project_type",
    "total_investment",
    "total_investment_raw",
    "bid_content",
    "bid_method",
    "bidder_name",
    "supervision_dept",
    "publish_type",
    "publish_unit",
    "construction_site",
    "construction_scale",
    "expected_publish_date",
    "publish_time",
    "trading_place",
    "publish_date_list",
]


def save_to_json(data: List[Dict], filepath: str) -> str:
    """将数据保存为JSON文件.

    Args:
        data: 招标计划数据列表
        filepath: 输出文件路径

    Returns:
        输出文件绝对路径
    """
    abs_path = os.path.abspath(filepath)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "metadata": {
                    "source": "全国公共资源交易平台（山西省）",
                    "section": "招标计划公告",
                    "total_count": len(data),
                    "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "days_lookback": DAYS_LOOKBACK,
                },
                "data": data,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.info(f"JSON数据已导出: {abs_path} ({len(data)} 条)")
    return abs_path


def save_to_csv(data: List[Dict], filepath: str) -> str:
    """将数据保存为CSV文件.

    Args:
        data: 招标计划数据列表
        filepath: 输出文件路径

    Returns:
        输出文件绝对路径
    """
    abs_path = os.path.abspath(filepath)
    with open(abs_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    logger.info(f"CSV数据已导出: {abs_path} ({len(data)} 条)")
    return abs_path


# ======================== 数据显示 ========================

def print_summary(data: List[Dict]):
    """在终端打印数据摘要.

    显示前5条记录的关键字段, 便于快速验证数据正确性。
    """
    if not data:
        logger.info("无数据记录")
        return

    print("\n" + "=" * 80)
    print(f"  爬取结果摘要 - 共 {len(data)} 条记录")
    print("=" * 80)

    # 统计交易场所分布
    place_counts = {}
    for item in data:
        place = item.get("trading_place", "未知")
        place_counts[place] = place_counts.get(place, 0) + 1

    print(f"\n  交易场所分布:")
    for place, count in sorted(place_counts.items(), key=lambda x: -x[1]):
        print(f"    {place}: {count} 条")

    # 打印前5条数据
    print(f"\n  最新 {min(5, len(data))} 条记录预览:")
    print("-" * 80)
    for i, item in enumerate(data[:5]):
        print(f"\n  [{i + 1}] {item.get('project_name', 'N/A')[:60]}")
        print(f"      交易场所: {item.get('trading_place', 'N/A')}")
        print(f"      发布时间: {item.get('publish_time', item.get('publish_date_list', 'N/A'))}")
        print(f"      招标人:   {item.get('bidder_name', 'N/A')}")
        print(f"      总投资:   {item.get('total_investment', 'N/A')} 万元")
        print(f"      招标方式: {item.get('bid_method', 'N/A')}")
        print(f"      详情URL:  {item.get('detail_url', 'N/A')}")

    # 检查字段完整性
    empty_fields = {f: 0 for f in CSV_FIELDS if f not in ("detail_id", "detail_url", "total_investment_raw")}
    for item in data:
        for field in empty_fields:
            if not item.get(field):
                empty_fields[field] += 1

    fields_with_missing = {k: v for k, v in empty_fields.items() if v > 0}
    if fields_with_missing:
        print(f"\n  字段完整性检查(缺失记录数):")
        for field, count in sorted(fields_with_missing.items(), key=lambda x: -x[1]):
            pct = count / len(data) * 100
            print(f"    {field}: {count}/{len(data)} ({pct:.1f}%) 缺失")

    print("\n" + "=" * 80)


# ======================== 主函数 ========================

def main():
    parser = argparse.ArgumentParser(
        description="全国公共资源交易平台(山西省) - 招标计划公告爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                                    # 直连模式爬取最近7天
  %(prog)s --proxy-file proxies.txt           # 使用代理文件
  %(prog)s --days 3 --output recent.json     # 爬取最近3天, 输出JSON
  %(prog)s --no-json --csv-only               # 仅输出CSV
  %(prog)s --log-level DEBUG                  # 调试模式
        """,
    )

    # ---- 爬取范围 ----
    parser.add_argument(
        "--days", type=int, default=DAYS_LOOKBACK,
        help=f"爬取最近N天的数据 (默认: {DAYS_LOOKBACK})",
    )

    # ---- 代理配置 ----
    parser.add_argument(
        "--proxy-file", type=str, default=PROXY_FILE,
        help=f"代理配置文件路径 (默认: {PROXY_FILE})",
    )
    parser.add_argument(
        "--no-proxy", action="store_true",
        help="禁用代理, 使用直连模式",
    )
    parser.add_argument(
        "--no-free-proxy", action="store_true",
        help="不从免费API自动补充代理, 仅使用本地文件中的代理",
    )

    # ---- 输出配置 ----
    parser.add_argument(
        "--output", "-o", type=str, default=OUTPUT_JSON_FILE,
        help=f"JSON输出文件路径 (默认: {OUTPUT_JSON_FILE})",
    )
    parser.add_argument(
        "--output-csv", type=str, default=OUTPUT_CSV_FILE,
        help=f"CSV输出文件路径 (默认: {OUTPUT_CSV_FILE})",
    )
    parser.add_argument(
        "--no-json", action="store_true",
        help="不输出JSON文件",
    )
    parser.add_argument(
        "--csv-only", action="store_true",
        help="仅输出CSV文件(不输出JSON)",
    )

    # ---- 请求控制 ----
    parser.add_argument(
        "--min-delay", type=float, default=REQUEST_DELAY_MIN,
        help=f"请求最小延迟秒数 (默认: {REQUEST_DELAY_MIN})",
    )
    parser.add_argument(
        "--max-delay", type=float, default=REQUEST_DELAY_MAX,
        help=f"请求最大延迟秒数 (默认: {REQUEST_DELAY_MAX})",
    )

    # ---- 日志 ----
    parser.add_argument(
        "--log-level", type=str, default=LOG_LEVEL,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=f"日志级别 (默认: {LOG_LEVEL})",
    )

    args = parser.parse_args()

    # ---- 动态覆盖配置 ----
    import config
    config.DAYS_LOOKBACK = args.days
    config.REQUEST_DELAY_MIN = args.min_delay
    config.REQUEST_DELAY_MAX = args.max_delay

    # 设置日志级别
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # 启动信息
    logger.info("=" * 60)
    logger.info("  全国公共资源交易平台(山西省) - 招标计划公告爬虫")
    logger.info("=" * 60)
    logger.info(f"  目标站点:  https://prec.sxzwfw.gov.cn")
    logger.info(f"  日期范围:  最近 {args.days} 天")
    logger.info(f"  请求延迟:  {args.min_delay}s ~ {args.max_delay}s")
    logger.info(f"  代理模式:  {'禁用' if args.no_proxy else '启用'}")
    logger.info("=" * 60)

    # ---- 初始化代理池 ----
    proxy_pool = None
    if not args.no_proxy:
        logger.info(f"初始化代理池 (代理文件: {args.proxy_file})...")

        # 如果禁用免费代理补充, 修改FREE_PROXY_APIS为空
        if args.no_free_proxy:
            config.FREE_PROXY_APIS = []

        proxy_pool = ProxyPool(proxy_file=args.proxy_file)
        pool_stats = proxy_pool.stats()
        logger.info(f"代理池状态: 可用 {pool_stats['available']} 个")

        if pool_stats["available"] == 0:
            logger.warning("代理池为空! 将使用直连模式。")
            logger.warning("提示: 请在 proxies.txt 中配置代理, 或使用 --no-proxy 跳过代理配置。")
            proxy_pool = None
    else:
        logger.info("代理已禁用, 使用直连模式")

    # ---- 执行爬取 ----
    crawler = TenderCrawler(proxy_pool=proxy_pool)
    results = crawler.crawl()

    # ---- 输出数据 ----
    if not results:
        logger.warning("未爬取到任何数据!")
        logger.warning("可能原因:")
        logger.warning("  1. 最近N天内没有新的招标计划公告")
        logger.warning("  2. 网站结构发生变化, 解析规则需要更新")
        logger.warning("  3. 网络连接问题或IP被限制")
        logger.warning("建议: 使用 --log-level DEBUG 查看详细日志")
        return 1

    # 数据存储在内存中(results列表), 此处进行输出
    output_files = []

    # JSON输出
    if not args.no_json and not args.csv_only:
        try:
            path = save_to_json(results, args.output)
            output_files.append(path)
        except Exception as e:
            logger.error(f"JSON导出失败: {e}")

    # CSV输出
    if args.output_csv:
        try:
            path = save_to_csv(results, args.output_csv)
            output_files.append(path)
        except Exception as e:
            logger.error(f"CSV导出失败: {e}")

    # 打印摘要
    print_summary(results)

    # 输出文件汇总
    if output_files:
        logger.info(f"\n输出文件 ({len(output_files)} 个):")
        for f in output_files:
            logger.info(f"  {f}")

    logger.info(f"\n爬取完成! 共获取 {len(results)} 条招标计划公告。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
