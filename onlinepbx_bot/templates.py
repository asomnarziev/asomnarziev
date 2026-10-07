"""Xabar shablonlari (uz / ru). Yangi til qo'shish uchun TEMPLATES ga kalit qo'shing."""

LANGS = {"uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Русский"}
DEFAULT_LANG = "uz"

TEMPLATES = {
    "uz": {
        "call": (
            "📞 <b>Qo'ng'iroq yozuvi</b>\n"
            "Yo'nalish: {direction}\n"
            "Kimdan: <code>{caller}</code>\n"
            "Kimga: <code>{callee}</code>\n"
            "Sana: {date}\n"
            "Davomiyligi: {duration}"
        ),
        "direction": {"inbound": "⬇️ Kiruvchi", "outbound": "⬆️ Chiquvchi", "local": "🔁 Ichki"},
        "unknown": "Noma'lum",
        "start": "Tilni tanlang / Выберите язык:",
        "lang_set": "✅ Til o'rnatildi: O'zbekcha",
        "no_record": "Yozuv topilmadi",
        "unauthorized": "⛔ Bu chat ruxsat etilmagan. Chat ID: <code>{chat_id}</code>",
    },
    "ru": {
        "call": (
            "📞 <b>Запись звонка</b>\n"
            "Направление: {direction}\n"
            "От: <code>{caller}</code>\n"
            "Кому: <code>{callee}</code>\n"
            "Дата: {date}\n"
            "Длительность: {duration}"
        ),
        "direction": {"inbound": "⬇️ Входящий", "outbound": "⬆️ Исходящий", "local": "🔁 Внутренний"},
        "unknown": "Неизвестно",
        "start": "Tilni tanlang / Выберите язык:",
        "lang_set": "✅ Язык установлен: Русский",
        "no_record": "Запись не найдена",
        "unauthorized": "⛔ Чат не разрешён. Chat ID: <code>{chat_id}</code>",
    },
}


def fmt_duration(seconds: int) -> str:
    m, s = divmod(int(seconds or 0), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_call(lang: str, call: dict) -> str:
    t = TEMPLATES.get(lang) or TEMPLATES[DEFAULT_LANG]
    from datetime import datetime

    ts = call.get("start_stamp")
    date = datetime.fromtimestamp(int(ts)).strftime("%d.%m.%Y %H:%M:%S") if ts else t["unknown"]
    return t["call"].format(
        direction=t["direction"].get(call.get("accountcode"), t["unknown"]),
        caller=call.get("caller_id_number") or t["unknown"],
        callee=call.get("destination_number") or t["unknown"],
        date=date,
        duration=fmt_duration(call.get("duration", 0)),
    )
