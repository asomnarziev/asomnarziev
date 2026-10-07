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
        "linked": "✅ Chat ulandi. Tilni kabinetdan o'zgartirishingiz mumkin (hozir: O'zbekcha).",
        "bad_code": "⛔ Kod noto'g'ri. Kabinetdagi havoladan foydalaning.",
        "limit": "⚠️ Chatlar limiti tugagan. Tarifni yangilang.",
        "missed": (
            "📵 <b>Javobsiz qo'ng'iroq</b>\n"
            "Kimdan: <code>{caller}</code>\n"
            "Kimga: <code>{callee}</code>\n"
            "Sana: {date}\n"
            "Jiringlagan: {duration}"
        ),
        "pay_ok": "✅ To'lovingiz tasdiqlandi. Obuna {until} gacha faol.",
        "pay_no": "❌ To'lov tasdiqlanmadi. Sababni kabinetdagi «Tarif» sahifasida ko'ring.",
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
        "linked": "✅ Chat ulandi / Чат подключён. Язык меняется в кабинете.",
        "bad_code": "⛔ Неверный код. Используйте ссылку из кабинета.",
        "limit": "⚠️ Лимит чатов исчерпан. Обновите тариф.",
        "missed": (
            "📵 <b>Пропущенный звонок</b>\n"
            "От: <code>{caller}</code>\n"
            "Кому: <code>{callee}</code>\n"
            "Дата: {date}\n"
            "Звонил: {duration}"
        ),
        "pay_ok": "✅ Ваш платёж подтверждён. Подписка активна до {until}.",
        "pay_no": "❌ Платёж не подтверждён. Причину смотрите в кабинете на странице «Тариф».",
    },
}


def fmt_duration(seconds: int) -> str:
    m, s = divmod(int(seconds or 0), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


import os
from html import escape
from datetime import datetime, timedelta, timezone

# Server UTC'da ishlaydi; xabarlarda mijozning mahalliy vaqti ko'rsatiladi (Toshkent = UTC+5)
LOCAL_TZ = timezone(timedelta(hours=float(os.environ.get("TZ_OFFSET_HOURS", "5"))))


def local_time(ts):
    return datetime.fromtimestamp(int(ts), LOCAL_TZ) if ts else None


def audio_name(call: dict) -> str:
    """105_935033635_07.10_12-30.mp3 ko'rinishidagi fayl nomi (topilmasa uuid)."""
    dt = local_time(call.get("start_stamp"))
    parts = [call.get("caller_id_number"), call.get("destination_number"), dt and dt.strftime("%d.%m_%H-%M")]
    name = "_".join(str(p) for p in parts if p) or call.get("uuid", "call")
    return "".join(c for c in name if c.isalnum() or c in "._-+") + ".mp3"


def render_call(lang: str, call: dict) -> str:
    t = TEMPLATES.get(lang) or TEMPLATES[DEFAULT_LANG]
    dt = local_time(call.get("start_stamp"))
    return t["call"].format(
        direction=t["direction"].get(call.get("accountcode"), t["unknown"]),
        caller=escape(str(call.get("caller_id_number") or t["unknown"])),
        callee=escape(str(call.get("destination_number") or t["unknown"])),
        date=dt.strftime("%d.%m.%Y %H:%M:%S") if dt else t["unknown"],
        duration=fmt_duration(call.get("duration", 0)),
    )


def render_missed(lang: str, call: dict) -> str:
    t = TEMPLATES.get(lang) or TEMPLATES[DEFAULT_LANG]
    dt = local_time(call.get("start_stamp"))
    return t["missed"].format(
        caller=escape(str(call.get("caller_id_number") or t["unknown"])),
        callee=escape(str(call.get("destination_number") or t["unknown"])),
        date=dt.strftime("%d.%m.%Y %H:%M:%S") if dt else t["unknown"],
        duration=fmt_duration(call.get("duration", 0)),
    )
