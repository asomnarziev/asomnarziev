"""Tarif va kartaga o'tkazma orqali to'lov: chek yuklash, to'lovlar tarixi, chekni ko'rish."""
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import config, receipts, service
from ..db import get_db
from ..models import Payment, User
from .deps import go, render, require_user

router = APIRouter()
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
    acc, comment = user.account, comment.strip()[:500]
    if plan not in config.PLANS or months not in MONTHS:
        raise HTTPException(400)
    if db.query(Payment).filter_by(account_id=acc.id, status="pending").count() >= config.MAX_PENDING_PAYMENTS:
        return go("/billing", error="Kutilayotgan to'lovlaringiz ko'p. Administrator tasdiqlashini kuting")
    limit = config.MAX_RECEIPT_MB * 1024 * 1024 + 1
    data = await receipt.read(limit) if receipt is not None and receipt.filename else b""
    if not data and not comment:
        return go("/billing", error="Chekni yuklang yoki izohga to'lov haqida yozing (qaysi kartadan, kim, qachon o'tkazgansiz)")
    name = ""
    if data:
        try:
            name = receipts.save(data)
        except receipts.ReceiptError as e:
            return go("/billing", error=str(e))
    payment = Payment(account_id=acc.id, plan=plan, months=months, amount=config.PLANS[plan]["price"] * months,
                      provider="card", receipt=name, comment=comment)
    db.add(payment)
    db.commit()
    bg.add_task(service.notify_admin_payment, payment.id)
    return go("/billing", ok="1")


@router.get("/receipt/{pid}")
def receipt_file(pid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Chekni faqat egasi va administrator ko'radi. Fayl hech qachon sahifa sifatida bajarilmaydi."""
    payment = db.get(Payment, pid)
    if not payment or not payment.receipt or not (user.is_admin or payment.account_id == user.account.id):
        raise HTTPException(404)
    try:
        path = receipts.path(payment.receipt)
    except FileNotFoundError:
        raise HTTPException(404) from None
    return FileResponse(path, media_type=receipts.mime(payment.receipt), headers={
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "inline", "Cache-Control": "private, no-store"})
