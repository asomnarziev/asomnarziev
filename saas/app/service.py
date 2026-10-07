"""Biznes mantiq: qo'ng'iroqni yuborish, Telegram buyruqlari, to'lovni tasdiqlash."""
import logging
import time
from datetime import timedelta

from html import escape

from sqlalchemy.orm import Session

from . import config, receipts, telegram
from .messages import DEFAULT_LANG, LANGS, TEMPLATES, audio_name, render_call
from .db import SessionLocal
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
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    today = db.query(CallLog).filter(CallLog.account_id == acc.id, CallLog.created_at >= day_start).count()
    if acc.active and today > config.MAX_CALLS_PER_DAY:
        log_row.status, log_row.error = "skipped", f"kunlik limit ({config.MAX_CALLS_PER_DAY}) oshdi"
        db.commit()
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
    """Telegramda: /start <kod> (chatni ulash), /id (chat ID), admin uchun to'lov tugmalari. Til kabinetdan o'zgartiriladi."""
    if u.get("callback_query"):
        return handle_payment_callback(db, u["callback_query"])
    msg = u.get("message") or u.get("channel_post")
    if not (msg and msg.get("text", "").startswith("/")):
        return
    chat_id = str(msg["chat"]["id"])
    cmd, _, arg = msg["text"].partition(" ")
    if cmd.split("@")[0] == "/id":
        telegram.send_message(chat_id, f"Chat ID: <code>{chat_id}</code>")
        return
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
    acc.paid_until = start + timedelta(days=plan["days"] * (payment.months or 1))
    acc.plan = payment.plan
    payment.status = "paid"
    db.commit()


# ---------- Kartaga o'tkazma to'lovlari ----------
def payment_amount(p: Payment) -> int:
    return config.PLANS[p.plan]["price"] * p.months


def reject_payment(db: Session, payment: Payment, note: str = "") -> None:
    if payment.status != "pending":
        return
    payment.status, payment.note = "rejected", note.strip()[:300]
    db.commit()


def notify_customer(account_id: int, ok: bool) -> None:
    """Mijozning ulangan chatlariga to'lov natijasi haqida (har chat o'z tilida) yozadi. Xato bo'lsa e'tiborsiz."""
    with SessionLocal() as db:
        acc = db.get(Account, account_id)
        until = acc.valid_until.strftime("%d.%m.%Y")
        for chat in acc.chats:
            lang = chat.lang if chat.lang in LANGS else DEFAULT_LANG
            try:
                telegram.send_message(chat.chat_id, TEMPLATES[lang]["pay_ok" if ok else "pay_no"].format(until=until))
            except Exception:
                log.warning("customer notify failed chat=%s", chat.chat_id)


def notify_admin_payment(payment_id: int) -> None:
    """Adminga (ADMIN_CHAT_ID) yangi to'lov: chek rasmi + Tasdiqlash/Rad etish tugmalari."""
    if not config.ADMIN_CHAT_ID:
        return
    with SessionLocal() as db:
        p = db.get(Payment, payment_id)
        if not p or p.status != "pending":
            return
        plan = config.PLANS[p.plan]["name"]
        amount = f"{payment_amount(p):,}".replace(",", " ")
        caption = (f"💳 <b>Yangi to'lov #{p.id}</b>\n"
                   f"Mijoz: {escape(p.account.user.email)}\n"
                   f"Tarif: {escape(plan)} × {p.months} oy\n"
                   f"Summa: <b>{amount}</b> so'm")
        markup = {"inline_keyboard": [[{"text": "✅ Tasdiqlash", "callback_data": f"pay:ok:{p.id}"},
                                       {"text": "❌ Rad etish", "callback_data": f"pay:no:{p.id}"}]]}
        try:
            data = receipts.path(p.receipt).read_bytes()
            telegram.send_file(config.ADMIN_CHAT_ID, caption, data, p.receipt, receipts.mime(p.receipt), markup)
        except Exception:
            log.exception("admin payment notify failed #%s", payment_id)
            try:  # chekni yuborib bo'lmasa ham eslatma ketsin
                telegram.send_message(config.ADMIN_CHAT_ID, caption + "\n(chekni saytda ko'ring: /admin)", markup)
            except Exception:
                log.exception("admin text notify failed #%s", payment_id)


def handle_payment_callback(db: Session, cb: dict) -> None:
    """Admin Telegramdagi ✅/❌ tugmani bosdi. Faqat ADMIN_CHAT_ID chatidan kelgan bosish qabul qilinadi."""
    data = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if not data.startswith("pay:") or not config.ADMIN_CHAT_ID or chat_id != config.ADMIN_CHAT_ID:
        return
    try:
        _, action, pid = data.split(":")
        p = db.get(Payment, int(pid))
    except ValueError:
        return
    if not p or p.status != "pending":
        telegram.answer_callback(cb["id"], "Bu to'lov allaqachon ko'rib chiqilgan")
        return
    if action == "ok":
        confirm_payment(db, p)
        result = "✅ Tasdiqlandi"
    else:
        reject_payment(db, p, "Administrator rad etdi")
        result = "❌ Rad etildi"
    telegram.answer_callback(cb["id"], result)
    try:
        telegram.clear_buttons(chat_id, msg["message_id"])
        telegram.send_message(chat_id, f"{result}: to'lov #{p.id} ({escape(p.account.user.email)})")
    except Exception:
        log.warning("clear buttons failed #%s", p.id)
    notify_customer(p.account_id, action == "ok")
