"""Kirish va chiqish."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..ratelimit import login_throttle
from ..security import hash_password, verify_password
from .deps import current_user, go, render

router = APIRouter()
_DUMMY_HASH = hash_password("dummy-password")  # mavjud bo'lmagan email uchun ham parol tekshiruvi vaqti bir xil bo'lsin


@router.get("/")
def index(user: User | None = Depends(current_user)):
    return go("/cabinet" if user else "/login")


@router.get("/login")
def login_form(request: Request):
    return render(request, "auth.html", mode="login", error=None)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    email = email.strip().lower()
    ip = request.client.host if request.client else "?"
    wait = login_throttle.blocked(ip, email)
    if wait:
        return render(request, "auth.html", status_code=429, mode="login",
                      error=f"Juda ko'p urinish. {wait // 60 + 1} daqiqadan keyin qayta urinib ko'ring")
    user = db.query(User).filter_by(email=email).first()
    if not verify_password(password, user.password_hash if user else _DUMMY_HASH) or not user:
        login_throttle.fail(ip, email)
        return render(request, "auth.html", status_code=401, mode="login", error="Email yoki parol noto'g'ri")
    login_throttle.reset(email)
    request.session.clear()  # sessiya fiksatsiyasidan himoya
    request.session["uid"] = user.id
    return go("/cabinet")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/login")
