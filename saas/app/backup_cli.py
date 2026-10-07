"""Zaxirani adminning Telegramiga shifrlab yuborish va shifrni ochish. `deploy/backup.sh` shuni chaqiradi.

    python -m app.backup_cli telegram FAYL [--state YO'L]   shifrlaydi, Telegramga yuboradi, kechagi xabarni o'chiradi
    python -m app.backup_cli decrypt KIRISH CHIQISH         shifrni ochadi (parol: BACKUP_PASSPHRASE yoki so'raladi)

Zaxirada mijozlar emaillari va qo'ng'iroq raqamlari bor, shuning uchun Telegramga FAQAT shifrlangan holda yuboriladi.
Shifr: scrypt (parol -> kalit) + Fernet (AES-128-CBC + HMAC): fayl o'zgartirilsa yoki parol noto'g'ri bo'lsa, ochilmaydi.
"""

import argparse
import base64
import getpass
import hashlib
import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

MAGIC = b"PBXBAK1"
SALT_LEN = 16
MAX_TELEGRAM_BYTES = 49 * 1024 * 1024  # Bot API fayl yuborish chegarasi 50 MB

# Chiqish kodlari (backup.sh log uchun)
OK, FAILED, NOT_CONFIGURED, NO_PASSPHRASE, TOO_BIG = 0, 1, 2, 3, 4


class BackupError(Exception):
    pass


def load_env(path: str | os.PathLike) -> None:
    """`.env` ni o'qib, muhitda yo'q o'zgaruvchilarni o'rnatadi (shell'siz: maxsus belgilar xavfsiz)."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _key(passphrase: str, salt: bytes) -> bytes:
    raw = hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return base64.urlsafe_b64encode(raw)


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    salt = os.urandom(SALT_LEN)
    return MAGIC + salt + Fernet(_key(passphrase, salt)).encrypt(data)


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + SALT_LEN + 1:
        raise BackupError("Bu shifrlangan zaxira fayli emas")
    salt = blob[len(MAGIC) : len(MAGIC) + SALT_LEN]
    try:
        return Fernet(_key(passphrase, salt)).decrypt(blob[len(MAGIC) + SALT_LEN :])
    except InvalidToken:
        raise BackupError("Parol noto'g'ri yoki fayl shikastlangan") from None


def _size(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _stats() -> str:
    """Xabar uchun qisqa statistika (baza ishlamasa, shunchaki tushirib qoldiriladi)."""
    try:
        from .db import SessionLocal
        from .models import CallLog, Payment, User

        with SessionLocal() as db:
            users, calls, payments = (db.query(m).count() for m in (User, CallLog, Payment))
            return f"Mijozlar: {users} · qo'ng'iroqlar: {calls} · to'lovlar: {payments}"
    except Exception:
        return ""


def _read_state(path: Path | None) -> dict:
    try:
        return json.loads(path.read_text()) if path and path.is_file() else {}
    except (OSError, ValueError):
        return {}


def send_backup(file: str | os.PathLike, state_path: str | os.PathLike | None = None, now: datetime | None = None) -> int:
    """Zaxirani shifrlab adminga yuboradi; muvaffaqiyatli bo'lsa, oldingi yuborilgan xabarni o'chiradi."""
    from . import config, telegram
    from .messages import LOCAL_TZ

    chat_id, passphrase = config.ADMIN_CHAT_ID, os.environ.get("BACKUP_PASSPHRASE", "")
    if not (chat_id and config.BOT_TOKEN):
        print("OGOHLANTIRISH: ADMIN_CHAT_ID yoki BOT_TOKEN sozlanmagan, Telegramga yuborilmadi", file=sys.stderr)
        return NOT_CONFIGURED

    def warn(text: str) -> None:
        try:
            telegram.send_message(chat_id, f"⚠️ <b>Zaxira Telegramga yuborilmadi</b>\n{html.escape(text)}")
        except Exception:
            print("OGOHLANTIRISH: adminga xabar ham yuborib bo'lmadi", file=sys.stderr)

    if not passphrase:
        warn(
            "BACKUP_PASSPHRASE .env da yo'q. Shifrlanmagan zaxira Telegramga yuborilmaydi (mijozlar ma'lumoti bor). "
            "Parol qo'shing: openssl rand -base64 24"
        )
        return NO_PASSPHRASE

    path, state = Path(file), Path(state_path) if state_path else None
    raw = path.read_bytes()
    blob = encrypt_bytes(raw, passphrase)
    if len(blob) > MAX_TELEGRAM_BYTES:
        warn(f"Zaxira juda katta ({_size(len(blob))}), Telegram chegarasi 50 MB. Serverdagi nusxa saqlangan: {path.name}")
        return TOO_BIG

    stamp = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    caption = "\n".join(
        line
        for line in (
            f"💾 <b>Zaxira</b> · {stamp:%d.%m.%Y %H:%M}",
            f"Baza: {_size(len(raw))} (shifrlangan: {_size(len(blob))})",
            _stats(),
            "Ochish: <code>python -m app.backup_cli decrypt FAYL.enc CHIQISH.sql.gz</code>",
        )
        if line
    )
    try:
        message_id = telegram.send_document(chat_id, blob, f"pbx_{path.stem}.gz.enc", caption)
    except Exception as e:
        print(f"XATO: Telegramga yuborilmadi: {e}", file=sys.stderr)
        return FAILED

    previous = _read_state(state)  # kechagi xabar faqat YANGISI yetib borgandan keyin o'chiriladi
    if previous.get("chat_id") == str(chat_id) and previous.get("message_id"):
        try:
            telegram.delete_message(chat_id, previous["message_id"])
        except Exception:
            print("OGOHLANTIRISH: kechagi xabarni o'chirib bo'lmadi (48 soatdan o'tgan bo'lishi mumkin)", file=sys.stderr)
    if state:
        state.write_text(json.dumps({"chat_id": str(chat_id), "message_id": message_id, "sent_at": stamp.isoformat()}))
        state.chmod(0o600)
    print(f"OK: Telegramga yuborildi ({_size(len(blob))}), xabar #{message_id}")
    return OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.backup_cli", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("telegram", help="shifrlab Telegramga yuborish")
    t.add_argument("file")
    t.add_argument("--state", help="oxirgi yuborilgan xabar ID saqlanadigan fayl")
    d = sub.add_parser("decrypt", help="shifrni ochish")
    d.add_argument("src")
    d.add_argument("dst")
    args = parser.parse_args(argv)

    load_env(os.environ.get("ENV_FILE", ".env"))
    if args.cmd == "telegram":
        return send_backup(args.file, args.state)
    passphrase = os.environ.get("BACKUP_PASSPHRASE") or getpass.getpass("Zaxira paroli: ")
    try:
        Path(args.dst).write_bytes(decrypt_bytes(Path(args.src).read_bytes(), passphrase))
    except BackupError as e:
        print(f"XATO: {e}", file=sys.stderr)
        return FAILED
    print(f"OK: {args.dst}")
    return OK


if __name__ == "__main__":
    sys.exit(main())
