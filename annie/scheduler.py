import schedule
import time
import logging
from config import SCHEDULE_HOUR, SCHEDULE_MINUTE

logger = logging.getLogger(__name__)


def run_job(max_pages: int = 5, fetch_detail: bool = True):
    from scraper import crawl_all
    from storage import get_stats

    logger.info("========== 定时任务启动 ==========")
    results = crawl_all(max_pages=max_pages, fetch_detail=fetch_detail)
    stats   = get_stats()

    logger.info("---------- 本次新增汇总 ----------")
    for cat, cnt in results.items():
        logger.info("  %s: 新增 %d 条", cat, cnt)
    logger.info("---------- 数据库总量 ----------")
    for cat, cnt in stats.items():
        logger.info("  %s: 共 %d 条", cat, cnt)
    logger.info("========== 任务完成 ==========")


def start_scheduler(max_pages: int = 5, fetch_detail: bool = True):
    run_time = f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}"
    logger.info("定时任务已设置，每天 %s 自动运行", run_time)
    schedule.every().day.at(run_time).do(run_job, max_pages=max_pages, fetch_detail=fetch_detail)
    while True:
        schedule.run_pending()
        time.sleep(60)
