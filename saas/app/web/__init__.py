"""Veb-qatlam: sahifalar mavzular bo'yicha alohida modullarda."""

from fastapi import APIRouter

from . import admin, analytics, auth, billing, cabinet, calls, restore, stream

router = APIRouter()
for _module in (auth, cabinet, billing, analytics, calls, stream, restore, admin):
    router.include_router(_module.router)
