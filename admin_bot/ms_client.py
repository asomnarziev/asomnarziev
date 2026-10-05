"""
admin_bot/ms_client.py
----------------------
MoySklad API dan hujjatlarni oladi.

Har bir hujjat turi uchun 2 ta funksiya:
  poll_*(since)    — updated > since  → CREATE va UPDATE aniqlash
  deleted_*(since) — deleted > since  → DELETE aniqlash

Barcha 4 ta hujjat turida:
  - applicable  — проведён / не проведён
  - state       — status nomi (expand kerak)
  - deleted     — o'chirilgan vaqt (filtrlash mumkin)
  - updated     — oxirgi yangilanish vaqti
"""

import httpx
import logging
from datetime import datetime, timezone
from admin_bot.config import MS_HEADERS, MS_BASE_URL

logger = logging.getLogger(__name__)
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_MAX_ROWS = 2000  # cheksiz siklga qarshi himoya


# ── Yordamchi funksiyalar ─────────────────────────────────────────────────────

from zoneinfo import ZoneInfo
_TZ = ZoneInfo("Asia/Tashkent")
_MS_TZ = ZoneInfo("Europe/Moscow")  # MoySklad barcha vaqtlarni Moskva vaqtida beradi/kutadi


def _moment_date(moment_str: str) -> str:
    """MoySklad moment (Moskva vaqti) ni Toshkent sanasiga o'giradi."""
    if not moment_str:
        return "—"
    try:
        dt = datetime.fromisoformat(moment_str.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_MS_TZ)
        return dt.astimezone(_TZ).strftime("%Y-%m-%d")
    except Exception:
        return moment_str[:10]


def _get(url: str) -> list[dict]:
    """
    Barcha sahifalarni (limit=100, offset) o'qib qaytaradi.
    Xatolik bo'lsa exception ko'taradi — jim bo'sh ro'yxat QAYTARMAYDI,
    shunda polling xatosi ko'rinadi va admin ogohlantiriladi.
    """
    rows: list[dict] = []
    offset = 0
    with httpx.Client(headers=MS_HEADERS, timeout=_TIMEOUT) as client:
        while True:
            sep = "&" if "?" in url else "?"
            resp = client.get(f"{url}{sep}offset={offset}")
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("rows", [])
            rows.extend(batch)
            size = (data.get("meta") or {}).get("size", len(rows))
            offset += len(batch)
            if not batch or offset >= size or offset >= _MAX_ROWS:
                break
    return rows


def _since_str(since: datetime) -> str:
    """since (UTC) ni MoySklad kutgan Moskva vaqtiga o'giradi."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return since.astimezone(_MS_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _positions(rows: list) -> list[dict]:
    result = []
    for item in rows:
        assortment = item.get("assortment") or {}
        qty   = item.get("quantity", 0)
        price = item.get("price", 0) / 100
        result.append({
            "name":     assortment.get("name", "Товар"),
            "quantity": qty,
            "price":    price,
            "total":    qty * price,
        })
    return result


def _applicable_text(val: bool) -> str:
    return "✅ Проведён" if val else "❌ Не проведён"


def _state_name(row: dict) -> str:
    state = row.get("state") or {}
    return state.get("name") or "—"


def get_counterparty_balance(agent_id: str) -> float:
    """
    Kontragentning umumiy qarzdorligini qaytaradi.
    balance manfiy = mijoz qarzdor → Задолженность musbat
    balance musbat = mijoz ortiqcha to'lagan → Задолженность manfiy (ko'rsatilmaydi)
    """
    url = f"{MS_BASE_URL}/report/counterparty/{agent_id}"
    try:
        with httpx.Client(headers=MS_HEADERS, timeout=_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            balance = data.get("balance", 0)
            return round(balance / 100 * (-1), 2)
    except Exception as e:
        logger.error(f"Counterparty balance xatolik {agent_id}: {e}")
    return 0.0


# ── ОТГРУЗКА (demand) ─────────────────────────────────────────────────────────

def poll_demands(since: datetime) -> list[dict]:
    """updated > since bo'lgan otgruzkalarni qaytaradi (CREATE + UPDATE)."""
    url = (
        f"{MS_BASE_URL}/entity/demand"
        f"?filter=updated>{_since_str(since)}"
        f"&expand=agent,store,positions.assortment,state,owner"
        f"&limit=100&order=updated,asc"
    )
    result = []
    for r in _get(url):
        agent = r.get("agent") or {}
        store = r.get("store") or {}
        owner = r.get("owner") or {}
        result.append({
            "id":          r.get("id", ""),
            "updated":     r.get("updated", ""),
            "doc_number":  r.get("name", "—"),
            "moment":      _moment_date(r.get("moment", "")),
            "applicable":  _applicable_text(r.get("applicable", True)),
            "state":       _state_name(r),
            "total":       r.get("sum", 0) / 100,
            "payed_sum":   r.get("payedSum", 0) / 100,
            "comment":     r.get("description") or "—",
            "address":     r.get("shipmentAddress") or "—",
            "client_name": agent.get("name") or "—",
            "client_phone":agent.get("phone") or "—",
            "agent_id":    agent.get("id") or "",
            "store":       store.get("name") or "—",
            "positions":   _positions(r.get("positions", {}).get("rows", [])),
            "owner":       owner.get("name") or "—",
        })
    return result


def deleted_demands(since: datetime) -> list[dict]:
    """deleted > since bo'lgan o'chirilgan otgruzkalarni qaytaradi."""
    url = (
        f"{MS_BASE_URL}/entity/demand"
        f"?filter=deleted>{_since_str(since)}"
        f"&expand=agent&limit=100"
    )
    result = []
    for r in _get(url):
        agent = r.get("agent") or {}
        result.append({
            "id":          r.get("id", ""),
            "doc_number":  r.get("name", "—"),
            "client_name": agent.get("name") or "—",
            "client_phone":agent.get("phone") or "—",
            "total":       r.get("sum", 0) / 100,
        })
    return result


# ── ВХОДЯЩИЙ ПЛАТЁЖ (paymentin) ───────────────────────────────────────────────

def poll_paymentins(since: datetime) -> list[dict]:
    """updated > since bo'lgan to'lovlarni qaytaradi (CREATE + UPDATE)."""
    url = (
        f"{MS_BASE_URL}/entity/paymentin"
        f"?filter=updated>{_since_str(since)}"
        f"&expand=agent,organizationAccount,state,owner"
        f"&limit=100&order=updated,asc"
    )
    result = []
    for r in _get(url):
        agent   = r.get("agent") or {}
        org_acc = r.get("organizationAccount") or {}
        owner   = r.get("owner") or {}
        result.append({
            "id":              r.get("id", ""),
            "updated":         r.get("updated", ""),
            "doc_number":      r.get("name", "—"),
            "moment":          _moment_date(r.get("moment", "")),
            "applicable":      _applicable_text(r.get("applicable", True)),
            "state":           _state_name(r),
            "amount":          r.get("sum", 0) / 100,
            "comment":         r.get("description") or "—",
            "purpose":         r.get("paymentPurpose") or "—",
            "incoming_number": r.get("incomingNumber") or "—",
            "incoming_date":   _moment_date(r.get("incomingDate", "")),
            "client_name":     agent.get("name") or "—",
            "client_phone":    agent.get("phone") or "—",
            "agent_id":        agent.get("id") or "",
            "account_number":  org_acc.get("accountNumber") or "—",
            "owner":           owner.get("name") or "—",
        })
    return result


def deleted_paymentins(since: datetime) -> list[dict]:
    """deleted > since bo'lgan o'chirilgan to'lovlarni qaytaradi."""
    url = (
        f"{MS_BASE_URL}/entity/paymentin"
        f"?filter=deleted>{_since_str(since)}"
        f"&expand=agent&limit=100"
    )
    result = []
    for r in _get(url):
        agent = r.get("agent") or {}
        result.append({
            "id":          r.get("id", ""),
            "doc_number":  r.get("name", "—"),
            "client_name": agent.get("name") or "—",
            "client_phone":agent.get("phone") or "—",
            "amount":      r.get("sum", 0) / 100,
        })
    return result


# ── ВОЗВРАТ ПОКУПАТЕЛЯ (salesreturn) ──────────────────────────────────────────

def poll_salesreturns(since: datetime) -> list[dict]:
    """updated > since bo'lgan qaytarishlarni qaytaradi (CREATE + UPDATE)."""
    url = (
        f"{MS_BASE_URL}/entity/salesreturn"
        f"?filter=updated>{_since_str(since)}"
        f"&expand=agent,store,positions.assortment,state,owner"
        f"&limit=100&order=updated,asc"
    )
    result = []
    for r in _get(url):
        agent = r.get("agent") or {}
        store = r.get("store") or {}
        owner = r.get("owner") or {}
        result.append({
            "id":          r.get("id", ""),
            "updated":     r.get("updated", ""),
            "doc_number":  r.get("name", "—"),
            "moment":      _moment_date(r.get("moment", "")),
            "applicable":  _applicable_text(r.get("applicable", True)),
            "state":       _state_name(r),
            "total":       r.get("sum", 0) / 100,
            "comment":     r.get("description") or "—",
            "client_name": agent.get("name") or "—",
            "client_phone":agent.get("phone") or "—",
            "agent_id":    agent.get("id") or "",
            "store":       store.get("name") or "—",
            "positions":   _positions(r.get("positions", {}).get("rows", [])),
            "owner":       owner.get("name") or "—",
        })
    return result


def deleted_salesreturns(since: datetime) -> list[dict]:
    """deleted > since bo'lgan o'chirilgan qaytarishlarni qaytaradi."""
    url = (
        f"{MS_BASE_URL}/entity/salesreturn"
        f"?filter=deleted>{_since_str(since)}"
        f"&expand=agent&limit=100"
    )
    result = []
    for r in _get(url):
        agent = r.get("agent") or {}
        result.append({
            "id":          r.get("id", ""),
            "doc_number":  r.get("name", "—"),
            "client_name": agent.get("name") or "—",
            "client_phone":agent.get("phone") or "—",
            "total":       r.get("sum", 0) / 100,
        })
    return result


# ── ПРИЁМКА (supply) ──────────────────────────────────────────────────────────

def poll_supplies(since: datetime) -> list[dict]:
    """updated > since bo'lgan priyomkalarni qaytaradi (CREATE + UPDATE)."""
    url = (
        f"{MS_BASE_URL}/entity/supply"
        f"?filter=updated>{_since_str(since)}"
        f"&expand=agent,store,positions.assortment,state,owner"
        f"&limit=100&order=updated,asc"
    )
    result = []
    for r in _get(url):
        agent = r.get("agent") or {}
        store = r.get("store") or {}
        owner = r.get("owner") or {}
        result.append({
            "id":            r.get("id", ""),
            "updated":       r.get("updated", ""),
            "doc_number":    r.get("name", "—"),
            "moment":        _moment_date(r.get("moment", "")),
            "incoming_date": _moment_date(r.get("incomingDate", "")),
            "applicable":    _applicable_text(r.get("applicable", True)),
            "state":         _state_name(r),
            "total":         r.get("sum", 0) / 100,
            "payed_sum":     r.get("payedSum", 0) / 100,
            "comment":       r.get("description") or "—",
            "supplier_name": agent.get("name") or "—",
            "supplier_phone":agent.get("phone") or "—",
            "agent_id":      agent.get("id") or "",
            "store":         store.get("name") or "—",
            "positions":     _positions(r.get("positions", {}).get("rows", [])),
            "owner":         owner.get("name") or "—",
        })
    return result


def deleted_supplies(since: datetime) -> list[dict]:
    """deleted > since bo'lgan o'chirilgan priyomkalarni qaytaradi."""
    url = (
        f"{MS_BASE_URL}/entity/supply"
        f"?filter=deleted>{_since_str(since)}"
        f"&expand=agent,store&limit=100"
    )
    result = []
    for r in _get(url):
        agent = r.get("agent") or {}
        store = r.get("store") or {}
        owner = r.get("owner") or {}
        result.append({
            "id":            r.get("id", ""),
            "doc_number":    r.get("name", "—"),
            "supplier_name": agent.get("name") or "—",
            "supplier_phone":agent.get("phone") or "—",
            "store":         store.get("name") or "—",
            "total":         r.get("sum", 0) / 100,
        })
    return result
