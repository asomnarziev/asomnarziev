from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import config, service, telegram
from .db import get_db
from .messages import LANGS, TEMPLATES
from .models import Account, Chat, Payment, User, now
from .security import encrypt, hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def go(url: str):
    return RedirectResponse(url, status_code=303)


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("uid")
    return db.get(User, uid) if uid else None


def require_user(user: User | None = Depends(current_user)) -> User:
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403)
    return user


def render(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, {"config": config, "langs": LANGS, **ctx})


@router.get("/")
def index(user: User | None = Depends(current_user)):
    return go("/cabinet" if user else "/login")


@router.get("/register")
def register_form(request: Request):
    return render(request, "auth.html", mode="register", error=None)


@router.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if len(password) < 8:
        return render(request, "auth.html", mode="register", error="Parol kamida 8 belgi")
    if db.query(User).filter_by(email=email).first():
        return render(request, "auth.html", mode="register", error="Bu email band")
    user = User(email=email, password_hash=hash_password(password), is_admin=(email == config.ADMIN_EMAIL))
    user.account = Account()
    db.add(user)
    db.commit()
    request.session["uid"] = user.id
    return go("/cabinet")


@router.get("/login")
def login_form(request: Request):
    return render(request, "auth.html", mode="login", error=None)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        return render(request, "auth.html", mode="login", error="Email yoki parol noto'g'ri")
    request.session.clear()
    request.session["uid"] = user.id
    return go("/cabinet")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/login")


@router.get("/cabinet")
def cabinet(request: Request, user: User = Depends(require_user)):
    acc = user.account
    return render(request, "cabinet.html", user=user, acc=acc, now=now(),
                  hook_url=f"{config.BASE_URL}/hook/{acc.hook_token}",
                  tg_link=f"https://t.me/{config.BOT_USERNAME}?start={acc.link_code}")


@router.post("/cabinet/pbx")
def save_pbx(domain: str = Form(...), key: str = Form(""), user: User = Depends(require_user),
             db: Session = Depends(get_db)):
    user.account.pbx_domain = domain.strip()
    if key.strip():  # bo'sh qoldirilsa eski kalit saqlanadi
        user.account.pbx_key_enc = encrypt(key.strip())
    db.commit()
    return go("/cabinet")


@router.post("/cabinet/chat")
def add_chat(chat_id: str = Form(...), lang: str = Form("uz"), user: User = Depends(require_user),
             db: Session = Depends(get_db)):
    acc, chat_id = user.account, chat_id.strip()
    # shaxsiy/guruh/superguruh/kanal: 12345, -100123..., yoki ochiq kanal uchun @username
    valid = chat_id.lstrip("-").isdigit() or (chat_id.startswith("@") and len(chat_id) > 3)
    if not valid or lang not in LANGS:
        raise HTTPException(400, "Chat ID raqam (-100... kanal/guruh) yoki @kanal bo'lishi kerak")
    if len(acc.chats) >= acc.max_chats:
        raise HTTPException(400, "Chatlar limiti tugagan")
    if not any(c.chat_id == chat_id for c in acc.chats):
        try:  # bot shu chatga yoza olishini darrov tekshiramiz (kanalda bot admin bo'lishi kerak)
            telegram.send_message(chat_id, TEMPLATES[lang]["lang_set"])
        except Exception:
            raise HTTPException(400, "Bot bu chatga yoza olmadi. Shaxsiy chatda avval botga /start yuboring; "
                                     "guruh/kanalga botni (kanalda admin sifatida) qo'shing.")
        db.add(Chat(account_id=acc.id, chat_id=chat_id, lang=lang))
        db.commit()
    return go("/cabinet")


def _own_chat(db: Session, user: User, chat_pk: int) -> Chat:
    chat = db.get(Chat, chat_pk)
    if not chat or chat.account_id != user.account.id:
        raise HTTPException(404)
    return chat


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


@router.get("/billing")
def billing(request: Request, user: User = Depends(require_user)):
    return render(request, "billing.html", user=user, acc=user.account, plans=config.PLANS)


@router.post("/billing/checkout")
def checkout(plan: str = Form(...), user: User = Depends(require_user), db: Session = Depends(get_db)):
    if plan not in config.PLANS:
        raise HTTPException(400)
    p = Payment(account_id=user.account.id, plan=plan, amount=config.PLANS[plan]["price"], provider="manual")
    db.add(p)
    db.commit()
    return go("/billing")


@router.get("/admin")
def admin(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return render(request, "admin.html", user=user, now=now(),
                  accounts=db.query(Account).order_by(Account.id.desc()).all(),
                  payments=db.query(Payment).filter_by(status="pending").all())


@router.post("/admin/payment/{pid}/confirm")
def admin_confirm(pid: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(Payment, pid)
    if not p:
        raise HTTPException(404)
    service.confirm_payment(db, p)
    return go("/admin")


@router.post("/admin/account/{aid}/suspend")
def admin_suspend(aid: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    acc = db.get(Account, aid)
    if not acc:
        raise HTTPException(404)
    acc.suspended = not acc.suspended
    db.commit()
    return go("/admin")
