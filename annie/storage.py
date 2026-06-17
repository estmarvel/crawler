import sqlite3
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger(__name__)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            category      TEXT,
            ann_type      TEXT,
            title         TEXT    NOT NULL,
            url           TEXT    UNIQUE NOT NULL,
            pub_date      TEXT,
            location      TEXT,
            project_no    TEXT,
            project_type  TEXT,
            budget        TEXT,
            reg_start     TEXT,
            reg_end       TEXT,
            bid_deadline  TEXT,
            bid_opening   TEXT,
            qualification TEXT,
            tenderee      TEXT,
            agency        TEXT,
            content       TEXT,
            status        TEXT    DEFAULT '有效',
            crawled_at    TEXT    NOT NULL
        )
    """)
    # 兼容旧库：逐列补加新字段
    new_cols = [
        ("ann_type",      "TEXT"),
        ("reg_start",     "TEXT"),
        ("reg_end",       "TEXT"),
        ("bid_deadline",  "TEXT"),
        ("bid_opening",   "TEXT"),
        ("qualification", "TEXT"),
        ("tenderee",      "TEXT"),
        ("agency",        "TEXT"),
        ("project_type",  "TEXT"),
    ]
    for col, typ in new_cols:
        try:
            cur.execute(f"ALTER TABLE announcements ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    cur.execute("""
        CREATE TABLE IF NOT EXISTS crawl_log (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            page     INTEGER,
            count    INTEGER,
            run_at   TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info("数据库初始化完成：%s", DB_PATH)


def save_announcement(item: dict) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT OR IGNORE INTO announcements
                (category, ann_type, title, url, pub_date, location,
                 project_no, project_type, budget, reg_start, reg_end,
                 bid_deadline, bid_opening, qualification, tenderee,
                 agency, content, status, crawled_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            item.get("category", ""),
            item.get("ann_type", ""),
            item.get("title", ""),
            item.get("url", ""),
            item.get("pub_date", ""),
            item.get("location", ""),
            item.get("project_no", ""),
            item.get("project_type", ""),
            item.get("budget", ""),
            item.get("reg_start", ""),
            item.get("reg_end", ""),
            item.get("bid_deadline", ""),
            item.get("bid_opening", ""),
            item.get("qualification", ""),
            item.get("tenderee", ""),
            item.get("agency", ""),
            item.get("content", ""),
            item.get("status", "有效"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.Error as e:
        logger.error("存储失败 [%s]: %s", item.get("url"), e)
        return False
    finally:
        conn.close()


def save_crawl_log(category: str, page: int, count: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO crawl_log (category, page, count, run_at) VALUES (?,?,?,?)",
        (category, page, count, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def query_announcements(keyword: str = "", category: str = "", ann_type: str = "", limit: int = 20):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM announcements WHERE 1=1"
    params = []
    if keyword:
        sql += " AND title LIKE ?"
        params.append(f"%{keyword}%")
    if category:
        sql += " AND category = ?"
        params.append(category)
    if ann_type:
        sql += " AND ann_type = ?"
        params.append(ann_type)
    sql += " ORDER BY crawled_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT category, ann_type, COUNT(*) as cnt FROM announcements GROUP BY category, ann_type"
    ).fetchall()
    conn.close()
    return [(r[0], r[1], r[2]) for r in rows]
