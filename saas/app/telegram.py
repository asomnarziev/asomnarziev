import json

import requests

from . import config


def _call(method: str, **kw) -> dict:
    r = requests.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}", timeout=120, **kw)
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


def answer_callback(cb_id, text):
    return _call("answerCallbackQuery", data={"callback_query_id": cb_id, "text": text})


def set_webhook():
    url = f"{config.BASE_URL}/tg/{config.TG_WEBHOOK_SECRET}"
    return _call("setWebhook", data={"url": url, "allowed_updates": json.dumps(["message", "callback_query"])})
