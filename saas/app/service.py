"""Biznes mantiq: qo'ng'iroqni yuborish, Telegram buyruqlari, to'lovni tasdiqlash."""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from html import escape as _escape

from sqlalchemy.orm import Session

from . import config, live, receipts, telegram
from .db import SessionLocal
from .messages import DEFAULT_LANG, LANGS, LOCAL_TZ, TEMPLATES, audio_name, render_call, render_missed
from .models import Account, CallLog, Chat, Payment, now
from .pbx import PbxClient, PbxError
from .security import decrypt

log = logging.getLogger(__name__)


def escape(text) -> str:
    """Telegram HTML uchun: faqat < > & tozalanadi (' va \" ga tegilmaydi)."""
    return _escape(str(text), quote=False)


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


def store_call_stats(row: CallLog, call: dict) -> None:
    """OnlinePBX qo'ng'iroq ma'lumotini hisobotlar uchun CallLog qatoriga yozadi."""

    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    row.direction = str(call.get("accountcode") or "")[:10]
    row.caller = str(call.get("caller_id_number") or "")[:40]
    row.callee = str(call.get("destination_number") or "")[:40]
    ts = num(call.get("start_stamp"))
    row.started_at = datetime.fromtimestamp(ts, UTC).replace(tzinfo=None) if ts else now()
    row.duration = num(call.get("duration")) or 0
    row.talk = num(call.get("user_talk_time"))


def is_missed_inbound(call: dict, row: CallLog) -> bool:
    """Kiruvchi va javob berilmagan qo'ng'iroq. OnlinePBX ma'lumoti bo'sh kelsa (noma'lum) ogohlantirilmaydi."""
    if not (call.get("caller_id_number") and call.get("start_stamp")) or row.direction != "inbound":
        return False
    return not ((row.talk if row.talk is not None else row.duration) or 0) > 0


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
    today = (
        db.query(CallLog)
        .filter(CallLog.account_id == acc.id, CallLog.created_at >= day_start, CallLog.status != "imported")
        .count()
    )
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
        store_call_stats(log_row, call)
        db.commit()  # hisobot ma'lumoti Telegram yuborilmasa ham saqlanadi
        live.publish(acc.id, {"id": log_row.id, "direction": log_row.direction})  # ochiq sahifalar yangilansin
        if acc.missed_on and acc.can("missed_alerts") and is_missed_inbound(call, log_row):
            # Pro: javobsiz kiruvchi qo'ng'iroq -> darrov ogohlantirish; yozuv yo'q, shuning uchun API'dan so'ramaymiz
            for chat in acc.chats:
                lang = chat.lang if chat.lang in LANGS else DEFAULT_LANG
                telegram.send_message(chat.chat_id, render_missed(lang, call))
            log_row.status = "sent"
            db.commit()
            return
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
        handle_payment_callback(db, u["callback_query"])
        return
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
        caption = (
            f"💳 <b>Yangi to'lov #{p.id}</b>\n"
            f"Mijoz: {escape(p.account.user.email)}\n"
            f"Tarif: {escape(plan)} × {p.months} oy\n"
            f"Summa: <b>{amount}</b> so'm"
        )
        caption += f"\nIzoh: {escape(p.comment)}" if p.comment else ""
        if not p.receipt:
            caption += "\n⚠️ Chek yuklanmagan"
        markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Tasdiqlash", "callback_data": f"pay:ok:{p.id}"},
                    {"text": "❌ Rad etish", "callback_data": f"pay:no:{p.id}"},
                ]
            ]
        }
        try:
            if not p.receipt:  # chek yo'q: matnli eslatma (izoh bilan) va o'sha tugmalar
                telegram.send_message(config.ADMIN_CHAT_ID, caption, markup)
                return
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


# ---------- Eski qo'ng'iroqlarni OnlinePBX tarixidan yuklash (faqat ma'lumot; yozuv fayllari saqlanmaydi) ----------
IMPORT_DAYS = 30
IMPORT_SPLIT_AT = 500  # oynada shuncha va undan ko'p qator qaytsa, API javobni qirqqan bo'lishi mumkin: oynani ikkiga bo'lamiz
IMPORT_MIN_WINDOW = 900  # soniya (15 daqiqa): bundan kichik oynalar bo'linmaydi
IMPORT_MAX_ROWS = 20_000  # bir yuklashda ko'pi bilan (suiiste'moldan himoya)
IMPORT_COOLDOWN = 600  # soniya: muvaffaqiyatli yuklashdan keyin qayta yuklashga tanaffus
_importing: set[int] = set()
_import_lock = threading.Lock()


def is_importing(account_id: int) -> bool:
    with _import_lock:
        return account_id in _importing


def _fetch_window(pbx, start: int, end: int, out: list) -> None:
    rows = pbx.history(start, end)
    if len(rows) >= IMPORT_SPLIT_AT and end - start > IMPORT_MIN_WINDOW:
        mid = (start + end) // 2
        _fetch_window(pbx, start, mid, out)
        _fetch_window(pbx, mid, end, out)
    else:
        out.extend(rows)


def _store_history(db: Session, acc: Account, calls: dict[str, dict]) -> int:
    uuids = list(calls)
    existing: set[str] = set()
    for i in range(0, len(uuids), 500):
        chunk = uuids[i : i + 500]
        existing |= {u for (u,) in db.query(CallLog.uuid).filter(CallLog.account_id == acc.id, CallLog.uuid.in_(chunk))}
    added = 0
    for u in uuids:
        call = calls[u]
        if u in existing or not call.get("start_stamp"):
            continue
        row = CallLog(account_id=acc.id, uuid=u[:100], status="imported")
        store_call_stats(row, call)
        row.created_at = row.started_at  # "bugun yaratilgan" hisoblanmasin (kunlik limit)
        db.add(row)
        added += 1
        if added % 500 == 0:
            db.commit()
    try:
        db.commit()
    except Exception:  # webhook bilan bir vaqtda kelgan takror uuid: qatorma-qator qo'shamiz
        db.rollback()
        added = 0
        for u in uuids:
            call = calls[u]
            if not call.get("start_stamp"):
                continue
            row = CallLog(account_id=acc.id, uuid=u[:100], status="imported")
            store_call_stats(row, call)
            row.created_at = row.started_at
            try:
                with db.begin_nested():
                    db.add(row)
                added += 1
            except Exception:  # takror uuid: shu qatorni o'tkazib yuboramiz
                log.debug("history import: duplicate %s skipped", u)
        db.commit()
    return added


def import_history(account_id: int, days: int = IMPORT_DAYS) -> None:
    """Oxirgi `days` kun qo'ng'iroqlarini OnlinePBX tarixidan CallLog'ga ('imported') qo'shadi. Takror ishga tushirish xavfsiz."""
    with _import_lock:
        if account_id in _importing:
            return
        _importing.add(account_id)
    try:
        with SessionLocal() as db:
            acc = db.get(Account, account_id)
            ok, note = False, ""
            try:
                pbx = PbxClient(acc.pbx_domain, decrypt(acc.pbx_key_enc))
                first_day = datetime.now(LOCAL_TZ).date() - timedelta(days=days - 1)
                cursor = int(datetime.combine(first_day, dtime(0), LOCAL_TZ).timestamp())
                end = int(time.time()) + 1
                calls: dict[str, dict] = {}
                while cursor < end:  # kunma-kun: so'rovlar API tezlik chegarasi (PBX_RPS_*) bilan sekin yuboriladi
                    nxt = min(cursor + 86400, end)
                    rows: list = []
                    _fetch_window(pbx, cursor, nxt, rows)
                    for r in rows:
                        if r.get("uuid"):
                            calls[str(r["uuid"])] = r
                    if len(calls) > IMPORT_MAX_ROWS:
                        raise PbxError(f"Qo'ng'iroqlar juda ko'p (>{IMPORT_MAX_ROWS}): davrni qisqartiring")
                    cursor = nxt
                note, ok = f"{_store_history(db, acc, calls)} ta qo'ng'iroq qo'shildi (jami topildi: {len(calls)})", True
            except Exception as e:
                log.exception("history import failed acc=%s", account_id)
                db.rollback()
                note = str(e)[:190] or "Noma'lum xato"
            acc = db.get(Account, account_id)
            acc.import_note = note
            if ok:
                acc.import_at = now()
            db.commit()
            live.publish(account_id, {"id": 0, "import": True})  # ochiq hisobot/qidiruv sahifalari yangilansin
    finally:
        with _import_lock:
            _importing.discard(account_id)
