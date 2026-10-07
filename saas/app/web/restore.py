"""Bazani zaxiradan tiklash sahifalari (faqat administrator, faqat `ALLOW_PANEL_RESTORE=1` bo'lsa)."""

import hmac
import logging
import os
import threading

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import restore as core
from ..db import engine, get_db, init_db
from ..models import User
from ..ratelimit import login_throttle
from ..security import verify_password
from .deps import go, render, require_admin

router = APIRouter()
log = logging.getLogger(__name__)


def _guard(admin: User = Depends(require_admin)) -> User:
    if not core.enabled():
        raise HTTPException(404)
    return admin


def _page(request: Request, admin: User, **ctx):
    snaps = []
    if core.enabled():
        try:
            snaps = [(p.name, round(p.stat().st_size / 1024)) for p in core.list_snapshots()]
        except OSError:
            snaps = []
    return render(request, "admin_restore.html", user=admin, enabled=core.enabled(), snapshots=snaps, **ctx)


@router.get("/admin/restore")
def restore_page(request: Request, error: str = "", admin: User = Depends(require_admin)):
    return _page(request, admin, error=error[:300])


def _confirm(request: Request, admin: User, token: str, info: core.DumpInfo, source: str):
    request.session["restore_token"] = token
    return render(request, "restore_confirm.html", user=admin, info=info, source=source, phrase=core.CONFIRM_PHRASE)


@router.post("/admin/restore/inspect")
async def inspect(
    request: Request,
    backup: UploadFile = File(...),
    passphrase: str = Form(""),
    admin: User = Depends(_guard),
):
    blob = await backup.read(core.MAX_UPLOAD_BYTES + 1)
    try:
        token, info = core.stage_upload(blob, passphrase.strip() or os.environ.get("BACKUP_PASSPHRASE", ""))
    except core.RestoreError as e:
        return go("/admin/restore", error=str(e))
    return _confirm(request, admin, token, info, backup.filename or "yuklangan fayl")


@router.post("/admin/restore/snapshot")
def pick_snapshot(request: Request, name: str = Form(...), admin: User = Depends(_guard)):
    try:
        token, info = core.stage_snapshot(name)
    except core.RestoreError as e:
        return go("/admin/restore", error=str(e))
    return _confirm(request, admin, token, info, name)


@router.post("/admin/restore/run")
def run(
    request: Request,
    token: str = Form(...),
    confirm: str = Form(""),
    password: str = Form(""),
    admin: User = Depends(_guard),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "?"
    key = f"restore:{admin.id}"
    if wait := login_throttle.blocked(ip, key):
        return go("/admin/restore", error=f"Juda ko'p urinish. {wait // 60 + 1} daqiqadan keyin qayta urinib ko'ring")
    session_token = request.session.get("restore_token", "")
    if not session_token or not hmac.compare_digest(session_token, token):
        return go("/admin/restore", error="Tasdiqlash muddati tugagan: faylni qaytadan tanlang")
    if confirm.strip() != core.CONFIRM_PHRASE:
        return go("/admin/restore", error=f"Tasdiqlash so'zi noto'g'ri: {core.CONFIRM_PHRASE} deb yozing")
    if not verify_password(password, admin.password_hash):
        login_throttle.fail(ip, key)
        return go("/admin/restore", error="Parol noto'g'ri")
    login_throttle.reset(key)
    try:
        pending = core.pending_path(token)
    except core.RestoreError as e:
        return go("/admin/restore", error=str(e))
    if not pending.is_file():
        return go("/admin/restore", error="Fayl topilmadi yoki muddati o'tgan: qaytadan yuklang")
    if not core.STATE.claim(token):
        return go("/admin/restore", error="Tiklash allaqachon bajarilmoqda")
    actor = admin.email

    def after() -> None:
        engine.dispose()  # eski bazaga bog'langan ulanishlar tashlanadi
        init_db()  # eski zaxira bo'lsa, yangi ustunlar qo'shiladi

    threading.Thread(target=core.run_restore, args=(pending, actor), kwargs={"post_restore": after}, daemon=True).start()
    return go("/admin/restore/progress")


@router.get("/admin/restore/progress")
def progress(request: Request):
    """Baza ishlamay turganda ham ochiladi (bazaga tegmaydi); holatni /status dan o'qiydi."""
    if not core.STATE.token or not hmac.compare_digest(request.session.get("restore_token", ""), core.STATE.token):
        raise HTTPException(404)
    return render(request, "restore_progress.html")


@router.get("/admin/restore/status")
def status(request: Request):
    s = core.STATE
    if not s.token or not hmac.compare_digest(request.session.get("restore_token", ""), s.token):
        raise HTTPException(404)
    return JSONResponse({"phase": s.phase, "message": s.message}, headers={"Cache-Control": "no-store"})
