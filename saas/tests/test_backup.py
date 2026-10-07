"""Zaxira: shifrlash, adminning Telegramiga yuborish (kechagisini o'chirish) va deploy/backup.sh."""

import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import backup_cli, config, telegram
from tests.helpers import register

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "backup.sh"
PASS = "test-parol-123"


# ---------- Shifrlash ----------
def test_encrypt_decrypt_roundtrip_and_no_plaintext_leak():
    data = b"customer@example.com,998901112233\n" * 50
    blob = backup_cli.encrypt_bytes(data, PASS)
    assert blob.startswith(backup_cli.MAGIC) and b"customer@example.com" not in blob and b"998901112233" not in blob
    assert backup_cli.decrypt_bytes(blob, PASS) == data
    assert backup_cli.encrypt_bytes(data, PASS) != blob  # har safar yangi tuz: bir xil kirish -> boshqa shifr


def test_wrong_passphrase_tampering_and_foreign_file_rejected():
    blob = backup_cli.encrypt_bytes(b"secret", PASS)
    with pytest.raises(backup_cli.BackupError, match="noto'g'ri"):
        backup_cli.decrypt_bytes(blob, "boshqa-parol")
    tampered = bytearray(blob)
    tampered[-5] ^= 0x01
    with pytest.raises(backup_cli.BackupError):
        backup_cli.decrypt_bytes(bytes(tampered), PASS)
    with pytest.raises(backup_cli.BackupError, match="shifrlangan zaxira"):
        backup_cli.decrypt_bytes(b"random bytes that are not a backup", PASS)


def test_load_env_parses_quotes_comments_and_keeps_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# izoh\n\nA_PLAIN=value\nA_QUOTED=\"with spaces & symbols\"\nexport A_EXPORT='x y'\nA_EXISTING=from_file\nbad line\n"
    )
    for k in ("A_PLAIN", "A_QUOTED", "A_EXPORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("A_EXISTING", "from_env")
    backup_cli.load_env(env)
    assert os.environ["A_PLAIN"] == "value" and os.environ["A_QUOTED"] == "with spaces & symbols"
    assert os.environ["A_EXPORT"] == "x y" and os.environ["A_EXISTING"] == "from_env"  # muhit ustun
    for k in ("A_PLAIN", "A_QUOTED", "A_EXPORT"):
        os.environ.pop(k, None)
    backup_cli.load_env(tmp_path / "yoq.env")  # fayl yo'q: jim o'tadi


# ---------- Telegramga yuborish ----------
@pytest.fixture()
def tg(monkeypatch, tmp_path):
    """Telegram'ni yozib oluvchi soxta; ADMIN_CHAT_ID va parol sozlangan."""
    calls = {"docs": [], "deleted": [], "messages": []}
    counter = iter(range(100, 200))

    def send_document(chat, data, name, caption=""):
        calls["docs"].append({"chat": chat, "data": data, "name": name, "caption": caption})
        return next(counter)

    monkeypatch.setattr(telegram, "send_document", send_document)
    monkeypatch.setattr(telegram, "delete_message", lambda chat, mid: calls["deleted"].append((chat, mid)))
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: calls["messages"].append((chat, text)))
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "555")
    monkeypatch.setattr(config, "BOT_TOKEN", "token")
    monkeypatch.setenv("BACKUP_PASSPHRASE", PASS)
    dump = tmp_path / "db_2026-10-07_2359.sql.gz"
    dump.write_bytes(b"gzip-bytes-of-dump" * 100)
    calls["dump"], calls["state"] = dump, tmp_path / "state.json"
    return calls


def test_send_backup_encrypts_and_describes(client, tg):
    register(client, "c@x.uz")
    rc = backup_cli.send_backup(tg["dump"], tg["state"])
    assert rc == backup_cli.OK
    doc = tg["docs"][0]
    assert doc["chat"] == "555" and doc["name"].endswith(".gz.enc") and "sql" in doc["name"]
    assert backup_cli.decrypt_bytes(doc["data"], PASS) == tg["dump"].read_bytes()  # Telegramga ketgani shifrlangan, ochiladi
    assert tg["dump"].read_bytes() not in doc["data"]
    assert "Zaxira" in doc["caption"] and "Mijozlar: 1" in doc["caption"] and "decrypt" in doc["caption"]
    state = json.loads(tg["state"].read_text())
    assert state["chat_id"] == "555" and state["message_id"] == 100
    assert oct(tg["state"].stat().st_mode & 0o777) == "0o600"
    assert tg["deleted"] == []  # birinchi marta o'chiradigan narsa yo'q


def test_next_backup_deletes_previous_telegram_message(tg):
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.OK
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.OK
    assert tg["deleted"] == [("555", 100)]  # kechagisi o'chdi
    assert json.loads(tg["state"].read_text())["message_id"] == 101  # endi yangisi eslab qolindi
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.OK
    assert tg["deleted"] == [("555", 100), ("555", 101)]


def test_previous_message_kept_when_new_send_fails(tg, monkeypatch):
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.OK

    def boom(*a, **k):
        raise telegram.TelegramError("Bad Gateway", 502)

    monkeypatch.setattr(telegram, "send_document", boom)
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.FAILED
    assert tg["deleted"] == [] and json.loads(tg["state"].read_text())["message_id"] == 100  # eski nusxa himoyalangan


def test_delete_failure_and_other_chat_do_not_break(tg, monkeypatch):
    tg["state"].write_text(json.dumps({"chat_id": "OTHER", "message_id": 7}))
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.OK
    assert tg["deleted"] == []  # boshqa chat xabariga tegilmaydi
    monkeypatch.setattr(
        telegram, "delete_message", lambda *a: (_ for _ in ()).throw(telegram.TelegramError("message can't be deleted"))
    )
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.OK  # o'chirib bo'lmasa ham muvaffaqiyat
    tg["state"].write_text("{buzuq json")
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.OK  # holat fayli buzuq: e'tiborsiz


def test_without_passphrase_nothing_unencrypted_is_sent(tg, monkeypatch):
    monkeypatch.delenv("BACKUP_PASSPHRASE")
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.NO_PASSPHRASE
    assert tg["docs"] == [] and "BACKUP_PASSPHRASE" in tg["messages"][0][1] and "yuborilmadi" in tg["messages"][0][1]


def test_not_configured_and_too_big(tg, monkeypatch):
    monkeypatch.setattr(backup_cli, "MAX_TELEGRAM_BYTES", 1000)
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.TOO_BIG
    assert tg["docs"] == [] and "juda katta" in tg["messages"][0][1]
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "")
    assert backup_cli.send_backup(tg["dump"], tg["state"]) == backup_cli.NOT_CONFIGURED
    assert len(tg["messages"]) == 1  # sozlanmagan bo'lsa, xabar yuborishga urinmaydi


def test_cli_decrypt_and_telegram_reads_env_file(tg, tmp_path, monkeypatch, capsys):
    blob = backup_cli.encrypt_bytes(b"dump-data", PASS)
    (tmp_path / "x.enc").write_bytes(blob)
    assert backup_cli.main(["decrypt", str(tmp_path / "x.enc"), str(tmp_path / "x.out")]) == 0
    assert (tmp_path / "x.out").read_bytes() == b"dump-data"
    monkeypatch.setenv("BACKUP_PASSPHRASE", "xato")
    assert backup_cli.main(["decrypt", str(tmp_path / "x.enc"), str(tmp_path / "y.out")]) == backup_cli.FAILED
    assert "noto'g'ri" in capsys.readouterr().err and not (tmp_path / "y.out").exists()
    # parol .env dan olinadi (backup.sh .env ni shell orqali o'qimaydi)
    monkeypatch.delenv("BACKUP_PASSPHRASE")
    (tmp_path / ".env").write_text(f'BACKUP_PASSPHRASE="{PASS}"\n')
    monkeypatch.setenv("ENV_FILE", str(tmp_path / ".env"))
    assert backup_cli.main(["telegram", str(tg["dump"]), "--state", str(tg["state"])]) == backup_cli.OK
    os.environ.pop("BACKUP_PASSPHRASE", None)


# ---------- deploy/backup.sh ----------

FAKE_SUDO = """#!/usr/bin/env bash
# sudo -u postgres pg_dump pbxbot  (soxta)
echo "-- PostgreSQL database dump"
echo "SELECT 1;"
[ -n "${FAKE_FAIL:-}" ] && exit 1
[ -n "${FAKE_TRUNCATE:-}" ] && exit 0
echo "-- PostgreSQL database dump complete"
"""


@pytest.fixture()
def sh(tmp_path):
    bin_dir, dest, app = tmp_path / "bin", tmp_path / "backups", tmp_path / "app"
    bin_dir.mkdir()
    (bin_dir / "sudo").write_text(FAKE_SUDO)
    (bin_dir / "sudo").chmod(0o755)
    (app / "saas" / "uploads").mkdir(parents=True)
    (app / "saas" / "uploads" / "chek.png").write_bytes(b"png")

    def run(extra_env=None, keep=None):
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "APP": str(app),
            "BACKUP_DIR": str(dest),
            "SKIP_TELEGRAM": "1",
        }
        if keep is not None:
            env["BACKUP_KEEP"] = str(keep)
        env.update(extra_env or {})
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False)

    def seed(name, age_days):
        dest.mkdir(exist_ok=True)
        f = dest / name
        f.write_bytes(b"old")
        t = time.time() - age_days * 86400
        os.utime(f, (t, t))
        return f

    return run, seed, dest


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash kerak")
def test_backup_keeps_only_latest_and_removes_old(sh):
    run, seed, dest = sh
    for i, age in enumerate((3, 2, 1)):
        seed(f"db_2020-01-0{i + 1}_0000.sql.gz", age)
        seed(f"uploads_2020-01-0{i + 1}_0000.tar.gz", age)
    other = seed("telegram_state.json", 5)  # boshqa fayllarga tegilmaydi
    r = run()
    assert r.returncode == 0 and r.stdout.startswith("OK:"), r.stderr
    files = sorted(p.name for p in dest.iterdir())
    assert len(files) == 3 and other.exists()  # 1 baza + 1 cheklar + holat fayli
    new_db = next(p for p in dest.glob("db_*.sql.gz") if p.stat().st_size > 10)
    with gzip.open(new_db, "rt") as fh:
        assert "dump complete" in fh.read()
    assert not list(dest.glob("*.part"))
    assert oct(new_db.stat().st_mode & 0o777) == "0o600"  # faqat egasi o'qiy oladi


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash kerak")
def test_backup_keep_count_is_configurable_and_invalid_falls_back(sh):
    run, seed, dest = sh
    for i, age in enumerate((4, 3, 2)):
        seed(f"db_2020-01-0{i + 1}_0000.sql.gz", age)
    assert run(keep=2).returncode == 0
    assert len(list(dest.glob("db_*.sql.gz"))) == 2  # yangi + eng yangi eskisi
    assert run(extra_env={"BACKUP_KEEP": "abc"}).returncode == 0
    assert len(list(dest.glob("db_*.sql.gz"))) == 1  # noto'g'ri qiymat -> 1


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash kerak")
@pytest.mark.parametrize("flag", ["FAKE_FAIL", "FAKE_TRUNCATE"])
def test_failed_or_incomplete_dump_never_deletes_good_backups(sh, flag):
    run, seed, dest = sh
    good = seed("db_2020-01-01_0000.sql.gz", 1)
    r = run(extra_env={flag: "1"})
    assert r.returncode != 0
    assert good.exists() and good.read_bytes() == b"old"  # yaxshi nusxa saqlandi
    assert [p.name for p in dest.iterdir()] == [good.name]  # .part ham, yangi buzuq fayl ham qolmadi


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash kerak")
def test_telegram_failure_does_not_fail_the_local_backup(sh, tmp_path):
    run, seed, dest = sh
    stub = tmp_path / "fakepy"
    stub.write_text("#!/bin/sh\nexit 3\n")
    stub.chmod(0o755)
    r = run(extra_env={"SKIP_TELEGRAM": "", "PY": str(stub)})
    assert r.returncode == 0 and "OGOHLANTIRISH" in r.stderr and "kod 3" in r.stderr
    assert len(list(dest.glob("db_*.sql.gz"))) == 1  # mahalliy zaxira saqlangan


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash kerak")
def test_backup_script_runs_real_cli_end_to_end(sh, tmp_path, monkeypatch):
    """backup.sh -> haqiqiy `python -m app.backup_cli`: sozlanmagan Telegram jim ogohlantiradi, zaxirani buzmaydi."""
    run, _, dest = sh
    (tmp_path / "app" / "saas").mkdir(parents=True, exist_ok=True)
    project = str(Path(__file__).resolve().parent.parent)
    env = {
        "SKIP_TELEGRAM": "",
        "PY": sys.executable,
        "PYTHONPATH": project,
        "ENV_FILE": str(tmp_path / "yoq.env"),
        "ADMIN_CHAT_ID": "",
        "BOT_TOKEN": "",
        "DATABASE_URL": "sqlite:///./test.db",
    }
    r = run(extra_env=env)
    assert r.returncode == 0 and "ADMIN_CHAT_ID" in r.stderr and "kod 2" in r.stderr
    assert len(list(dest.glob("db_*.sql.gz"))) == 1
