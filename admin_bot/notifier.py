import asyncio
import logging
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from admin_bot.config import ADMIN_BOT_TOKEN

logger = logging.getLogger(__name__)


def _esc(value) -> str:
    """Telegram legacy Markdown uchun foydalanuvchi matnini xavfsiz qiladi."""
    if not isinstance(value, str):
        return value
    for ch in ("_", "*", "`", "["):
        value = value.replace(ch, "\\" + ch)
    return value


def _esc_doc(d: dict) -> dict:
    """Hujjat dict dagi barcha matn maydonlarini escape qiladi (nusxa qaytaradi)."""
    out = {}
    for k, v in d.items():
        if k == "positions":
            out[k] = [{**p, "name": _esc(p.get("name", ""))} for p in (v or [])]
        else:
            out[k] = _esc(v)
    return out


def _positions_text(positions: list) -> str:
    if not positions:
        return "\n  _(нет позиций)_"
    return "".join(
        f"\n• {p['name']} — {p['quantity']} шт × {p['price']:,.2f} = {p['total']:,.2f} $"
        for p in positions
    )


_TG_LIMIT = 4000  # Telegram 4096 belgidan cheklovi; uzun hujjatda qisqartiramiz


def _clip(text: str) -> str:
    if len(text) <= _TG_LIMIT:
        return text
    return text[:_TG_LIMIT].rsplit("\n", 1)[0] + "\n…_(список сокращён)_"


def _recipients(entity: str) -> list[int]:
    from admin_bot.config import ADMIN_CHAT_IDS, SUPER_ADMIN_IDS
    # supply faqat super adminlarga, qolganlar hammaga
    return list(SUPER_ADMIN_IDS if entity == "supply" else ADMIN_CHAT_IDS)


def _main_kb():
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"),         KeyboardButton(text="📋 Должники")],
            [KeyboardButton(text="👥 Администраторы"), KeyboardButton(text="🔍 Поиск")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )


async def _send_one(bot: Bot, chat_id: int, text: str, with_kb: bool):
    kb = _main_kb() if with_kb else None
    try:
        return await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
        )
    except TelegramBadRequest as e:
        # Markdown parse xatosi bo'lsa — oddiy matn sifatida qayta yuboramiz
        if "parse" in str(e).lower():
            return await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
        raise


async def _send_all(text: str, entity: str = "", recipients: list[int] | None = None,
                    with_kb: bool = True) -> dict:
    msg_data = {}
    recipients = _recipients(entity) if recipients is None else recipients
    if not recipients:
        return msg_data
    text = _clip(text)

    bot = Bot(token=ADMIN_BOT_TOKEN)
    try:
        for chat_id in recipients:
            try:
                msg = await _send_one(bot, chat_id, text, with_kb)
                msg_data[str(chat_id)] = msg.message_id
            except Exception as e:
                logger.error(f"Admin {chat_id} ga yuborishda xatolik: {e}")
    finally:
        await bot.session.close()
    return msg_data


async def _delete_all(msg_data: dict) -> None:
    if not msg_data:
        return
    bot = Bot(token=ADMIN_BOT_TOKEN)
    try:
        for chat_id_str, message_id in msg_data.items():
            try:
                await bot.delete_message(chat_id=int(chat_id_str), message_id=message_id)
            except Exception as e:
                logger.error(f"Admin {chat_id_str} delete xatolik: {e}")
    finally:
        await bot.session.close()


def _run(coro):
    """
    Sinxron kodni (poll_job — asyncio.to_thread ichida ishlaydi) async
    funksiya bilan ishlatadi. Thread da event loop yo'q, shuning uchun
    asyncio.run xavfsiz.
    """
    return asyncio.run(coro)


def send(text: str, entity: str = "") -> dict:
    """Yuboradi va {chat_id: message_id} qaytaradi."""
    return _run(_send_all(text, entity))


def send_plain(text: str, recipients: list[int]) -> dict:
    """Berilgan chat_id larga xabar yuboradi (ogohlantirishlar uchun)."""
    return _run(_send_all(text, recipients=list(recipients), with_kb=False))


def delete_messages(msg_data: dict) -> None:
    """Eski xabarlarni o'chiradi."""
    _run(_delete_all(msg_data))


# ── Headers ───────────────────────────────────────────────────────────────────

_HEADERS = {
    "demand": {
        "CREATE": "🟢 *Новая отгрузка*",
        "UPDATE": "🔄 *Отгрузка изменена*",
        "DELETE": "🗑 *Отгрузка удалена*",
    },
    "paymentin": {
        "CREATE": "💵 *Новый входящий платёж*",
        "UPDATE": "🔄 *Платёж изменён*",
        "DELETE": "🗑 *Платёж удалён*",
    },
    "salesreturn": {
        "CREATE": "🔴 *Новый возврат покупателя*",
        "UPDATE": "🔄 *Возврат изменён*",
        "DELETE": "🗑 *Возврат удалён*",
    },
    "supply": {
        "CREATE": "📥 *Новая приёмка товара*",
        "UPDATE": "🔄 *Приёмка изменена*",
        "DELETE": "🗑 *Приёмка удалена*",
    },
}


def _h(entity: str, event: str) -> str:
    return _HEADERS.get(entity, {}).get(event, "📋 *Изменение*")


# ── ОТГРУЗКА ──────────────────────────────────────────────────────────────────

def fmt_demand(d: dict, event: str = "CREATE") -> str:
    d = _esc_doc(d)
    h = _h("demand", event)

    if event == "DELETE":
        return (
            f"{h}\n\n"
            f"🧾 *Номер:* {d.get('doc_number', '—')}\n"
            f"👤 *Клиент:* {d.get('client_name', '—')}\n"
            f"📞 *Телефон:* {d.get('client_phone', '—')}\n"
            f"💰 *Сумма:* {d.get('total', 0):,.2f} $"
        )
    ostatka = d.get("total", 0) - d.get("payed_sum", 0)
    ostatka_line = f"\n⏳ *Остаток по документу:* {ostatka:,.2f} $" if ostatka > 0 else ""

    state_line = f"\n🏷 *Статус:* {d['state']}" if d.get("state") and d["state"] != "—" else ""

    # Umumiy qarzdorlik
    balance = d.get("balance")
    balance_line = f"\n💸 *Задолженность:* {balance:,.2f} $" if balance is not None and balance > 0 else ""

    return (
        f"{h}\n\n"
        f"🧾 *Номер:* {d['doc_number']}\n"
        f"📋 *Проведён:* {d.get('applicable', '—')}"
        f"{state_line}\n"
        f"📅 *Дата:* {d['moment']}\n"
        f"🏪 *Склад:* {d['store']}\n"
        f"📍 *Адрес:* {d['address']}\n"
        f"💬 *Комментарий:* {d['comment']}\n\n"
        f"👤 *Клиент:* {d['client_name']}\n"
        f"📞 *Телефон:* {d['client_phone']}\n"
        f"🧑‍💼 *Сотрудник:* {d.get('owner', '—')}\n\n"
        f"📦 *Товары:*{_positions_text(d['positions'])}\n\n"
        f"💰 *Сумма:* {d['total']:,.2f} $\n"
        f"✅ *Оплачено:* {d['payed_sum']:,.2f} $"
        f"{ostatka_line}"
        f"{balance_line}"
    )


# ── ВХОДЯЩИЙ ПЛАТЁЖ ───────────────────────────────────────────────────────────

def fmt_paymentin(d: dict, event: str = "CREATE") -> str:
    d = _esc_doc(d)
    h = _h("paymentin", event)

    if event == "DELETE":
        return (
            f"{h}\n\n"
            f"🧾 *Номер:* {d.get('doc_number', '—')}\n"
            f"👤 *Плательщик:* {d.get('client_name', '—')}\n"
            f"📞 *Телефон:* {d.get('client_phone', '—')}\n"
            f"💳 *Сумма:* {d.get('amount', 0):,.2f} $"
        )

    state_line = f"\n🏷 *Статус:* {d['state']}" if d.get("state") and d["state"] != "—" else ""
    inc_date = f"\n📬 *Входящая дата:* {d['incoming_date']}" if d.get("incoming_date") else ""
    inc_num  = f"\n🔢 *Входящий №:* {d['incoming_number']}" if d.get("incoming_number") and d["incoming_number"] != "—" else ""
    balance  = d.get("balance")
    balance_line = f"\n💸 *Задолженность:* {balance:,.2f} $" if balance is not None and balance > 0 else ""

    return (
        f"{h}\n\n"
        f"🧾 *Номер:* {d['doc_number']}\n"
        f"📋 *Проведён:* {d.get('applicable', '—')}"
        f"{state_line}\n"
        f"📅 *Дата:* {d['moment']}"
        f"{inc_date}{inc_num}\n\n"
        f"👤 *Плательщик:* {d['client_name']}\n"
        f"📞 *Телефон:* {d['client_phone']}\n"
        f"🧑‍💼 *Сотрудник:* {d.get('owner', '—')}\n\n"
        f"💳 *Сумма:* {d['amount']:,.2f} $\n"
        f"🏦 *Счёт:* {d['account_number']}\n"
        f"📝 *Назначение:* {d['purpose']}\n"
        f"💬 *Комментарий:* {d['comment']}"
        f"{balance_line}"
    )


# ── ВОЗВРАТ ПОКУПАТЕЛЯ ────────────────────────────────────────────────────────

def fmt_salesreturn(d: dict, event: str = "CREATE") -> str:
    d = _esc_doc(d)
    h = _h("salesreturn", event)

    if event == "DELETE":
        return (
            f"{h}\n\n"
            f"🧾 *Номер:* {d.get('doc_number', '—')}\n"
            f"👤 *Клиент:* {d.get('client_name', '—')}\n"
            f"📞 *Телефон:* {d.get('client_phone', '—')}\n"
            f"💸 *Сумма возврата:* {d.get('total', 0):,.2f} $"
        )

    state_line = f"\n🏷 *Статус:* {d['state']}" if d.get("state") and d["state"] != "—" else ""

    return (
        f"{h}\n\n"
        f"🧾 *Номер:* {d['doc_number']}\n"
        f"📋 *Проведён:* {d.get('applicable', '—')}"
        f"{state_line}\n"
        f"📅 *Дата:* {d['moment']}\n"
        f"🏪 *Склад:* {d['store']}\n"
        f"💬 *Комментарий:* {d['comment']}\n\n"
        f"👤 *Клиент:* {d['client_name']}\n"
        f"📞 *Телефон:* {d['client_phone']}\n"
        f"🧑‍💼 *Сотрудник:* {d.get('owner', '—')}\n\n"
        f"📦 *Товары:*{_positions_text(d['positions'])}\n\n"
        f"💸 *Сумма возврата:* {d['total']:,.2f} $"
    )


# ── ПРИЁМКА ───────────────────────────────────────────────────────────────────

def fmt_supply(d: dict, event: str = "CREATE") -> str:
    d = _esc_doc(d)
    h = _h("supply", event)

    if event == "DELETE":
        return (
            f"{h}\n\n"
            f"🧾 *Номер:* {d.get('doc_number', '—')}\n"
            f"🏭 *Поставщик:* {d.get('supplier_name', '—')}\n"
            f"🏪 *Склад:* {d.get('store', '—')}\n"
            f"💰 *Сумма:* {d.get('total', 0):,.2f} $"
        )

    state_line    = f"\n🏷 *Статус:* {d['state']}" if d.get("state") and d["state"] != "—" else ""
    inc_date      = f"\n📬 *Дата прихода:* {d['incoming_date']}" if d.get("incoming_date") else ""
    supplier_phone= f"\n📞 *Тел. поставщика:* {d['supplier_phone']}" if d.get("supplier_phone") and d["supplier_phone"] != "—" else ""

    # Hujjat bo'yicha qoldiq
    ostatka = d.get("total", 0) - d.get("payed_sum", 0)
    ostatka_line = f"\n⏳ *Остаток по документу:* {ostatka:,.2f} $" if ostatka > 0 else ""

    # Umumiy qarzdorlik (postavshik ham kontragent)
    balance = d.get("balance")
    balance_line = f"\n💸 *Задолженность:* {balance:,.2f} $" if balance is not None and balance > 0 else ""

    return (
        f"{h}\n\n"
        f"🧾 *Номер:* {d['doc_number']}\n"
        f"📋 *Проведён:* {d.get('applicable', '—')}"
        f"{state_line}\n"
        f"📅 *Дата:* {d['moment']}"
        f"{inc_date}\n"
        f"🏪 *Склад:* {d['store']}\n"
        f"🏭 *Поставщик:* {d['supplier_name']}"
        f"{supplier_phone}\n"
        f"🧑‍💼 *Сотрудник:* {d.get('owner', '—')}\n"
        f"💬 *Комментарий:* {d['comment']}\n\n"
        f"📦 *Товары:*{_positions_text(d['positions'])}\n\n"
        f"💰 *Сумма:* {d['total']:,.2f} $\n"
        f"✅ *Оплачено:* {d['payed_sum']:,.2f} $"
        f"{ostatka_line}"
        f"{balance_line}"
    )
