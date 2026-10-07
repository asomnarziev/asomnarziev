"""Fon vazifalari: Pro mijozlarga kunlik hisobotni Telegramga yuborish.

Bitta jarayonda ishlaydi (bitta uvicorn worker). Takror yuborilmaydi: har akkauntda oxirgi yuborilgan kun saqlanadi
(`digest_last`), shuning uchun server qayta ishga tushsa ham xabar ikki marta ketmaydi.
"""
import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from . import reports, telegram
from .db import SessionLocal
from .messages import DEFAULT_LANG, LANGS, LOCAL_TZ
from .models import Account

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3          # Telegram xatosida bir kun uchun urinishlar soni, keyin kun "yuborilgan" deb belgilanadi
_attempts: dict[tuple, int] = {}


def send_digest(db: Session, acc: Account, day, test: bool = False) -> int:
    """`day` kuni bo'yicha hisobotni akkauntning barcha chatlariga (har biri o'z tilida) yuboradi. Muvaffaqiyatli yuborilganlar soni."""
    data = reports.build(reports.load(db, acc.id, day, day), day, day)
    sent = 0
    for chat in acc.chats:
        lang = chat.lang if chat.lang in LANGS else DEFAULT_LANG
        try:
            telegram.send_message(chat.chat_id, reports.render_digest(lang, day, data, test=test))
            sent += 1
        except Exception:
            log.exception("digest failed acc=%s chat=%s", acc.id, chat.chat_id)
    return sent


def run_due(db: Session, now_utc: datetime | None = None) -> int:
    """Vaqti kelgan akkauntlarga kechagi hisobotni yuboradi. Yuborilgan (yoki o'tkazib yuborilgan) akkauntlar soni."""
    now_utc = now_utc or datetime.now(UTC)
    local_now = now_utc.astimezone(LOCAL_TZ)
    yesterday = local_now.date() - timedelta(days=1)
    done = 0
    for acc in db.query(Account).filter(Account.digest_on.is_(True)).all():
        if acc.digest_last == yesterday.isoformat() or local_now.hour < acc.digest_hour:
            continue
        if not acc.can("reports") or not acc.chats:
            continue
        key = (acc.id, yesterday.isoformat())
        has_calls = bool(reports.load(db, acc.id, yesterday, yesterday, limit=1))
        ok = send_digest(db, acc, yesterday) if has_calls else 1  # qo'ng'iroq bo'lmagan kunda xabar yuborilmaydi
        _attempts[key] = _attempts.get(key, 0) + 1
        if ok or _attempts[key] >= MAX_ATTEMPTS:
            acc.digest_last = yesterday.isoformat()
            db.commit()
            _attempts.pop(key, None)
            done += 1
    return done


def _loop(stop: threading.Event) -> None:
    stop.wait(5)
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                run_due(db)
        except Exception:
            log.exception("scheduler tick failed")
        stop.wait(60)


def start() -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    t = threading.Thread(target=_loop, args=(stop,), name="digest-scheduler", daemon=True)
    t.start()
    return t, stop
