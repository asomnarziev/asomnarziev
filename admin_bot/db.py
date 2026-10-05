"""
admin_bot/db.py
---------------
SQLite da yuborilgan hujjat ma'lumotlarini saqlaydi.

Jadval: admin_bot_sent
  entity      — 'demand' | 'paymentin' | 'salesreturn' | 'supply'
  doc_id      — MoySklad hujjat UUID si
  updated_at  — MoySklad dagi oxirgi o'zgarish vaqti
  sent_at     — bizda yuborilgan vaqt (UTC)
  msg_data    — JSON: {chat_id: message_id, ...} — har admin uchun message_id
"""

import json
import sqlite3
import logging
from datetime import datetime, timezone
from admin_bot.config import DB_PATH

logger = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


class _tx:
    """with _tx() as conn: — commit/rollback va ulanishni albatta yopadi."""

    def __enter__(self) -> sqlite3.Connection:
        self._c = _conn()
        return self._c

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._c.commit()
            else:
                self._c.rollback()
        finally:
            self._c.close()
        return False


def init_db() -> None:
    create_sql = """
        CREATE TABLE IF NOT EXISTS admin_bot_sent (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            entity      TEXT NOT NULL,
            doc_id      TEXT NOT NULL,
            updated_at  TEXT,
            sent_at     TEXT NOT NULL,
            msg_data    TEXT,
            doc_info    TEXT,
            UNIQUE (entity, doc_id)
        );
        CREATE INDEX IF NOT EXISTS idx_admin_bot_sent
            ON admin_bot_sent (entity, doc_id);
    """
    # Eski jadvalga yangi ustunlar qo'shish
    alter_sqls = [
        "ALTER TABLE admin_bot_sent ADD COLUMN updated_at TEXT;",
        "ALTER TABLE admin_bot_sent ADD COLUMN msg_data TEXT;",
        "ALTER TABLE admin_bot_sent ADD COLUMN doc_info TEXT;",
    ]
    try:
        with _tx() as conn:
            conn.executescript(create_sql)
            for sql in alter_sqls:
                try:
                    conn.execute(sql)
                except Exception:
                    pass  # Ustun allaqachon bor
        logger.info(f"✅ admin_bot_sent jadvali tayyor ({DB_PATH})")
    except Exception as e:
        logger.error(f"DB init xatolik: {e}")
        raise


def get_doc(entity: str, doc_id: str) -> dict | None:
    """Hujjat bazada bormi? {doc_id, updated_at, msg_data, doc_info} qaytaradi."""
    sql = "SELECT doc_id, updated_at, msg_data, doc_info FROM admin_bot_sent WHERE entity = ? AND doc_id = ? LIMIT 1"
    try:
        with _tx() as conn:
            row = conn.execute(sql, (entity, doc_id)).fetchone()
            if row:
                msg_data = {}
                if row["msg_data"]:
                    try:
                        msg_data = json.loads(row["msg_data"])
                    except Exception:
                        pass
                doc_info = {}
                if row["doc_info"]:
                    try:
                        doc_info = json.loads(row["doc_info"])
                    except Exception:
                        pass
                return {
                    "doc_id":     row["doc_id"],
                    "updated_at": row["updated_at"],
                    "msg_data":   msg_data,
                    "doc_info":   doc_info,
                }
    except Exception as e:
        logger.error(f"get_doc xatolik: {e}")
    return None


def mark_deleted(entity: str, doc_id: str) -> None:
    """O'chirilgan hujjatni 'deleted' deb belgilaydi (bazadan o'chirmaymiz)."""
    sql = """
        INSERT INTO admin_bot_sent (entity, doc_id, updated_at, sent_at, msg_data, doc_info)
        VALUES (?, ?, 'deleted', ?, '{}', '{}')
        ON CONFLICT (entity, doc_id)
        DO UPDATE SET updated_at = 'deleted', sent_at = excluded.sent_at
    """
    try:
        with _tx() as conn:
            conn.execute(sql, (entity, doc_id, datetime.now(tz=timezone.utc).isoformat()))
    except Exception as e:
        logger.error(f"mark_deleted xatolik: {e}")
        raise


def mark_sent(entity: str, doc_id: str, updated_at: str, msg_data: dict, doc_info: dict) -> None:
    """Hujjatni bazaga yozadi yoki yangilaydi."""
    sql = """
        INSERT INTO admin_bot_sent (entity, doc_id, updated_at, sent_at, msg_data, doc_info)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (entity, doc_id)
        DO UPDATE SET
            updated_at = excluded.updated_at,
            sent_at    = excluded.sent_at,
            msg_data   = excluded.msg_data,
            doc_info   = excluded.doc_info
    """
    try:
        with _tx() as conn:
            conn.execute(sql, (
                entity, doc_id, updated_at,
                datetime.now(tz=timezone.utc).isoformat(),
                json.dumps(msg_data),
                json.dumps(doc_info),
            ))
    except Exception as e:
        logger.error(f"mark_sent xatolik: {e}")
        raise


def get_today_stats() -> dict:
    """Bugun (Toshkent vaqti bilan) yuborilgan xabarlar sonini qaytaradi."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tashkent")
    start_local = datetime.now(tz=tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc).isoformat()
    end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    sql = """
        SELECT entity, COUNT(*) as cnt
        FROM admin_bot_sent
        WHERE sent_at >= ? AND sent_at < ?
        GROUP BY entity
    """
    result = {"demand": 0, "paymentin": 0, "salesreturn": 0, "supply": 0}
    try:
        with _tx() as conn:
            for row in conn.execute(sql, (start_utc, end_utc)).fetchall():
                if row["entity"] in result:
                    result[row["entity"]] = row["cnt"]
    except Exception as e:
        logger.error(f"get_today_stats xatolik: {e}")
    return result
