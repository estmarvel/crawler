#!/usr/bin/env python3
import argparse
import logging
import sys
from config import LOG_FILE


def setup_logging():
    fmt = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


def main():
    setup_logging()
    logger = logging.getLogger("main")

    parser = argparse.ArgumentParser(description="金蝉爬虫")
    parser.add_argument("--pages",     type=int, default=5)
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--schedule",  action="store_true")
    parser.add_argument("--query",     type=str, default="")
    parser.add_argument("--stats",     action="store_true")
    parser.add_argument("--category",  type=str, default="")
    args = parser.parse_args()

    from storage import init_db
    init_db()

    if args.query:
        from storage import query_announcements
        rows = query_announcements(keyword=args.query, limit=20)
        if not rows:
            print("未找到匹配记录")
        for r in rows:
            print(f"[{r['category']}] {r['pub_date']}  {r['title']}")
            print(f"  URL: {r['url']}")
            print()
        return

    if args.stats:
        from storage import get_stats
        stats = get_stats()
        print("\n数据库统计：")
        for cat, ann_type, cnt in stats:
            print(f"  {cat} / {ann_type}: {cnt} 条")
        return

    fetch_detail = not args.no_detail

    if args.schedule:
        from scheduler import start_scheduler
        start_scheduler(max_pages=args.pages, fetch_detail=fetch_detail)
        return

    if args.category:
        from config import CATEGORIES
        from scraper import crawl_category
        if args.category not in CATEGORIES:
            logger.error("未知分类：%s", args.category)
            sys.exit(1)
        crawl_category(args.category, CATEGORIES[args.category], args.pages, fetch_detail)
    else:
        from scraper import crawl_all
        crawl_all(max_pages=args.pages, fetch_detail=fetch_detail)

    from storage import get_stats
    stats = get_stats()
    print("\n数据库统计：")
    for cat, ann_type, cnt in stats:
        print(f"  {cat} / {ann_type}: {cnt} 条")


if __name__ == "__main__":
    main()
