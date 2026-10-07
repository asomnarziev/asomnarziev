"""To'lov cheklarini xavfsiz saqlash: tur fayl mazmuniga qarab tekshiriladi (nomiga/Content-Type'ga ishonilmaydi)."""
import secrets
from pathlib import Path

from . import config

SIGNATURES = [  # (boshlanish baytlari, kengaytma, mime)
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"%PDF-", "pdf", "application/pdf"),
]
MIME_BY_EXT = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "pdf": "application/pdf"}


class ReceiptError(ValueError):
    pass


def detect(data: bytes):
    for sig, ext, mime in SIGNATURES:
        if data.startswith(sig):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    raise ReceiptError("Faqat JPG, PNG, WEBP yoki PDF chek yuklash mumkin")


def upload_dir() -> Path:
    d = Path(config.UPLOAD_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(data: bytes) -> str:
    if not data:
        raise ReceiptError("Chek fayli tanlanmagan")
    if len(data) > config.MAX_RECEIPT_MB * 1024 * 1024:
        raise ReceiptError(f"Fayl {config.MAX_RECEIPT_MB} MB dan katta")
    ext, _ = detect(data)
    name = f"{secrets.token_urlsafe(18)}.{ext}"  # taxmin qilib bo'lmaydigan nom
    (upload_dir() / name).write_bytes(data)
    return name


def path(name: str) -> Path:
    p = (upload_dir() / name).resolve()
    if p.parent != upload_dir().resolve() or not p.is_file():  # path traversal'dan himoya
        raise FileNotFoundError(name)
    return p


def mime(name: str) -> str:
    return MIME_BY_EXT.get(name.rsplit(".", 1)[-1], "application/octet-stream")
