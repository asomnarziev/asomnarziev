"""Veb-qatlam uchun umumiy yordamchilar: shablonlar, redirect (flash), autentifikatsiya va ruxsatlar."""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..messages import LANGS
from ..models import User

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def card_format(number) -> str:
    """8600123412341234 -> 8600 1234 1234 1234"""
    digits = str(number)
    return " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))


templates.env.filters["card"] = card_format


def go(url: str, **flash: str) -> RedirectResponse:
    """303 redirect. `flash` (ok=..., error=...) so'rov parametrlari bo'lib qo'shiladi va sahifada ko'rsatiladi."""
    if flash:
        url += ("&" if "?" in url else "?") + urlencode(flash)
    return RedirectResponse(url, status_code=303)


def render(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, {"config": config, "langs": LANGS, **ctx}, status_code=status_code)


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


# --- Pro imkoniyatlari: administrator hammasini ko'ra oladi ---
def reports_allowed(user: User) -> bool:
    """Hisobotlar: Pro (reports imkoniyati bor, faol pullik obuna) yoki administrator."""
    return user.is_admin or user.account.can("reports")


def can_search(user: User) -> bool:
    return user.is_admin or user.account.can("search")
