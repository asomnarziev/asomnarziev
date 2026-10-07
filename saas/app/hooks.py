import hmac
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


async def _payload(request: Request) -> dict:
    """OnlinePBX webhook maydonlari: query, form yoki JSON ko'rinishida kelishi mumkin."""
    data = dict(request.query_params)
    try:
        data.update(dict(await request.form()))
    except Exception:
        log.debug("webhook: form parse failed", exc_info=True)
    if not data.get("uuid"):
        try:
            body = await request.json()
            if isinstance(body, dict):
                data.update(body)
        except Exception:
            log.debug("webhook: json parse failed", exc_info=True)
    return data


@router.api_route("/hook/{token}", methods=["GET", "POST"])
async def pbx_hook(token: str, request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    """OnlinePBX webhook: qo'ng'iroq tugaganda uuid keladi; qayta ishlash fonda bajariladi."""
    acc = db.query(Account).filter_by(hook_token=token).first()
    if not acc:
        raise HTTPException(404)
    data = await _payload(request)
    uuid = str(data.get("uuid") or data.get("call_id") or "")[:100]
    if not uuid:
        # OnlinePBX "Тест" so'rovi uuid'siz keladi; 4xx qaytarsak webhook "buzuq" deb belgilanadi
        log.info("webhook without uuid ignored, fields: %s", sorted(data))
        return {"ok": True, "ignored": True}
    log.info("webhook uuid=%s fields=%s", uuid, sorted(data))
    bg.add_task(_bg_call, acc.id, uuid)
    return {"ok": True}


@router.post("/tg/{secret}")
async def tg_hook(secret: str, request: Request, db: Session = Depends(get_db)):
    if not hmac.compare_digest(secret.encode(), config.TG_WEBHOOK_SECRET.encode()):  # vaqt orqali taxmin qilib bo'lmasin
        raise HTTPException(404)
    try:
        service.handle_update(db, await request.json())
    except Exception:
        log.exception("telegram update failed")
    return {"ok": True}
