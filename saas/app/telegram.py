import json

import requests

from . import config


class TelegramError(Exception):
    """Telegram API xatosi; .description — Telegram qaytargan sabab (masalan "Bad Request: chat not found")."""

    def __init__(self, description: str, code: int = 0):
        super().__init__(description)
        self.description, self.code = description, code


# Telegram sababi -> mijozga tushunarli tushuntirish
_HINTS = [
    ("chat not found", "Chat topilmadi: ID noto'g'ri, yoki bot bu chat bilan hech qachon bog'lanmagan. "
                       "Eng oson yo'l: yuqoridagi havolani oching va Start bosing."),
    ("bot can't initiate conversation", "Bot siz bilan suhbat boshlay olmaydi: avval botga shaxsiy chatda /start yuboring."),
    ("bot was blocked", "Foydalanuvchi botni bloklagan. Blokdan chiqarib, /start yuboring."),
    ("not a member", "Bot bu guruh/kanalga qo'shilmagan. Botni qo'shing (kanalda administrator qilib)."),
    ("kicked", "Bot bu guruh/kanaldan chiqarib yuborilgan. Qayta qo'shing."),
    ("not enough rights", "Botda bu yerga yozish huquqi yo'q. Kanalda botni administrator (xabar yuborish huquqi bilan) qiling."),
    ("have no rights to send", "Botda bu yerga yozish huquqi yo'q. Kanalda botni administrator (xabar yuborish huquqi bilan) qiling."),
    ("need administrator rights", "Kanalda botni administrator (xabar yuborish huquqi bilan) qiling."),
]


def explain(err: Exception) -> str:
    """Xatoni foydalanuvchiga ko'rsatish uchun: tushuntirish + Telegramning asl sababi."""
    desc = getattr(err, "description", "") or str(err)
    low = desc.lower()
    for key, hint in _HINTS:
        if key in low:
            return f"{hint} (Telegram: {desc})"
    return f"Bot bu chatga yoza olmadi. (Telegram: {desc})"


def _call(method: str, **kw) -> dict:
    r = requests.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}", timeout=120, **kw)
    if r.status_code >= 400:
        try:
            body = r.json()
            raise TelegramError(body.get("description") or f"HTTP {r.status_code}", r.status_code)
        except ValueError:
            r.raise_for_status()
    return r.json()


def send_message(chat_id, text, markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup:
        data["reply_markup"] = json.dumps(markup)
    return _call("sendMessage", data=data)


def send_audio(chat_id, caption, audio: bytes, filename: str):
    return _call("sendAudio", data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                 files={"audio": (filename, audio, "audio/mpeg")})


def send_file(chat_id, caption, data: bytes, filename: str, mime: str, markup=None):
    """Chek: rasm bo'lsa sendPhoto, aks holda (PDF) sendDocument; ostida inline tugmalar."""
    method, field = ("sendPhoto", "photo") if mime.startswith("image/") else ("sendDocument", "document")
    form = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if markup:
        form["reply_markup"] = json.dumps(markup)
    return _call(method, data=form, files={field: (filename, data, mime)})


def answer_callback(cb_id, text):
    return _call("answerCallbackQuery", data={"callback_query_id": cb_id, "text": text})


def clear_buttons(chat_id, message_id):
    return _call("editMessageReplyMarkup", data={"chat_id": chat_id, "message_id": message_id,
                                                  "reply_markup": json.dumps({"inline_keyboard": []})})


def set_webhook():
    url = f"{config.BASE_URL}/tg/{config.TG_WEBHOOK_SECRET}"
    return _call("setWebhook", data={"url": url, "allowed_updates": json.dumps(["message", "channel_post", "callback_query"])})
