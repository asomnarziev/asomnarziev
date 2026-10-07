"""Mijoz kabineti: OnlinePBX ulash, webhook, Telegram chatlar, Pro sozlamalari (kunlik hisobot, ogohlantirish)."""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from .. import config, reports, scheduler, telegram
from ..db import get_db
from ..messages import LANGS, TEMPLATES
from ..models import CallLog, Chat, User, now, token
from ..security import encrypt
from .deps import go, render, reports_allowed, require_user

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/cabinet")
def cabinet(
    request: Request,
    error: str | None = None,
    ok: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    acc = user.account
    # faqat oxirgi 20 ta webhook hodisasi ("imported" kirmaydi); butun to'plam xotiraga yuklanmaydi
    logs = (
        db.query(CallLog)
        .filter(CallLog.account_id == acc.id, CallLog.status != "imported")
        .order_by(CallLog.id.desc())
        .limit(20)
        .all()
    )
    return render(
        request,
        "cabinet.html",
        user=user,
        acc=acc,
        now=now(),
        error=error,
        ok=ok,
        logs=logs,
        can_digest=reports_allowed(user),
        hook_url=f"{config.BASE_URL}/hook/{acc.hook_token}",
        tg_link=f"https://t.me/{config.BOT_USERNAME}?start={acc.link_code}",
    )


@router.post("/cabinet/pbx")
def save_pbx(domain: str = Form(...), key: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db)):
    user.account.pbx_domain = domain.strip()
    if key.strip():  # bo'sh qoldirilsa eski kalit saqlanadi
        user.account.pbx_key_enc = encrypt(key.strip())
    db.commit()
    return go("/cabinet")


@router.post("/cabinet/hook/regenerate")
def regenerate_hook(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Webhook manzili oshkor bo'lsa — yangisini yaratadi (eskisi darrov ishlamay qoladi)."""
    user.account.hook_token = token()
    db.commit()
    return go("/cabinet")


@router.post("/cabinet/test")
def test_message(user: User = Depends(require_user)):
    for chat in user.account.chats:
        try:
            telegram.send_message(chat.chat_id, TEMPLATES[chat.lang if chat.lang in LANGS else "uz"]["lang_set"])
        except Exception:
            log.warning("test message failed chat=%s", chat.chat_id)
    return go("/cabinet")


# ---------- Telegram chatlar ----------
def _own_chat(db: Session, user: User, chat_pk: int) -> Chat:
    chat = db.get(Chat, chat_pk)
    if not chat or chat.account_id != user.account.id:
        raise HTTPException(404)
    return chat


@router.post("/cabinet/chat")
def add_chat(chat_id: str = Form(...), lang: str = Form("uz"), user: User = Depends(require_user), db: Session = Depends(get_db)):
    def back(msg: str):  # xatoni xom JSON o'rniga kabinet sahifasida ko'rsatamiz
        return go("/cabinet", error="Chat qo'shilmadi. " + msg)

    acc, chat_id = user.account, chat_id.strip()
    # shaxsiy/guruh/superguruh/kanal: 12345, -100123..., yoki ochiq kanal uchun @username
    valid = chat_id.lstrip("-").isdigit() or (chat_id.startswith("@") and len(chat_id) > 3)
    if not valid or lang not in LANGS:
        return back("Chat ID raqam (-100... kanal/guruh) yoki @kanal bo'lishi kerak. Telefon raqam yoki ism ishlamaydi.")
    if len(acc.chats) >= acc.max_chats:
        return back("Chatlar limiti tugagan. Tarifni yangilang.")
    if not any(c.chat_id == chat_id for c in acc.chats):
        try:  # bot shu chatga yoza olishini darrov tekshiramiz (kanalda bot admin bo'lishi kerak)
            telegram.send_message(chat_id, TEMPLATES[lang]["lang_set"])
        except Exception as e:
            return back(telegram.explain(e))
        db.add(Chat(account_id=acc.id, chat_id=chat_id, lang=lang))
        db.commit()
    return go("/cabinet")


@router.post("/cabinet/chat/{chat_pk}/lang")
def chat_lang(chat_pk: int, lang: str = Form(...), user: User = Depends(require_user), db: Session = Depends(get_db)):
    if lang not in LANGS:
        raise HTTPException(400)
    _own_chat(db, user, chat_pk).lang = lang
    db.commit()
    return go("/cabinet")


@router.post("/cabinet/chat/{chat_pk}/delete")
def chat_delete(chat_pk: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    db.delete(_own_chat(db, user, chat_pk))
    db.commit()
    return go("/cabinet")


# ---------- Pro sozlamalari ----------
@router.post("/cabinet/digest")
def save_digest(
    on: str | None = Form(None), hour: int = Form(9), user: User = Depends(require_user), db: Session = Depends(get_db)
):
    """Kunlik hisobot sozlamasi (Pro): yoqish/o'chirish va yuborish soati (Toshkent vaqti)."""
    if not reports_allowed(user):
        raise HTTPException(403, "Kunlik hisobot Pro tarifda")
    if not 0 <= hour <= 23:
        raise HTTPException(400)
    user.account.digest_on, user.account.digest_hour = on is not None, hour
    db.commit()
    return go("/cabinet", ok="digest")


@router.post("/cabinet/digest/test")
def test_digest(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Bugungi (hozirgacha) hisobotni darrov chatlarga yuboradi: sozlamani tekshirish uchun."""
    if not reports_allowed(user):
        raise HTTPException(403, "Kunlik hisobot Pro tarifda")
    if not user.account.chats:
        return go("/cabinet", error="Avval Telegram chat ulang")
    sent = scheduler.send_digest(db, user.account, reports.today_local(), test=True)
    if sent:
        return go("/cabinet", ok="digest_test")
    return go("/cabinet", error="Hisobotni yuborib bo'lmadi: chatlarni tekshiring")


@router.post("/cabinet/missed-alerts")
def save_missed_alerts(on: str | None = Form(None), user: User = Depends(require_user), db: Session = Depends(get_db)):
    if not (user.is_admin or user.account.can("missed_alerts")):
        raise HTTPException(403, "Javobsiz qo'ng'iroq ogohlantirishi Pro tarifda")
    user.account.missed_on = on is not None
    db.commit()
    return go("/cabinet", ok="missed")
