from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import config, receipts, service, telegram
from .db import get_db
from .messages import LANGS, TEMPLATES
from .models import Account, Chat, Payment, User, now, token
from .security import encrypt, hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["card"] = lambda n: " ".join(str(n)[i:i + 4] for i in range(0, len(str(n)), 4))  # 8600123412341234 -> 8600 1234 1234 1234


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


@router.post("/cabinet/hook/regenerate")
def regenerate_hook(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Webhook manzili oshkor bo'lsa — yangisini yaratadi (eskisi darrov ishlamay qoladi)."""
    user.account.hook_token = token()
    db.commit()
    return go("/cabinet")


@router.post("/cabinet/test")
def test_message(user: User = Depends(require_user)):
    for c in user.account.chats:
        try:
            telegram.send_message(c.chat_id, TEMPLATES[c.lang if c.lang in LANGS else "uz"]["lang_set"])
        except Exception:
            pass
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


MONTHS = (1, 3, 6, 12)


@router.get("/billing")
def billing(request: Request, error: str | None = None, ok: str | None = None, user: User = Depends(require_user),
            db: Session = Depends(get_db)):
    acc = user.account
    history = db.query(Payment).filter(Payment.account_id == acc.id).order_by(Payment.id.desc()).limit(10).all()
    return render(request, "billing.html", user=user, acc=acc, plans=config.PLANS, months=MONTHS, history=history,
                  error=error, ok=ok, amount=service.payment_amount, max_mb=config.MAX_RECEIPT_MB)


@router.post("/billing/checkout")
async def checkout(bg: BackgroundTasks, plan: str = Form(...), months: int = Form(1), comment: str = Form(""),
                   receipt: UploadFile | None = File(None), user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    """Mijoz kartaga o'tkazgach chekni yuklaydi. Chek bo'lmasa izoh majburiy. To'lov 'pending', adminga Telegramda eslatma."""
    from urllib.parse import quote_plus

    def back(error):
        return go(f"/billing?error={quote_plus(error)}")

    acc, comment = user.account, comment.strip()[:500]
    if plan not in config.PLANS or months not in MONTHS:
        raise HTTPException(400)
    if db.query(Payment).filter_by(account_id=acc.id, status="pending").count() >= config.MAX_PENDING_PAYMENTS:
        return back("Kutilayotgan to'lovlaringiz ko'p. Administrator tasdiqlashini kuting")
    data = await receipt.read(config.MAX_RECEIPT_MB * 1024 * 1024 + 1) if receipt is not None and receipt.filename else b""
    if not data and not comment:
        return back("Chekni yuklang yoki izohga to'lov haqida yozing (qaysi kartadan, kim, qachon o'tkazgansiz)")
    name = ""
    if data:
        try:
            name = receipts.save(data)
        except receipts.ReceiptError as e:
            return back(str(e))
    p = Payment(account_id=acc.id, plan=plan, months=months, amount=config.PLANS[plan]["price"] * months,
                provider="card", receipt=name, comment=comment)
    db.add(p)
    db.commit()
    bg.add_task(service.notify_admin_payment, p.id)
    return go("/billing?ok=1")


@router.get("/receipt/{pid}")
def receipt_file(pid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    p = db.get(Payment, pid)
    if not p or not p.receipt or not (user.is_admin or p.account_id == user.account.id):
        raise HTTPException(404)
    try:
        path = receipts.path(p.receipt)
    except FileNotFoundError:
        raise HTTPException(404)
    # Yuklangan fayl hech qachon sahifa sifatida bajarilmasin
    return FileResponse(path, media_type=receipts.mime(p.receipt), headers={
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "inline", "Cache-Control": "private, no-store"})


def admin_page(request: Request, db: Session, user: User, error=None, created=None):
    return render(request, "admin.html", user=user, now=now(), error=error, created=created,
                  accounts=db.query(Account).order_by(Account.id.desc()).all(),
                  payments=db.query(Payment).filter_by(status="pending").all(),
                  history=db.query(Payment).filter(Payment.status == "paid").order_by(Payment.id.desc()).limit(20).all(),
                  plans=config.PLANS, amount=service.payment_amount)


@router.get("/admin")
def admin(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return admin_page(request, db, user)


@router.post("/admin/user")
def admin_create_user(request: Request, email: str = Form(...), password: str = Form(...),
                      admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    email = email.strip().lower()
    err = None
    if len(password) < 8:
        err = "Parol kamida 8 belgi"
    elif not email or "@" not in email:
        err = "Email noto'g'ri"
    elif db.query(User).filter_by(email=email).first():
        err = "Bu email band"
    if err:
        return admin_page(request, db, admin, error=err)
    user = User(email=email, password_hash=hash_password(password))
    user.account = Account()
    db.add(user)
    db.commit()
    return admin_page(request, db, admin, created=(email, password))


@router.post("/admin/account/{aid}/subscription")
def admin_subscription(aid: int, action: str = Form(...), plan: str = Form("start"), days: int = Form(30),
                       _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Obunani qo'lda boshqarish: uzaytirish (tarif bilan) yoki darrov tugatish. Har amal Payment (provider=admin) bilan yoziladi."""
    acc = db.get(Account, aid)
    if not acc:
        raise HTTPException(404)
    if action == "extend":
        if plan not in config.PLANS or not 1 <= days <= 3650:
            raise HTTPException(400, "Tarif noto'g'ri yoki kunlar 1..3650 oralig'ida emas")
        acc.paid_until = max(acc.paid_until or now(), now()) + timedelta(days=days)
        acc.plan = plan
        db.add(Payment(account_id=acc.id, plan=plan, amount=0, provider="admin", status="paid"))
    elif action == "expire":
        acc.paid_until = now()
        acc.trial_ends = now()
    else:
        raise HTTPException(400)
    db.commit()
    return go("/admin")


@router.post("/admin/account/{aid}/password")
def admin_set_password(aid: int, password: str = Form(...), _: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    acc = db.get(Account, aid)
    if not acc or len(password) < 8:
        raise HTTPException(400, "Akkaunt topilmadi yoki parol 8 belgidan qisqa")
    acc.user.password_hash = hash_password(password)
    db.commit()
    return go("/admin")


@router.post("/admin/payment/{pid}/confirm")
def admin_confirm(pid: int, bg: BackgroundTasks, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(Payment, pid)
    if not p:
        raise HTTPException(404)
    was_pending = p.status == "pending"
    service.confirm_payment(db, p)
    if was_pending:
        bg.add_task(service.notify_customer, p.account_id, True)
    return go("/admin")


@router.post("/admin/payment/{pid}/reject")
def admin_reject(pid: int, bg: BackgroundTasks, note: str = Form(""), _: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    p = db.get(Payment, pid)
    if not p:
        raise HTTPException(404)
    was_pending = p.status == "pending"
    service.reject_payment(db, p, note)
    if was_pending:
        bg.add_task(service.notify_customer, p.account_id, False)
    return go("/admin")


@router.post("/admin/account/{aid}/suspend")
def admin_suspend(aid: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    acc = db.get(Account, aid)
    if not acc:
        raise HTTPException(404)
    acc.suspended = not acc.suspended
    db.commit()
    return go("/admin")
