"""Sozlamalar: hammasi muhit o'zgaruvchilaridan (`.env`), tarif jadvali esa shu yerda."""

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} butun son bo'lishi kerak, hozir: {raw!r}") from None


def _flag(name: str, default: bool = True) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


# ---------- Asosiy ----------
# Dev standartlari: HTTPS (production) rejimida ishlatilsa ilova ishga tushmaydi (pastdagi tekshiruv)
DEV_SECRET_KEY = "dev-secret-change-me"  # noqa: S105  # nosec B105
DEV_WEBHOOK_SECRET = "tg-secret"  # noqa: S105  # nosec B105
SECRET_KEY = _env("SECRET_KEY", DEV_SECRET_KEY)  # sessiya imzosi va OnlinePBX kalitlarini shifrlash uchun
DATABASE_URL = _env("DATABASE_URL", "sqlite:///./saas.db")
BASE_URL = _env("BASE_URL", "http://localhost:8000").rstrip("/")
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
# Fon rejalashtiruvchi (kunlik hisobot). Faqat bitta uvicorn worker bilan ishlating; testlarda SCHEDULER=0
SCHEDULER = _flag("SCHEDULER")

# ---------- Telegram ----------
BOT_TOKEN = _env("BOT_TOKEN")
BOT_USERNAME = _env("BOT_USERNAME")
TG_WEBHOOK_SECRET = _env("TG_WEBHOOK_SECRET", DEV_WEBHOOK_SECRET)
ADMIN_CHAT_ID = _env("ADMIN_CHAT_ID").strip()  # to'lov eslatmalari keladigan shaxsiy chat ID (botga /id yozing)

if BASE_URL.startswith("https") and (SECRET_KEY == DEV_SECRET_KEY or TG_WEBHOOK_SECRET == DEV_WEBHOOK_SECRET):
    raise RuntimeError("Productionda SECRET_KEY va TG_WEBHOOK_SECRET o'rnatilishi shart")

# ---------- Kirish sahifasida ko'rsatiladigan administrator kontaktlari ----------
CONTACT_TELEGRAM = _env("CONTACT_TELEGRAM", "asom_narziev")
CONTACT_PHONE = _env("CONTACT_PHONE", "+998948302407")
CONTACT_EMAIL = _env("CONTACT_EMAIL", "asomnarziev07@gmail.com")

# ---------- Tariflar ----------
# narx so'mda; days — obuna muddati; features — tarifga ochiladigan imkoniyatlar; perks — tarif kartasida ko'rsatiladigan ro'yxat
TRIAL_DAYS = _int("TRIAL_DAYS", 7)
TRIAL_MAX_CHATS = 3
PLANS = {
    "start": {
        "name": "Start",
        "price": 99_000,
        "days": 30,
        "max_chats": 3,
        "features": (),
        "perks": ["3 tagacha chat", "O'zbekcha va ruscha shablonlar", "Qo'ng'iroq yozuvlari Telegramga"],
    },
    "pro": {
        "name": "Pro",
        "price": 249_000,
        "days": 30,
        "max_chats": 20,
        "features": ("reports", "missed_alerts", "search"),
        "perks": [
            "20 tagacha chat",
            "O'zbekcha va ruscha shablonlar",
            "Qo'ng'iroq yozuvlari Telegramga",
            "Hisobotlar: kunlar, soatlar, xodimlar bo'yicha statistika",
            "Hisobotni CSV (Excel) ga yuklab olish",
            "Kunlik hisobot Telegramga (har kuni ertalab)",
            "Javobsiz qo'ng'iroq ogohlantirishi (darrov Telegramga)",
            "Qo'ng'iroqlarni qidirish va yozuvni qayta tinglash",
        ],
    },
}

# ---------- Kartaga o'tkazma orqali to'lov ----------
PAYMENT_CARD = _env("PAYMENT_CARD").replace(" ", "")  # karta raqami (bo'sh joy bilan yozsa ham bo'ladi)
PAYMENT_CARD_HOLDER = _env("PAYMENT_CARD_HOLDER")
UPLOAD_DIR = _env("UPLOAD_DIR", "uploads")  # cheklar saqlanadigan papka (ishchi papkaga nisbatan)
MAX_RECEIPT_MB = _int("MAX_RECEIPT_MB", 8)
MAX_PENDING_PAYMENTS = 3  # bitta mijozning kutilayotgan to'lovlari

# ---------- Limitlar (suiiste'mol va OnlinePBX API limitidan himoya) ----------
MAX_CALLS_PER_DAY = _int("MAX_CALLS_PER_DAY", 5000)  # bitta mijoz uchun kuniga qayta ishlanadigan qo'ng'iroqlar
MAX_RECORD_FETCH_PER_HOUR = _int("MAX_RECORD_FETCH_PER_HOUR", 60)  # kabinetdan yozuvni qayta olish

# ---------- Bazani panel orqali tiklash (xavfli amal: standart holatda o'chiq) ----------
ALLOW_PANEL_RESTORE = _flag("ALLOW_PANEL_RESTORE", False)
RESTORE_DIR = _env("RESTORE_DIR", "restore_data")  # yuklangan zaxira va tiklashdan oldingi avtomatik nusxalar
RESTORE_KEEP_SNAPSHOTS = _int("RESTORE_KEEP_SNAPSHOTS", 3)
