import os

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./saas.db")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
TG_WEBHOOK_SECRET = os.environ.get("TG_WEBHOOK_SECRET", "tg-secret")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").lower()
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))

# narx so'mda; days — obuna muddati
PLANS = {
    "start": {"name": "Start", "price": 99_000, "days": 30, "max_chats": 3},
    "pro": {"name": "Pro", "price": 249_000, "days": 30, "max_chats": 20},
}
TRIAL_MAX_CHATS = 3

if BASE_URL.startswith("https") and (SECRET_KEY == "dev-secret-change-me" or TG_WEBHOOK_SECRET == "tg-secret"):
    raise RuntimeError("Productionda SECRET_KEY va TG_WEBHOOK_SECRET o'rnatilishi shart")
