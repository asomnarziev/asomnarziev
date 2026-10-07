"""Administrator paneli: mijozlar, obunalar, to'lovlarni tasdiqlash."""
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from .. import config, service
from ..db import get_db
from ..models import Account, Payment, User, now
from ..security import hash_password
from .deps import go, render, require_admin

router = APIRouter()
MIN_PASSWORD = 8


def _page(request: Request, db: Session, user: User, error=None, created=None):
    return render(request, "admin.html", user=user, now=now(), error=error, created=created,
                  accounts=db.query(Account).order_by(Account.id.desc()).all(),
                  payments=db.query(Payment).filter_by(status="pending").all(),
                  history=db.query(Payment).filter(Payment.status == "paid").order_by(Payment.id.desc()).limit(20).all(),
                  plans=config.PLANS, amount=service.payment_amount)


def _account(db: Session, account_id: int) -> Account:
    acc = db.get(Account, account_id)
    if not acc:
        raise HTTPException(404)
    return acc


def _payment(db: Session, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404)
    return payment


@router.get("/admin")
def admin(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _page(request, db, user)


@router.post("/admin/user")
def create_customer(request: Request, email: str = Form(...), password: str = Form(...),
                    admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    email = email.strip().lower()
    error = None
    if len(password) < MIN_PASSWORD:
        error = f"Parol kamida {MIN_PASSWORD} belgi"
    elif not email or "@" not in email:
        error = "Email noto'g'ri"
    elif db.query(User).filter_by(email=email).first():
        error = "Bu email band"
    if error:
        return _page(request, db, admin, error=error)
    user = User(email=email, password_hash=hash_password(password))
    user.account = Account()
    db.add(user)
    db.commit()
    return _page(request, db, admin, created=(email, password))


@router.post("/admin/account/{account_id}/subscription")
def manage_subscription(account_id: int, action: str = Form(...), plan: str = Form("start"), days: int = Form(30),
                        _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Obunani qo'lda boshqarish: uzaytirish (tarif bilan) yoki darrov tugatish. Uzaytirish Payment (provider=admin) bilan yoziladi."""
    acc = _account(db, account_id)
    if action == "extend":
        if plan not in config.PLANS or not 1 <= days <= 3650:
            raise HTTPException(400, "Tarif noto'g'ri yoki kunlar 1..3650 oralig'ida emas")
        acc.paid_until = max(acc.paid_until or now(), now()) + timedelta(days=days)
        acc.plan = plan
        db.add(Payment(account_id=acc.id, plan=plan, amount=0, provider="admin", status="paid"))
    elif action == "expire":
        acc.paid_until = acc.trial_ends = now()
    else:
        raise HTTPException(400)
    db.commit()
    return go("/admin")


@router.post("/admin/account/{account_id}/password")
def set_password(account_id: int, password: str = Form(...), _: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    acc = _account(db, account_id)
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Parol {MIN_PASSWORD} belgidan qisqa")
    acc.user.password_hash = hash_password(password)
    db.commit()
    return go("/admin")


@router.post("/admin/account/{account_id}/suspend")
def toggle_suspend(account_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    acc = _account(db, account_id)
    acc.suspended = not acc.suspended
    db.commit()
    return go("/admin")


@router.post("/admin/payment/{payment_id}/confirm")
def confirm_payment(payment_id: int, bg: BackgroundTasks, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    payment = _payment(db, payment_id)
    was_pending = payment.status == "pending"
    service.confirm_payment(db, payment)
    if was_pending:
        bg.add_task(service.notify_customer, payment.account_id, True)
    return go("/admin")


@router.post("/admin/payment/{payment_id}/reject")
def reject_payment(payment_id: int, bg: BackgroundTasks, note: str = Form(""), _: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    payment = _payment(db, payment_id)
    was_pending = payment.status == "pending"
    service.reject_payment(db, payment, note)
    if was_pending:
        bg.add_task(service.notify_customer, payment.account_id, False)
    return go("/admin")
