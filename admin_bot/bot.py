"""
admin_bot/bot.py
----------------
Har qanday menyu bosilganda oldingi menyu xabari o'chiriladi.
Hujjat xabarlari (otguzka, platej va h.k.) o'z o'rnida turadi.
"""

import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.enums import ParseMode

from admin_bot.config import ADMIN_BOT_TOKEN, ADMIN_CHAT_IDS, MS_BASE_URL, MS_HEADERS
from admin_bot.db import get_today_stats
from admin_bot import state as bot_state

import httpx

from admin_bot.notifier import _esc

logger = logging.getLogger(__name__)

# ── FSM States ────────────────────────────────────────────────────────────────

class AddAdmin(StatesGroup):
    choosing_type = State()
    waiting_id    = State()

class RemoveAdmin(StatesGroup):
    waiting_id = State()

class SetInterval(StatesGroup):
    waiting_seconds = State()

class Search(StatesGroup):
    waiting_query = State()

# ── Menu message tracker ──────────────────────────────────────────────────────
# Har bir admin uchun oxirgi menyu xabarining message_id sini saqlaymiz
_last_menu_msg: dict[int, int] = {}   # chat_id → message_id
_debtors_cache: dict[int, list] = {}  # chat_id → debtors list

PAGE_SIZE = 10

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_admin(chat_id: int) -> bool:
    from admin_bot.config import SUPER_ADMIN_IDS
    return chat_id in ADMIN_CHAT_IDS or chat_id in SUPER_ADMIN_IDS

def is_super_admin(chat_id: int) -> bool:
    from admin_bot.config import SUPER_ADMIN_IDS
    return chat_id in SUPER_ADMIN_IDS

def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"),         KeyboardButton(text="📋 Должники")],
            [KeyboardButton(text="👥 Администраторы"), KeyboardButton(text="🔍 Поиск")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )

def settings_kb() -> ReplyKeyboardMarkup:
    pause_text = "▶️ Возобновить" if bot_state.paused else "⏸ Пауза"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=pause_text),           KeyboardButton(text="⏱ Интервал")],
            [KeyboardButton(text="➕ Добавить админа"), KeyboardButton(text="➖ Удалить админа")],
            [KeyboardButton(text="🔙 Назад")],
        ],
        resize_keyboard=True
    )

def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Отмена")]],
        resize_keyboard=True
    )

async def _delete_last_menu(bot: Bot, chat_id: int):
    """Oldingi menyu xabarini o'chiradi."""
    msg_id = _last_menu_msg.pop(chat_id, None)
    if msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

async def _send_menu(message: types.Message, bot: Bot, text: str,
                     reply_markup=None, parse_mode=ParseMode.MARKDOWN) -> types.Message:
    """Oldingi menyu xabarini o'chirib yangi yuboradi."""
    await _delete_last_menu(bot, message.from_user.id)
    # Agar reply_markup berilmasa — main_kb ni ishlatamiz (keyboard yo'qolmasin)
    if reply_markup is None:
        reply_markup = main_kb()
    sent = await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    _last_menu_msg[message.from_user.id] = sent.message_id
    return sent

# ── MoySklad ──────────────────────────────────────────────────────────────────

async def fetch_top_debtors(limit: int = 200) -> list[dict]:
    """Barcha kontragentlar hisobotini sahifalab o'qib, qarzdorlarni qaytaradi."""
    debtors = []
    offset = 0
    try:
        async with httpx.AsyncClient(headers=MS_HEADERS, timeout=15.0) as client:
            while True:
                resp = await client.get(
                    f"{MS_BASE_URL}/report/counterparty",
                    params={"limit": 1000, "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()
                rows = data.get("rows", [])
                for r in rows:
                    debt = round(r.get("balance", 0) / 100 * (-1), 2)
                    if debt > 0:
                        cp = r.get("counterparty", {})
                        debtors.append({"name": cp.get("name", "—"), "debt": debt})
                offset += len(rows)
                size = (data.get("meta") or {}).get("size", offset)
                if not rows or offset >= size:
                    break
    except Exception as e:
        logger.error(f"fetch_top_debtors: {e}")
        return []
    debtors.sort(key=lambda x: x["debt"], reverse=True)
    return debtors[:limit]

async def search_counterparty(query: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(headers=MS_HEADERS, timeout=15.0) as client:
            resp = await client.get(
                f"{MS_BASE_URL}/entity/counterparty",
                params={"search": query, "limit": 5},
            )
            resp.raise_for_status()
            return [
                {"id": r.get("id", ""), "name": r.get("name", "—"), "phone": r.get("phone") or "—"}
                for r in resp.json().get("rows", [])
            ]
    except Exception as e:
        logger.error(f"search_counterparty: {e}")
    return []

async def get_balance_async(agent_id: str) -> float:
    url = f"{MS_BASE_URL}/report/counterparty/{agent_id}"
    try:
        async with httpx.AsyncClient(headers=MS_HEADERS, timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return round(resp.json().get("balance", 0) / 100 * (-1), 2)
    except Exception as e:
        logger.error(f"balance: {e}")
    return 0.0

# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    await state.clear()
    logger.info(f"👤 /start: chat_id={message.from_user.id} username=@{message.from_user.username} name={message.from_user.full_name}")
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    await _send_menu(
        message, bot,
        "👋 *Добро пожаловать!*\n"
        "Этот бот уведомляет администраторов о событиях в МойСклад.\n\n"
        "Выберите действие:",
        reply_markup=main_kb()
    )


async def handle_status(message: types.Message, bot: Bot, **kwargs):
    if not is_admin(message.from_user.id):
        return
    stats = get_today_stats()
    last  = bot_state.last_poll_time
    status_icon = "⏸ Пауза" if bot_state.paused else "✅ Активен"
    if last:
        from zoneinfo import ZoneInfo
        tashkent = ZoneInfo("Asia/Tashkent")
        last_str = last.astimezone(tashkent).strftime("%H:%M:%S")
    else:
        last_str = "—"
    await _send_menu(
        message, bot,
        f"📊 *Статус системы*\n\n"
        f"🔄 *Состояние:* {status_icon}\n"
        f"⏱ *Интервал:* {bot_state.poll_interval // 60} мин\n"
        f"🕐 *Последний опрос:* {last_str}\n\n"
        f"📈 *Сегодня отправлено:*\n"
        f"  📦 Отгрузки: *{stats.get('demand', 0)}*\n"
        f"  💵 Платежи: *{stats.get('paymentin', 0)}*\n"
        f"  🔴 Возвраты: *{stats.get('salesreturn', 0)}*\n"
        f"  📥 Приёмки: *{stats.get('supply', 0)}*"
    )


async def handle_admins(message: types.Message, bot: Bot, **kwargs):
    if not is_admin(message.from_user.id):
        return
    from admin_bot.config import SUPER_ADMIN_IDS
    lines = []
    if SUPER_ADMIN_IDS:
        lines.append("👑 *Супер-администраторы:*")
        for cid in SUPER_ADMIN_IDS:
            lines.append(f"  • `{cid}`")
    regular = [cid for cid in ADMIN_CHAT_IDS if cid not in SUPER_ADMIN_IDS]
    if regular:
        lines.append("\n👤 *Обычные администраторы:*")
        for cid in regular:
            lines.append(f"  • `{cid}`")
    if not lines:
        await _send_menu(message, bot, "👥 Список администраторов пуст.")
        return
    await _send_menu(message, bot, "👥 *Администраторы:*\n\n" + "\n".join(lines))


async def handle_settings(message: types.Message, bot: Bot, **kwargs):
    if not is_admin(message.from_user.id):
        return
    await _send_menu(message, bot, "⚙️ *Настройки*", reply_markup=settings_kb())


async def handle_back(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await _send_menu(message, bot, "🏠 Главное меню", reply_markup=main_kb())


async def handle_pause_resume(message: types.Message, bot: Bot, **kwargs):
    if not is_admin(message.from_user.id):
        return
    if not is_super_admin(message.from_user.id):
        await _send_menu(message, bot, "⛔ Только главный администратор может управлять паузой.")
        return
    bot_state.paused = not bot_state.paused
    status = "⏸ Опрос приостановлен" if bot_state.paused else "▶️ Опрос возобновлён"
    await _send_menu(message, bot, status, reply_markup=settings_kb())


async def handle_interval_start(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if not is_admin(message.from_user.id):
        return
    if not is_super_admin(message.from_user.id):
        await _send_menu(message, bot, "⛔ Только главный администратор может менять интервал.")
        return
    await state.set_state(SetInterval.waiting_seconds)
    await _send_menu(
        message, bot,
        f"⏱ Текущий интервал: *{bot_state.poll_interval // 60} мин*\n\nВведите новый (1–60):",
        reply_markup=cancel_kb()
    )


async def handle_interval_input(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if message.text == "🔙 Отмена":
        await state.clear()
        await _send_menu(message, bot, "⚙️ *Настройки*", reply_markup=settings_kb())
        return
    try:
        minutes = int((message.text or '').strip())
        if not 1 <= minutes <= 60:
            raise ValueError
        bot_state.poll_interval = minutes * 60
        bot_state.interval_changed = True
        await state.clear()
        await _send_menu(message, bot, f"✅ Интервал изменён на *{minutes} мин*", reply_markup=settings_kb())
    except ValueError:
        await message.answer("❌ Введите число от 1 до 60 или нажмите 🔙 Отмена.")


async def handle_add_admin_start(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if not is_admin(message.from_user.id):
        return
    if not is_super_admin(message.from_user.id):
        await _send_menu(message, bot, "⛔ Только супер-администратор может добавлять админов.")
        return
    await state.set_state(AddAdmin.choosing_type)
    await _send_menu(
        message, bot,
        "➕ Выберите тип администратора:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👤 Обычный админ"), KeyboardButton(text="👑 Супер админ")],
                [KeyboardButton(text="🔙 Отмена")],
            ],
            resize_keyboard=True
        )
    )


async def handle_add_admin_type(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if message.text == "🔙 Отмена":
        await state.clear()
        await _send_menu(message, bot, "⚙️ *Настройки*", reply_markup=settings_kb())
        return
    if message.text not in ("👤 Обычный админ", "👑 Супер админ"):
        return
    await state.update_data(admin_type=message.text)
    await state.set_state(AddAdmin.waiting_id)
    await _send_menu(
        message, bot,
        f"➕ Введите *chat_id* нового {'супер-' if '👑' in message.text else ''}администратора:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🔙 Отмена")]],
            resize_keyboard=True
        )
    )


async def handle_add_admin_input(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    from admin_bot.config import SUPER_ADMIN_IDS
    if message.text == "🔙 Отмена":
        await state.clear()
        await _send_menu(message, bot, "⚙️ *Настройки*", reply_markup=settings_kb())
        return
    try:
        new_id = int((message.text or '').strip())
        data = await state.get_data()
        admin_type = data.get("admin_type", "👤 Обычный админ")

        if "👑" in admin_type:
            # Super admin
            if new_id in SUPER_ADMIN_IDS:
                await message.answer("⚠️ Уже является супер-администратором.")
            else:
                SUPER_ADMIN_IDS.append(new_id)
                if new_id not in ADMIN_CHAT_IDS:
                    ADMIN_CHAT_IDS.append(new_id)
                await _send_menu(message, bot, f"✅ Супер-администратор `{new_id}` добавлен.", reply_markup=settings_kb())
        else:
            # Oddiy admin
            if new_id in ADMIN_CHAT_IDS:
                await message.answer("⚠️ Уже является администратором.")
            else:
                ADMIN_CHAT_IDS.append(new_id)
                await _send_menu(message, bot, f"✅ Администратор `{new_id}` добавлен.", reply_markup=settings_kb())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректный chat_id или нажмите 🔙 Отмена.")


async def handle_remove_admin_start(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if not is_admin(message.from_user.id):
        return
    if not is_super_admin(message.from_user.id):
        await _send_menu(message, bot, "⛔ Только главный администратор может удалять админов.")
        return
    if len(ADMIN_CHAT_IDS) <= 1:
        await _send_menu(message, bot, "⚠️ Нельзя удалить единственного администратора.")
        return
    lines = [f"{i+1}. `{cid}`" for i, cid in enumerate(ADMIN_CHAT_IDS)]
    await state.set_state(RemoveAdmin.waiting_id)
    await _send_menu(
        message, bot,
        "➖ *Текущие администраторы:*\n\n" + "\n".join(lines) + "\n\nВведите *chat_id* для удаления:",
        reply_markup=cancel_kb()
    )


async def handle_remove_admin_input(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if message.text == "🔙 Отмена":
        await state.clear()
        await _send_menu(message, bot, "⚙️ *Настройки*", reply_markup=settings_kb())
        return
    try:
        rem_id = int((message.text or '').strip())
        if rem_id == ADMIN_CHAT_IDS[0]:
            await message.answer("⛔ Нельзя удалить главного администратора.")
        elif rem_id not in ADMIN_CHAT_IDS:
            await message.answer("❌ Такого администратора нет в списке.")
        else:
            ADMIN_CHAT_IDS.remove(rem_id)
            await _send_menu(message, bot, f"✅ Администратор `{rem_id}` удалён.", reply_markup=settings_kb())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректный chat_id или нажмите 🔙 Отмена.")


async def handle_debtors(message: types.Message, bot: Bot, **kwargs):
    if not is_admin(message.from_user.id):
        return
    await _delete_last_menu(bot, message.from_user.id)
    debtors = await fetch_top_debtors(200)
    if not debtors:
        await _send_menu(message, bot, "✅ Должников нет.", reply_markup=main_kb())
        return
    _debtors_cache[message.from_user.id] = debtors
    await _send_debtors_page(message, bot, debtors, page=0)


def _debtors_view(debtors: list, page: int) -> tuple[str, InlineKeyboardMarkup]:
    start = page * PAGE_SIZE
    end   = start + PAGE_SIZE
    total = len(debtors)
    lines = [
        f"{start + i + 1}. *{_esc(d['name'])}* — `{d['debt']:,.2f} $`"
        for i, d in enumerate(debtors[start:end])
    ]
    text = f"📋 *Должники ({start+1}–{min(end, total)} из {total}):*\n\n" + "\n".join(lines)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"debt_page:{page-1}"))
    if end < total:
        nav_row.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"debt_page:{page+1}"))
    buttons = [nav_row] if nav_row else []
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="debt_main_menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _send_debtors_page(message: types.Message, bot: Bot, debtors: list, page: int):
    chat_id = message.from_user.id
    text, kb = _debtors_view(debtors, page)
    await _delete_last_menu(bot, chat_id)
    sent = await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    _last_menu_msg[chat_id] = sent.message_id


async def handle_debtors_page_cb(callback: types.CallbackQuery, bot: Bot, **kwargs):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    debtors = _debtors_cache.get(callback.from_user.id)
    if not debtors or not 0 <= page * PAGE_SIZE < len(debtors):
        await callback.answer("Список устарел. Откройте заново.", show_alert=True)
        return
    text, kb = _debtors_view(debtors, page)
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception as e:
        logger.warning(f"debtors edit: {e}")
    await callback.answer()


async def handle_debt_main_menu_cb(callback: types.CallbackQuery, bot: Bot, **kwargs):
    """Inline 🏠 Главное меню bosilganda xabarni o'chirib bosh menyu chiqaradi."""
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    chat_id = callback.from_user.id
    # Qarzdorlik xabarini o'chiramiz
    try:
        await callback.message.delete()
    except Exception:
        pass
    _last_menu_msg.pop(chat_id, None)
    _debtors_cache.pop(chat_id, None)
    await callback.answer()
    await bot.send_message(chat_id=chat_id, text="🏠 Главное меню", reply_markup=main_kb())


async def handle_search_start(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(Search.waiting_query)
    await _send_menu(message, bot, "🔍 Введите имя клиента:", reply_markup=cancel_kb())


async def handle_search_input(message: types.Message, bot: Bot, state: FSMContext, **kwargs):
    if message.text == "🔙 Отмена":
        await state.clear()
        await _send_menu(message, bot, "🏠 Главное меню", reply_markup=main_kb())
        return
    query = (message.text or '').strip()
    if not query:
        await message.answer("❌ Введите имя клиента текстом или нажмите 🔙 Отмена.")
        return
    await state.clear()
    await _send_menu(message, bot, "⏳ Ищу...")
    results = await search_counterparty(query)
    if not results:
        await _send_menu(message, bot, "❌ Ничего не найдено.", reply_markup=main_kb())
        return
    lines = []
    for r in results:
        balance = await get_balance_async(r["id"])
        debt_str = f"`{balance:,.2f} $`" if balance > 0 else "✅ нет долга"
        lines.append(f"👤 *{_esc(r['name'])}*\n📞 {_esc(r['phone'])}\n💸 Задолженность: {debt_str}")
    await _send_menu(
        message, bot,
        "🔍 *Результаты:*\n\n" + "\n\n".join(lines),
        reply_markup=main_kb()
    )


async def handle_main_menu(message: types.Message, bot: Bot, **kwargs):
    if not is_admin(message.from_user.id):
        return
    await _send_menu(message, bot, "🏠 Главное меню", reply_markup=main_kb())


# ── Bot setup ─────────────────────────────────────────────────────────────────

def create_bot_and_dp():
    bot = Bot(token=ADMIN_BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())

    dp.message.register(cmd_start,                 CommandStart())
    dp.message.register(handle_status,             F.text == "📊 Статус")
    dp.message.register(handle_admins,             F.text == "👥 Администраторы")
    dp.message.register(handle_settings,           F.text == "⚙️ Настройки")
    dp.message.register(handle_back,               F.text == "🔙 Назад")
    dp.message.register(handle_main_menu,          F.text == "🏠 Главное меню")
    dp.message.register(handle_debtors,            F.text == "📋 Должники")
    dp.message.register(handle_search_start,       F.text == "🔍 Поиск")
    dp.message.register(handle_pause_resume,       F.text.in_({"⏸ Пауза", "▶️ Возобновить"}))
    dp.message.register(handle_interval_start,     F.text == "⏱ Интервал")
    dp.message.register(handle_add_admin_start,    F.text == "➕ Добавить админа")
    dp.message.register(handle_remove_admin_start, F.text == "➖ Удалить админа")

    dp.message.register(handle_interval_input,     SetInterval.waiting_seconds)
    dp.message.register(handle_add_admin_type,     AddAdmin.choosing_type)
    dp.message.register(handle_add_admin_input,    AddAdmin.waiting_id)
    dp.message.register(handle_remove_admin_input, RemoveAdmin.waiting_id)
    dp.message.register(handle_search_input,       Search.waiting_query)

    dp.callback_query.register(handle_debtors_page_cb,     F.data.startswith("debt_page:"))
    dp.callback_query.register(handle_debt_main_menu_cb,   F.data == "debt_main_menu")

    return bot, dp


async def run_bot():
    bot, dp = create_bot_and_dp()
    logger.info("🤖 Bot polling ishga tushdi")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()
