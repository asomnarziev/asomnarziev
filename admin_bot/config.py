import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
ADMIN_BOT_TOKEN: str = os.getenv("ADMIN_BOT_TOKEN", "")

_raw = os.getenv("ADMIN_CHAT_IDS", "")
ADMIN_CHAT_IDS: list[int] = [
    int(x.strip()) for x in _raw.split(",")
    if x.strip().lstrip("-").isdigit()
]

# Super adminlar — barcha xabarlar keladi (supply ham)
_raw_super = os.getenv("SUPER_ADMIN_IDS", "")
if _raw_super.strip():
    SUPER_ADMIN_IDS: list[int] = [
        int(x.strip()) for x in _raw_super.split(",")
        if x.strip().lstrip("-").isdigit()
    ]
else:
    SUPER_ADMIN_IDS = ADMIN_CHAT_IDS[:1]

# Super adminlar avtomatik ADMIN_CHAT_IDS ga ham qo'shiladi
for _sid in SUPER_ADMIN_IDS:
    if _sid not in ADMIN_CHAT_IDS:
        ADMIN_CHAT_IDS.append(_sid)

# ── MoySklad ──────────────────────────────────────────────────────────────────
MOYSKLAD_TOKEN: str = os.getenv("MOYSKLAD_TOKEN", "")
MS_BASE_URL = "https://api.moysklad.ru/api/remap/1.2"
MS_HEADERS: dict = {
    "Authorization": f"Bearer {MOYSKLAD_TOKEN}",
    "Accept": "application/json;charset=utf-8",
}

# ── SQLite — mavjud loyihaning db.sqlite3 ─────────────────────────────────────
DB_PATH = BASE_DIR.parent / "db.sqlite3"

# ── Polling ───────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 120  # har 2 daqiqada
