import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import config, service
from .db import SessionLocal, get_db
from .models import Account

router = APIRouter()
log = logging.getLogger(__name__)


def _bg_call(account_id: int, uuid: str):
    with SessionLocal() as db:
        service.process_call(db, account_id, uuid)


@router.api_route("/hook/{token}", methods=["GET", "POST"])
async def pbx_hook(token: str, request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    """OnlinePBX webhook. uuid maydoni form, JSON yoki query'da kelishi mumkin."""
    acc = db.query(Account).filter_by(hook_token=token).first()
    if not acc:
        raise HTTPException(404)
    data = dict(request.query_params)
    try:
        data.update(dict(await request.form()))
    except Exception:
        pass
    if not data.get("uuid"):
        try:
            body = await request.json()
            if isinstance(body, dict):
                data.update(body)
        except Exception:
            pass
    uuid = str(data.get("uuid") or data.get("call_id") or "")[:100]
    if not uuid:
        raise HTTPException(400, "uuid yo'q")
    bg.add_task(_bg_call, acc.id, uuid)
    return {"ok": True}


@router.post("/tg/{secret}")
async def tg_hook(secret: str, request: Request, db: Session = Depends(get_db)):
    if secret != config.TG_WEBHOOK_SECRET:
        raise HTTPException(404)
    try:
        service.handle_update(db, await request.json())
    except Exception:
        log.exception("telegram update failed")
    return {"ok": True}
