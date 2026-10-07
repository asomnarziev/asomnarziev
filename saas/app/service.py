"""Biznes mantiq: qo'ng'iroqni yuborish, Telegram buyruqlari, to'lovni tasdiqlash."""
import logging
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from . import config, telegram
from .messages import DEFAULT_LANG, LANGS, TEMPLATES, audio_name, render_call
from .models import Account, CallLog, Chat, Payment, now
from .pbx import PbxClient
from .security import decrypt

log = logging.getLogger(__name__)

# Qo'ng'iroq tugagan zahoti yozuv fayli hali tayyor bo'lmasligi mumkin: shuncha soniya kutib qayta so'raymiz
RECORD_RETRY_DELAYS = (5, 15, 30, 60)


def fetch_record(pbx: PbxClient, uuid: str) -> bytes | None:
    audio = pbx.record(uuid)
    for delay in RECORD_RETRY_DELAYS:
        if audio:
            break
        time.sleep(delay)
        audio = pbx.record(uuid)
    return audio


def process_call(db: Session, account_id: int, uuid: str) -> None:
    """OnlinePBX webhook'dan keyin: ma'lumot + yozuvni olib, akkauntning barcha chatlariga yuboradi."""
    acc = db.get(Account, account_id)
    log_row = CallLog(account_id=acc.id, uuid=uuid)
    db.add(log_row)
    try:
        db.commit()
    except Exception:  # (account_id, uuid) takror — webhook qayta yuborilgan
        db.rollback()
        return
    if not acc.active:
        log_row.status, log_row.error = "skipped", "obuna tugagan"
        db.commit()
        return
    try:
        pbx = PbxClient(acc.pbx_domain, decrypt(acc.pbx_key_enc))
        call = pbx.call_info(uuid) or {"uuid": uuid}
        audio = fetch_record(pbx, uuid)
        for chat in acc.chats:
            lang = chat.lang if chat.lang in LANGS else DEFAULT_LANG
            caption = render_call(lang, call)
            if audio:
                telegram.send_audio(chat.chat_id, caption, audio, audio_name(call))
            else:
                telegram.send_message(chat.chat_id, caption + "\n\n" + TEMPLATES[lang]["no_record"])
        log_row.status = "sent"
    except Exception as e:
        log.exception("call %s failed", uuid)
        log_row.status, log_row.error = "error", str(e)[:500]
    db.commit()


def handle_update(db: Session, u: dict) -> None:
    """Telegramda faqat /start <kod> (chatni ulash). Chat tili kabinetdan o'zgartiriladi."""
    msg = u.get("message") or u.get("channel_post")
    if not (msg and msg.get("text", "").startswith("/")):
        return
    chat_id = str(msg["chat"]["id"])
    cmd, _, arg = msg["text"].partition(" ")
    if cmd.split("@")[0] != "/start" or not arg.strip():
        return
    acc = db.query(Account).filter_by(link_code=arg.strip()).first()
    if not acc:
        telegram.send_message(chat_id, TEMPLATES[DEFAULT_LANG]["bad_code"])
        return
    if not any(c.chat_id == chat_id for c in acc.chats):
        if len(acc.chats) >= acc.max_chats:
            telegram.send_message(chat_id, TEMPLATES[DEFAULT_LANG]["limit"])
            return
        db.add(Chat(account_id=acc.id, chat_id=chat_id, lang=DEFAULT_LANG))
        db.commit()
    telegram.send_message(chat_id, TEMPLATES[DEFAULT_LANG]["linked"])


def confirm_payment(db: Session, payment: Payment) -> None:
    """To'lov tasdiqlanganda obunani uzaytiradi (admin yoki Click/Payme callback chaqiradi)."""
    if payment.status == "paid":
        return
    acc = payment.account
    plan = config.PLANS[payment.plan]
    start = max(acc.paid_until or now(), now())
    acc.paid_until = start + timedelta(days=plan["days"])
    acc.plan = payment.plan
    payment.status = "paid"
    db.commit()
