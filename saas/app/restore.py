"""Bazani zaxiradan tiklash (administrator paneli uchun).

Xavfsizlik va ishonchlilik tamoyillari:
  * Yoqilishi ixtiyoriy (`ALLOW_PANEL_RESTORE=1`), faqat administrator va uning paroli bilan.
  * Zaxira yuklanganda AVVAL tekshiriladi (shifr, to'liqlik, faqat ruxsat etilgan SQL buyruqlari, kerakli jadvallar);
    tekshiruvdan o'tmagan fayl hech qachon bazaga yuborilmaydi.
  * Tiklashdan oldin joriy bazaning avtomatik nusxasi olinadi (xohlasa orqaga qaytarish mumkin).
  * Almashtirish BITTA tranzaksiyada: xato bo'lsa hamma narsa o'z holiga qaytadi (baza o'zgarmaydi).
  * Jarayon davomida sayt "texnik ishlar" rejimida (503), fon rejalashtiruvchi to'xtatiladi.
"""

import contextlib
import gzip
import logging
import os
import re
import secrets
import shutil
import subprocess  # nosec B404
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import make_url

from . import config
from .backup_cli import BackupError, decrypt_bytes

log = logging.getLogger(__name__)

REQUIRED_TABLES = ("users", "accounts", "chats", "call_logs", "payments")
MAX_DUMP_BYTES = 512 * 1024 * 1024  # ochilgan holatda (gzip-bomba himoyasi)
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
PENDING_TTL = 1800  # yuklangan, lekin tasdiqlanmagan fayl 30 daqiqadan keyin o'chadi
RESTORE_TIMEOUT = 900
CONFIRM_PHRASE = "TIKLASH"


class RestoreError(Exception):
    pass


# ---------------------------------------------------------------- holat (jarayon xotirasida)
@dataclass
class State:
    phase: str = "idle"  # idle | snapshot | restoring | done | error
    message: str = ""
    token: str = ""  # holat sahifasi uchun (baza ishlamayotganda ham tekshiriladi)
    started: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def claim(self, token: str) -> bool:
        with self.lock:
            if self.phase in ("snapshot", "restoring"):
                return False
            self.phase, self.message = "snapshot", "Joriy bazaning nusxasi olinmoqda"
            self.token, self.started = token, time.time()
            return True

    def set(self, phase: str, message: str) -> None:
        with self.lock:
            self.phase, self.message = phase, message


STATE = State()


def is_maintenance() -> bool:
    return STATE.phase in ("snapshot", "restoring")


def enabled() -> bool:
    return config.ALLOW_PANEL_RESTORE


def work_dir() -> Path:
    d = Path(config.RESTORE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


# ---------------------------------------------------------------- PostgreSQL bilan ishlash
def pg_env(url: str | None = None) -> dict[str, str]:
    """psql/pg_dump uchun muhit (parol buyruq qatorida emas, PGPASSWORD orqali: `ps` da ko'rinmasin)."""
    u = make_url(url or config.DATABASE_URL)
    if not u.drivername.startswith("postgresql"):
        raise RestoreError("Tiklash faqat PostgreSQL bazasida ishlaydi")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "HOME")}
    env.update(PGHOST=u.host or "localhost", PGPORT=str(u.port or 5432), PGUSER=u.username or "", PGDATABASE=u.database or "")
    if u.password:
        env["PGPASSWORD"] = u.password
    return env


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RestoreError(f"Serverda `{name}` topilmadi (postgresql-client o'rnating)")
    return path


# ---------------------------------------------------------------- zaxirani tekshirish
RESTRICT_RE = re.compile(r"^\\(restrict|unrestrict) [A-Za-z0-9]+$")  # PostgreSQL 16.10+/17.6+ dump'ida bor
COPY_RE = re.compile(r"^COPY public\.(\w+) \(([^)]*)\) FROM stdin;$")
CREATE_TABLE_RE = re.compile(r"^CREATE TABLE public\.(\w+) \(")
# Faqat shu turdagi buyruqlarga ruxsat (jadval, indeks, ketma-ketlik, ma'lumot): funksiya, trigger, rol, kengaytma va h.k. yo'q
ALLOWED_STATEMENT_RE = re.compile(
    r"^(SET\b"
    r"|SELECT pg_catalog\.(setval|set_config)\("
    r"|CREATE (TABLE|SEQUENCE|INDEX|UNIQUE INDEX) "
    r"|ALTER (TABLE|SEQUENCE) "
    r"|COMMENT ON (TABLE|COLUMN|SEQUENCE|INDEX) |COMMENT ON SCHEMA public IS "
    r"|COPY public\.\w+ \()",
)
OWNER_RE = re.compile(r"^ALTER (TABLE|SEQUENCE) \S+ OWNER TO \S+;$")
BAD_IN_ALTER_RE = re.compile(
    r"\b(PROGRAM|SECURITY\s+LABEL|SET\s+SCHEMA|RENAME\s+TO|ENABLE\s+(ALWAYS\s+|REPLICA\s+)?TRIGGER)\b", re.I
)


@dataclass
class DumpInfo:
    size: int = 0
    pg_version: str = ""
    rows: dict[str, int] = field(default_factory=dict)
    keys_total: int = 0
    keys_ok: int = 0  # joriy SECRET_KEY bilan ochiladigan OnlinePBX kalitlar soni


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8", newline="\n")


def _check_statement(stmt: str) -> None:
    stmt = stmt.strip()
    if not ALLOWED_STATEMENT_RE.match(stmt):
        raise RestoreError(f"Zaxirada ruxsat etilmagan SQL buyrug'i: {stmt[:60]!r}")
    if stmt.startswith("COPY") and not COPY_RE.match(stmt):  # faqat `FROM stdin` (FROM PROGRAM / fayl emas)
        raise RestoreError(f"Zaxirada xavfli COPY buyrug'i: {stmt[:60]!r}")
    if stmt.startswith("ALTER") and BAD_IN_ALTER_RE.search(stmt):
        raise RestoreError(f"Zaxirada xavfli ALTER buyrug'i: {stmt[:60]!r}")


def _unescape(value: str) -> str:
    return value.replace("\\t", "\t").replace("\\n", "\n").replace("\\\\", "\\")


def scan_dump(path: Path) -> DumpInfo:
    """Zaxirani to'liq o'qib tekshiradi; yaroqsiz bo'lsa RestoreError. Bazaga HECH NARSA yuborilmaydi."""
    from .security import decrypt  # kech import: faqat shu yerda kerak

    info, rows, tables = DumpInfo(), Counter(), set()
    in_copy, cols, pending, complete, total = None, [], "", False, 0
    try:
        with _open(path) as fh:
            for raw in fh:
                total += len(raw)
                if total > MAX_DUMP_BYTES:
                    raise RestoreError("Zaxira juda katta (ochilgan holatda)")
                line = raw.rstrip("\n")
                if in_copy:
                    if line == "\\.":
                        in_copy = None
                        continue
                    rows[in_copy] += 1
                    if in_copy == "accounts" and "pbx_key_enc" in cols:
                        value = line.split("\t")[cols.index("pbx_key_enc")] if line.count("\t") >= len(cols) - 1 else "\\N"
                        if value not in ("", "\\N"):
                            info.keys_total += 1
                            with contextlib.suppress(Exception):
                                decrypt(_unescape(value))
                                info.keys_ok += 1
                    continue
                if line.startswith("\\"):
                    if not RESTRICT_RE.match(line):
                        raise RestoreError(f"Zaxirada taqiqlangan psql buyrug'i: {line[:40]!r}")
                    continue
                if not pending and (not line.strip() or line.startswith("--")):
                    if line.startswith("-- Dumped by pg_dump version"):
                        info.pg_version = line.rsplit(" ", 1)[-1]
                    if "dump complete" in line:
                        complete = True
                    continue
                pending += line + "\n"
                if not line.rstrip().endswith(";"):
                    continue
                stmt, pending, complete = pending, "", False
                _check_statement(stmt)
                if m := COPY_RE.match(stmt.strip()):
                    in_copy, cols = m.group(1), [c.strip() for c in m.group(2).split(",")]
                    rows.setdefault(in_copy, 0)
                elif m := CREATE_TABLE_RE.match(stmt.strip()):
                    tables.add(m.group(1))
    except (OSError, EOFError, UnicodeDecodeError) as e:
        raise RestoreError(f"Zaxira fayli o'qilmadi (shikastlangan yoki gzip emas): {e}") from e
    if in_copy or pending.strip():
        raise RestoreError("Zaxira to'liq emas (dump oxiri kesilgan)")
    if not complete:
        raise RestoreError("Zaxira to'liq emas (dump oxiri yo'q)")
    if missing := [t for t in REQUIRED_TABLES if t not in tables]:
        raise RestoreError(f"Bu bizning tizim zaxirasi emas (jadvallar yo'q: {', '.join(missing)})")
    info.size, info.rows = total, dict(rows)
    return info


def clean_lines(path: Path):
    """Tiklash uchun SQL qatorlari: `ALTER ... OWNER TO ...` olib tashlanadi (egasi tiklayotgan rol bo'ladi)."""
    in_copy = False
    with _open(path) as fh:
        for line in fh:
            stripped = line.rstrip("\n")
            if in_copy:
                in_copy = stripped != "\\."
            elif COPY_RE.match(stripped):
                in_copy = True
            elif OWNER_RE.match(stripped):
                continue
            yield line


# ---------------------------------------------------------------- bosqichma-bosqich
def prune_pending(now: float | None = None) -> None:
    cutoff = (now or time.time()) - PENDING_TTL
    for f in work_dir().glob("pending_*.sql.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def pending_path(token: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        raise RestoreError("Noto'g'ri token")
    return work_dir() / f"pending_{token}.sql.gz"


def stage_upload(blob: bytes, passphrase: str) -> tuple[str, DumpInfo]:
    """Yuklangan shifrlangan zaxirani ochadi, tekshiradi va tasdiqlashni kutuvchi fayl sifatida saqlaydi."""
    if len(blob) > MAX_UPLOAD_BYTES:
        raise RestoreError("Fayl juda katta")
    try:
        gz = decrypt_bytes(blob, passphrase)
    except BackupError as e:
        raise RestoreError(str(e)) from e
    if not gz.startswith(b"\x1f\x8b"):
        raise RestoreError("Shifr ochildi, lekin ichida .sql.gz emas")
    return _stage(gz)


def _stage(gz: bytes) -> tuple[str, DumpInfo]:
    prune_pending()
    token = secrets.token_urlsafe(16)
    path = pending_path(token)
    path.write_bytes(gz)
    path.chmod(0o600)
    try:
        return token, scan_dump(path)
    except RestoreError:
        path.unlink(missing_ok=True)
        raise


SNAPSHOT_RE = re.compile(r"^snapshot_\d{8}_\d{6}\.sql\.gz$")


def list_snapshots() -> list[Path]:
    return sorted((p for p in work_dir().iterdir() if SNAPSHOT_RE.match(p.name)), reverse=True)


def stage_snapshot(name: str) -> tuple[str, DumpInfo]:
    if not SNAPSHOT_RE.match(name or "") or not (work_dir() / name).is_file():
        raise RestoreError("Bunday avtomatik nusxa yo'q")
    return _stage((work_dir() / name).read_bytes())


def snapshot(url: str | None = None) -> Path:
    """Joriy bazaning to'liqligi tekshirilgan nusxasi (tiklashdan oldin; bekor qilish uchun)."""
    dest = work_dir() / time.strftime("snapshot_%Y%m%d_%H%M%S.sql.gz")
    part = dest.with_suffix(".part")
    err = work_dir() / "pg_dump.err"
    try:
        with err.open("wb") as ef, gzip.open(part, "wb") as out:
            # argumentlar doimiy, yo'l shutil.which orqali topiladi
            cmd = [_tool("pg_dump"), "--no-owner", "--no-privileges"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ef, env=pg_env(url))  # noqa: S603  # nosec B603
            shutil.copyfileobj(proc.stdout, out)
            if proc.wait(timeout=RESTORE_TIMEOUT) != 0:
                raise RestoreError("pg_dump xato berdi: " + err.read_text(errors="replace")[-300:])
        scan_dump(part)  # nusxa o'zi ham to'liq va yaroqli bo'lishi shart
        part.replace(dest)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    finally:
        err.unlink(missing_ok=True)
    dest.chmod(0o600)
    for old in list_snapshots()[config.RESTORE_KEEP_SNAPSHOTS :]:
        old.unlink(missing_ok=True)
    return dest


def apply_dump(path: Path, url: str | None = None) -> None:
    """Bazani zaxira bilan almashtiradi: DROP + CREATE SCHEMA + yuklash, hammasi BITTA tranzaksiyada.
    Xato bo'lsa tranzaksiya bekor qilinadi va baza avvalgidek qoladi."""
    err = work_dir() / "psql.err"
    cmd = [_tool("psql"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-1"]
    for sql in ("SET lock_timeout = '30s'", "DROP SCHEMA public CASCADE", "CREATE SCHEMA public"):
        cmd += ["-c", sql]
    cmd += ["-f", "-"]
    try:
        with err.open("wb") as ef:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=ef, stdout=subprocess.DEVNULL, env=pg_env(url))  # noqa: S603  # nosec B603
            try:
                for line in clean_lines(path):
                    proc.stdin.write(line.encode())
            except BrokenPipeError:
                pass  # psql erta to'xtadi (xato): sababi stderr'da
            finally:
                with contextlib.suppress(BrokenPipeError, OSError):
                    proc.stdin.close()
            code = proc.wait(timeout=RESTORE_TIMEOUT)
        if code != 0:
            tail = err.read_text(errors="replace").strip().splitlines()[-3:]
            raise RestoreError("Tiklashda xato (baza o'zgarmadi): " + " | ".join(tail)[:400])
    finally:
        err.unlink(missing_ok=True)


def run_restore(pending: Path, actor: str, url: str | None = None, post_restore=None) -> None:
    """Fon oqimida: nusxa olish -> almashtirish -> tozalash. Holat STATE da; sayt davomida texnik ishlar rejimida."""
    from . import telegram

    def notify(text: str) -> None:
        if config.ADMIN_CHAT_ID and config.BOT_TOKEN:
            with contextlib.suppress(Exception):
                telegram.send_message(config.ADMIN_CHAT_ID, text)

    log.warning("RESTORE start by %s from %s", actor, pending.name)
    notify(f"♻️ <b>Baza tiklanmoqda</b> ({actor}). Sayt vaqtincha texnik ishlar rejimida.")
    try:
        snap = snapshot(url)
        STATE.set("restoring", "Baza almashtirilmoqda")
        apply_dump(pending, url)
        if post_restore:
            post_restore()  # ulanishlar havzasini yangilash, yangi ustunlarni qo'shish
        STATE.set("done", f"Tiklandi. Oldingi holat nusxasi: {snap.name}")
        log.warning("RESTORE done by %s, snapshot %s", actor, snap.name)
        notify(f"✅ <b>Baza tiklandi</b>. Oldingi holat: <code>{snap.name}</code>")
    except Exception as e:
        msg = str(e) if isinstance(e, RestoreError) else f"Kutilmagan xato: {type(e).__name__}"
        log.exception("RESTORE failed")
        STATE.set("error", msg)
        notify(f"❌ <b>Baza tiklanmadi</b>: {msg}")
    finally:
        pending.unlink(missing_ok=True)
