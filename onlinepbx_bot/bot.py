"""OnlinePBX -> Telegram: yangi qo'ng'iroq yozuvlarini shablon bo'yicha, chat tiliga qarab yuboradi.

Ishga tushirish:  python bot.py   (sozlamalar .env / muhit o'zgaruvchilarida)
"""
import json
import logging
import os
import time
from pathlib import Path

import requests

from templates import DEFAULT_LANG, LANGS, TEMPLATES, render_call

log = logging.getLogger("onlinepbx_bot")

PBX_DOMAIN = os.environ["PBX_DOMAIN"]            # masalan: company.onpbx.ru
PBX_AUTH_KEY = os.environ["PBX_AUTH_KEY"]        # OnlinePBX -> Sozlamalar -> Integratsiya -> API
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# "123456:uz,-100987:ru"  (til ko'rsatilmasa DEFAULT_LANG)
CHATS_ENV = os.environ["TELEGRAM_CHAT_IDS"]
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))

PBX_URL = f"https://api.onlinepbx.ru/{PBX_DOMAIN}"
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"chats": {}, "last_ts": int(time.time()), "tg_offset": 0}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def init_chats(state: dict) -> None:
    """Muhitdagi chat ID larni state ga qo'shadi (state'dagi tanlangan til ustun)."""
    for item in CHATS_ENV.split(","):
        chat_id, _, lang = item.strip().partition(":")
        if chat_id:
            state["chats"].setdefault(chat_id, lang if lang in LANGS else DEFAULT_LANG)


# ---------- OnlinePBX ----------
class Pbx:
    def __init__(self):
        self.s = requests.Session()
        self.key = None

    def _auth(self):
        r = self.s.post(f"{PBX_URL}/auth.json", data={"auth_key": PBX_AUTH_KEY, "new": "true"}, timeout=30)
        r.raise_for_status()
        d = r.json()["data"]
        self.key = f"{d['key_id']}:{d['key']}"

    def _post(self, path: str, data: dict) -> dict:
        for attempt in (1, 2):
            if not self.key:
                self._auth()
            r = self.s.post(f"{PBX_URL}/{path}", data=data, headers={"x-pbx-authentication": self.key}, timeout=60)
            if r.status_code in (401, 403) and attempt == 1:
                self.key = None
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("OnlinePBX auth failed")

    def calls_since(self, ts: int) -> list:
        res = self._post("mongo_history/search.json", {"start_stamp_from": ts, "start_stamp_to": int(time.time())})
        return res.get("data") or []

    def record_url(self, uuid: str) -> str | None:
        res = self._post("mongo_history/search.json", {"uuid": uuid, "download": 1})
        data = res.get("data")
        if isinstance(data, list) and data:
            return data[0] if isinstance(data[0], str) else None
        return data if isinstance(data, str) else None


# ---------- Telegram ----------
def tg(method: str, **kw) -> dict:
    r = requests.post(f"{TG_URL}/{method}", timeout=120, **kw)
    r.raise_for_status()
    return r.json()


def send_call(chat_id: str, lang: str, call: dict, audio: bytes | None) -> None:
    caption = render_call(lang, call)
    if audio is None:
        tg("sendMessage", data={"chat_id": chat_id, "text": caption + "\n\n" + TEMPLATES[lang]["no_record"], "parse_mode": "HTML"})
        return
    tg("sendAudio", data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
       files={"audio": (f"{call.get('uuid', 'call')}.mp3", audio, "audio/mpeg")})


def handle_updates(state: dict) -> None:
    """/start va /lang -> til tanlash tugmalari; tugma bosilganda chat tili saqlanadi."""
    res = tg("getUpdates", data={"offset": state["tg_offset"], "timeout": 0})
    for u in res.get("result", []):
        state["tg_offset"] = u["update_id"] + 1
        msg, cb = u.get("message"), u.get("callback_query")
        if msg and msg.get("text", "").split("@")[0] in ("/start", "/lang"):
            chat_id = str(msg["chat"]["id"])
            if chat_id not in state["chats"]:
                tg("sendMessage", data={"chat_id": chat_id, "parse_mode": "HTML",
                                        "text": TEMPLATES[DEFAULT_LANG]["unauthorized"].format(chat_id=chat_id)})
                continue
            kb = {"inline_keyboard": [[{"text": n, "callback_data": f"lang:{c}"} for c, n in LANGS.items()]]}
            tg("sendMessage", data={"chat_id": chat_id, "text": TEMPLATES[DEFAULT_LANG]["start"],
                                    "reply_markup": json.dumps(kb)})
        elif cb and cb.get("data", "").startswith("lang:"):
            chat_id, lang = str(cb["message"]["chat"]["id"]), cb["data"][5:]
            if chat_id in state["chats"] and lang in LANGS:
                state["chats"][chat_id] = lang
                tg("answerCallbackQuery", data={"callback_query_id": cb["id"], "text": TEMPLATES[lang]["lang_set"]})
                tg("sendMessage", data={"chat_id": chat_id, "text": TEMPLATES[lang]["lang_set"]})


def process_calls(pbx: Pbx, state: dict) -> None:
    calls = sorted(pbx.calls_since(state["last_ts"]), key=lambda c: int(c.get("start_stamp", 0)))
    for call in calls:
        # faqat tugagan va suhbat bo'lgan qo'ng'iroqlar
        if not call.get("uuid") or int(call.get("user_talk_time") or 0) <= 0:
            state["last_ts"] = max(state["last_ts"], int(call.get("start_stamp", 0)) + 1)
            continue
        audio = None
        url = pbx.record_url(call["uuid"])
        if url:
            audio = requests.get(url, timeout=120).content
        for chat_id, lang in state["chats"].items():
            send_call(chat_id, lang, call, audio)
        state["last_ts"] = max(state["last_ts"], int(call.get("start_stamp", 0)) + 1)
        save_state(state)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = load_state()
    init_chats(state)
    pbx = Pbx()
    while True:
        try:
            handle_updates(state)
            process_calls(pbx, state)
        except Exception:
            log.exception("sikl xatosi")
        save_state(state)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
