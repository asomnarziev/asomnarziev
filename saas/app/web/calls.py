"""Pro: qo'ng'iroqlarni qidirish, yozuvni brauzerda tinglash/yuklab olish va Telegramga qayta yuborish.

Yozuv fayllari serverda saqlanmaydi: har safar OnlinePBX'dan xotiraga olinib, brauzerga uzatiladi.
"""

import logging
import time
from collections import defaultdict, deque
from datetime import UTC
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config, reports, service, telegram
from ..db import get_db
from ..messages import LANGS
from ..models import Account, CallLog, User
from ..pbx import PbxClient, PbxError
from ..security import decrypt
from .deps import can_search, go, render, require_user

log = logging.getLogger(__name__)
router = APIRouter()
PAGE = 50
_fetches: dict[int, deque] = defaultdict(deque)  # akkaunt -> yozuv olingan vaqtlar (soatlik limit uchun)


def _fetch_record(acc: Account, uuid: str) -> bytes:
    """Yozuvni OnlinePBX'dan qayta oladi. Soatiga MAX_RECORD_FETCH_PER_HOUR dan ko'p emas (API limitidan himoya)."""
    q, t = _fetches[acc.id], time.monotonic()
    while q and t - q[0] > 3600:
        q.popleft()
    if len(q) >= config.MAX_RECORD_FETCH_PER_HOUR:
        raise HTTPException(
            429, f"Yozuvni qayta olish limiti oshdi (soatiga {config.MAX_RECORD_FETCH_PER_HOUR} ta). Keyinroq urinib ko'ring."
        )
    q.append(t)
    try:
        audio = PbxClient(acc.pbx_domain, decrypt(acc.pbx_key_enc)).record(uuid)
    except PbxError as e:
        raise HTTPException(502, str(e)) from e
    if not audio:
        raise HTTPException(404, "Yozuv topilmadi")
    return audio


def _own_call(db: Session, user: User, call_id: int) -> CallLog:
    row = db.get(CallLog, call_id)
    if not row or row.account_id != user.account.id:
        raise HTTPException(404)
    return row


def _row_to_call(row: CallLog) -> dict:
    """CallLog qatoridan OnlinePBX javobiga o'xshash lug'at (xabar shablonlari uchun)."""
    stamp = int(row.started_at.replace(tzinfo=UTC).timestamp()) if row.started_at else None
    return {
        "uuid": row.uuid,
        "accountcode": row.direction,
        "caller_id_number": row.caller,
        "destination_number": row.callee,
        "duration": row.duration,
        "start_stamp": stamp,
    }


def _search(db: Session, acc: Account, q: str, ext: str, direction: str, status: str, start, end):
    lo, hi = reports.utc_bounds(start, end)
    qs = db.query(CallLog).filter(CallLog.account_id == acc.id, CallLog.started_at >= lo, CallLog.started_at < hi)
    if q:
        qs = qs.filter(CallLog.caller.contains(q, autoescape=True) | CallLog.callee.contains(q, autoescape=True))
    if ext:
        qs = qs.filter((CallLog.caller == ext) | (CallLog.callee == ext))
    if direction in ("inbound", "outbound", "local"):
        qs = qs.filter(CallLog.direction == direction)
    talk = func.coalesce(CallLog.talk, CallLog.duration)
    missed = talk.is_(None) | (talk <= 0)
    if status == "missed":
        qs = qs.filter(missed)
    elif status == "answered":
        qs = qs.filter(~missed)
    return qs


@router.get("/calls")
def calls_page(
    request: Request,
    q: str = "",
    ext: str = "",
    d: str = "",
    st: str = "",
    frm: str | None = None,
    to: str | None = None,
    page: int = 1,
    ok: str | None = None,
    error: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Qo'ng'iroqlarni raqam, xodim, yo'nalish, holat va sana bo'yicha qidirish (Pro)."""
    if not can_search(user):
        return render(request, "calls_locked.html", user=user, acc=user.account, plans=config.PLANS)
    start, end, _ = reports.parse_period("30d", frm, to)
    q, ext = q.strip()[:40], ext.strip()[:10]
    qs = _search(db, user.account, q, ext, d, st, start, end)
    total = qs.count()
    pages = max(1, -(-total // PAGE))
    page = min(max(page, 1), pages)
    rows = qs.order_by(CallLog.started_at.desc()).offset((page - 1) * PAGE).limit(PAGE).all()
    base = urlencode({"q": q, "ext": ext, "d": d, "st": st, "frm": start.isoformat(), "to": end.isoformat()})
    return render(
        request,
        "calls.html",
        user=user,
        acc=user.account,
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        q=q,
        ext=ext,
        d=d,
        st=st,
        start=start,
        end=end,
        base=base,
        local=reports.local,
        answered=reports.answered,
        parties=reports.parties,
        ok=ok,
        error=error,
    )


@router.get("/calls/{call_id}/record")
def call_record(call_id: int, download: int = 0, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if not can_search(user):
        raise HTTPException(403, "Yozuvlarni qayta olish Pro tarifda")
    row = _own_call(db, user, call_id)
    audio = _fetch_record(user.account, row.uuid)
    name = service.audio_name(_row_to_call(row))
    return Response(
        audio,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/calls/{call_id}/send")
def call_resend(call_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Yozuvni qayta Telegramga yuborish (akkauntning barcha chatlariga, har biri o'z tilida)."""
    if not can_search(user):
        raise HTTPException(403)
    acc = user.account
    row = _own_call(db, user, call_id)
    if not acc.chats:
        return go("/calls", error="Avval Telegram chat ulang")
    try:
        audio = _fetch_record(acc, row.uuid)
    except HTTPException as e:
        return go("/calls", error=str(e.detail))
    call, sent = _row_to_call(row), 0
    for chat in acc.chats:
        lang = chat.lang if chat.lang in LANGS else "uz"
        try:
            telegram.send_audio(chat.chat_id, service.render_call(lang, call), audio, service.audio_name(call))
            sent += 1
        except Exception:
            log.warning("resend failed chat=%s", chat.chat_id)
    if sent:
        return go("/calls", ok="sent")
    return go("/calls", error="Telegramga yuborib bo'lmadi: chatlarni tekshiring")
