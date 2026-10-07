"""Jonli yangilanish: yangi qo'ng'iroq kelganda ochiq sahifalarga (SSE orqali) xabar beradi.

Bitta jarayon ichida ishlaydi (bitta uvicorn worker). `publish` istalgan oqimdan (webhook fon vazifasi) chaqiriladi,
`subscribe` esa asyncio oqimidan: har obunachining o'z event-loop'i va navbati bor.
"""
import asyncio
import threading
from dataclasses import dataclass, field

MAX_PER_ACCOUNT = 10   # bitta mijozning bir vaqtdagi ochiq ulanishlari (resursni himoya qilish)
KEEPALIVE = 25         # soniya: proksi (nginx 60s) ulanishni uzib qo'ymasligi uchun


@dataclass(eq=False)
class Sub:
    account_id: int
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=50))


_lock = threading.Lock()
_subs: dict[int, set[Sub]] = {}


def subscribe(account_id: int, loop: asyncio.AbstractEventLoop) -> Sub | None:
    with _lock:
        group = _subs.setdefault(account_id, set())
        if len(group) >= MAX_PER_ACCOUNT:
            return None
        sub = Sub(account_id, loop)
        group.add(sub)
        return sub


def unsubscribe(sub: Sub) -> None:
    with _lock:
        group = _subs.get(sub.account_id)
        if group:
            group.discard(sub)
            if not group:
                _subs.pop(sub.account_id, None)


def _put(queue: asyncio.Queue, event: dict) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:  # sekin mijoz: hodisani tashlaymiz, keyingisi sahifani baribir to'liq yangilaydi
        pass


def publish(account_id: int, event: dict) -> int:
    """Akkauntning barcha ochiq sahifalariga hodisa yuboradi (thread-safe). Yetkazilganlar soni."""
    with _lock:
        targets = list(_subs.get(account_id, ()))
    n = 0
    for sub in targets:
        try:
            sub.loop.call_soon_threadsafe(_put, sub.queue, event)
            n += 1
        except RuntimeError:  # loop yopilgan
            unsubscribe(sub)
    return n


def count(account_id: int) -> int:
    with _lock:
        return len(_subs.get(account_id, ()))
