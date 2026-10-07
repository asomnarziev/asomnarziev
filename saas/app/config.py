import os

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./saas.db")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
TG_WEBHOOK_SECRET = os.environ.get("TG_WEBHOOK_SECRET", "tg-secret")
# Kirish sahifasida ko'rsatiladigan administrator kontaktlari
CONTACT_TELEGRAM = os.environ.get("CONTACT_TELEGRAM", "asom_narziev")
CONTACT_PHONE = os.environ.get("CONTACT_PHONE", "+998948302407")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "asomnarziev07@gmail.com")
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))

# narx so'mda; days — obuna muddati
PLANS = {
    "start": {"name": "Start", "price": 99_000, "days": 30, "max_chats": 3},
    "pro": {"name": "Pro", "price": 249_000, "days": 30, "max_chats": 20},
}
TRIAL_MAX_CHATS = 3

if BASE_URL.startswith("https") and (SECRET_KEY == "dev-secret-change-me" or TG_WEBHOOK_SECRET == "tg-secret"):
    raise RuntimeError("Productionda SECRET_KEY va TG_WEBHOOK_SECRET o'rnatilishi shart")

# Bitta mijoz uchun kuniga ko'pi bilan shuncha qo'ng'iroq qayta ishlanadi (suiiste'mol va API limitidan himoya)
MAX_CALLS_PER_DAY = int(os.environ.get("MAX_CALLS_PER_DAY", "5000"))

# Kartaga o'tkazma orqali to'lov
PAYMENT_CARD = os.environ.get("PAYMENT_CARD", "").replace(" ", "")          # karta raqami
PAYMENT_CARD_HOLDER = os.environ.get("PAYMENT_CARD_HOLDER", "")             # karta egasi
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()                 # to'lov eslatmalari keladigan shaxsiy chat ID (botga /id yozing)
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")                        # cheklar saqlanadigan papka (ishchi papkaga nisbatan)
MAX_RECEIPT_MB = int(os.environ.get("MAX_RECEIPT_MB", "8"))
MAX_PENDING_PAYMENTS = 3                                                    # bitta mijozning kutilayotgan to'lovlari
