"""Jonli yangilanish: Server-Sent Events (Pro). Baza sessiyasi oqim davomida ushlab turilmaydi."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from .. import live
from ..db import SessionLocal
from ..models import User
from .deps import can_search, reports_allowed

router = APIRouter()


def _live_account(uid) -> int | None:
    """Sessiyadagi foydalanuvchi akkaunt ID'si, agar jonli yangilanish ruxsat etilgan bo'lsa (Pro).
    Baza sessiyasi shu yerda yopiladi: uzoq oqim davomida ulanish band qilinmaydi."""
    with SessionLocal() as db:
        user = db.get(User, uid) if uid else None
        if user and (reports_allowed(user) or can_search(user)):
            return user.account.id
    return None


@router.get("/live/stream")
async def live_stream(request: Request):
    """SSE: yangi qo'ng'iroq kelganda sahifa yangilanishi uchun hodisa. Sessiya cookie bilan himoyalangan."""
    account_id = await run_in_threadpool(_live_account, request.session.get("uid"))
    if account_id is None:
        raise HTTPException(403, "Jonli yangilanish Pro tarifda")
    sub = live.subscribe(account_id, asyncio.get_running_loop())
    if sub is None:
        raise HTTPException(429, "Ochiq sahifalar juda ko'p")

    async def events():
        try:
            yield "retry: 3000\n: connected\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(sub.queue.get(), live.KEEPALIVE)
                    yield f"event: call\ndata: {json.dumps(ev)}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            live.unsubscribe(sub)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
    )
