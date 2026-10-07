"""Bazani tiklash: zaxirani tekshirish, veb-oqim va (PostgreSQL bo'lsa) haqiqiy almashtirish."""

import gzip
import os
import time

import pytest
from sqlalchemy import create_engine, text

from app import config, restore, security
from app.backup_cli import encrypt_bytes
from app.db import Base
from tests.helpers import register

PG_URL = os.environ.get("TEST_PG_URL", "postgresql+psycopg2://pbxtest:pw123@127.0.0.1/pbxtest")
TABLES = "".join(f"CREATE TABLE public.{t} (\n    id integer NOT NULL\n);\n" for t in restore.REQUIRED_TABLES)
HEAD = "-- Dumped by pg_dump version 16.4\nSET statement_timeout = 0;\n"
TAIL = "-- PostgreSQL database dump complete\n"


def write(tmp_path, body, name="d.sql.gz"):
    p = tmp_path / name
    p.write_bytes(gzip.compress(body.encode()))
    return p


@pytest.fixture(autouse=True)
def restore_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESTORE_DIR", str(tmp_path / "rd"))
    monkeypatch.setattr(config, "ALLOW_PANEL_RESTORE", True)
    restore.STATE.phase, restore.STATE.token = "idle", ""


# ---------------------------------------------------------------- scan_dump
def test_scan_accepts_valid_dump_with_restrict_lines(tmp_path):
    body = "\\restrict abc123\n" + HEAD + TABLES + "COPY public.users (id) FROM stdin;\n1\n2\n\\.\n\\unrestrict abc123\n" + TAIL
    info = restore.scan_dump(write(tmp_path, body))
    assert info.rows["users"] == 2 and info.pg_version == "16.4"


@pytest.mark.parametrize(
    "extra",
    [
        "\\! rm -rf /\n",
        "\\copy users to '/tmp/x'\n",
        "CREATE FUNCTION f() RETURNS int AS 'select 1' LANGUAGE sql;\n",
        "CREATE ROLE evil SUPERUSER;\n",
        "DROP TABLE public.users;\n",
        "ALTER TABLE ONLY public.users ENABLE TRIGGER ALL;\n",
        "COPY public.users (id) FROM PROGRAM 'id';\n",
    ],
)
def test_scan_rejects_forbidden_statements(tmp_path, extra):
    with pytest.raises(restore.RestoreError):
        restore.scan_dump(write(tmp_path, HEAD + TABLES + extra + TAIL))


def test_scan_rejects_truncated_foreign_and_garbage(tmp_path):
    with pytest.raises(restore.RestoreError, match="to'liq emas"):
        restore.scan_dump(write(tmp_path, HEAD + TABLES))  # oxiri yo'q
    with pytest.raises(restore.RestoreError, match="to'liq emas"):
        restore.scan_dump(write(tmp_path, HEAD + TABLES + "COPY public.users (id) FROM stdin;\n1\n"))
    with pytest.raises(restore.RestoreError, match="jadvallar yo'q"):
        restore.scan_dump(write(tmp_path, HEAD + "CREATE TABLE public.other (\n    id integer\n);\n" + TAIL))
    bad = tmp_path / "bad.sql.gz"
    bad.write_bytes(b"not gzip")
    with pytest.raises(restore.RestoreError, match="o'qilmadi"):
        restore.scan_dump(bad)


def test_scan_checks_secret_key_compatibility(tmp_path):
    good, bad = security.encrypt("apikey"), "gAAAAABnotvalid"
    body = (
        HEAD
        + TABLES.replace(
            "public.accounts (\n    id integer NOT NULL", "public.accounts (\n    id integer NOT NULL, pbx_key_enc text"
        )
        + f"COPY public.accounts (id, pbx_key_enc) FROM stdin;\n1\t{good}\n2\t{bad}\n3\t\\N\n\\.\n"
        + TAIL
    )
    info = restore.scan_dump(write(tmp_path, body))
    assert (info.keys_total, info.keys_ok) == (2, 1)


def test_clean_lines_strips_owner_only_outside_copy(tmp_path):
    body = (
        HEAD
        + "ALTER TABLE public.users OWNER TO pbx;\nCOPY public.users (id) FROM stdin;\nALTER TABLE x OWNER TO y;\n\\.\n"
        + TAIL
    )
    out = "".join(restore.clean_lines(write(tmp_path, body)))
    assert "OWNER TO pbx" not in out and "ALTER TABLE x OWNER TO y;" in out


def test_stage_upload_encrypted_and_wrong_passphrase(tmp_path):
    gz = gzip.compress((HEAD + TABLES + TAIL).encode())
    token, info = restore.stage_upload(encrypt_bytes(gz, "pw"), "pw")
    assert restore.pending_path(token).is_file() and info.size
    with pytest.raises(restore.RestoreError):
        restore.stage_upload(encrypt_bytes(gz, "pw"), "wrong")
    with pytest.raises(restore.RestoreError):
        restore.stage_upload(encrypt_bytes(b"plain text", "pw"), "pw")
    assert len(list(restore.work_dir().glob("pending_*"))) == 1  # yaroqsizlari qolmaydi
    with pytest.raises(restore.RestoreError):
        restore.pending_path("../../etc/passwd")


def test_prune_pending_removes_old_files():
    f = restore.work_dir() / ("pending_" + "x" * 16 + ".sql.gz")
    f.write_bytes(b"x")
    os.utime(f, (time.time() - 7200,) * 2)
    restore.prune_pending()
    assert not f.exists()


# ---------------------------------------------------------------- veb
def _admin_client(client):
    register(client, "admin@x.uz")
    return client


def test_restore_pages_disabled_and_access_control(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    _admin_client(client)
    monkeypatch.setattr(config, "ALLOW_PANEL_RESTORE", False)
    assert "ALLOW_PANEL_RESTORE=1" in client.get("/admin/restore").text
    r = client.post("/admin/restore/inspect", files={"backup": ("a.enc", b"x")})
    assert r.status_code == 404
    other = TestClient(app)
    register(other, "u@x.uz")
    assert other.get("/admin/restore").status_code == 403
    assert TestClient(app).get("/admin/restore", follow_redirects=False).status_code == 303


def test_restore_flow_guards(client, monkeypatch):
    _admin_client(client)
    gz = gzip.compress((HEAD + TABLES + TAIL).encode())
    r = client.post("/admin/restore/inspect", files={"backup": ("b.enc", encrypt_bytes(gz, "pw"))}, data={"passphrase": "pw"})
    assert r.status_code == 200 and "Tiklashni tasdiqlang" in r.text
    token = restore.STATE.token or next(restore.work_dir().glob("pending_*")).name[8:-7]
    calls = []
    monkeypatch.setattr(restore, "run_restore", lambda *a, **k: calls.append(a))

    def run(**over):
        data = {"token": token, "confirm": "TIKLASH", "password": "12345678", **over}
        return client.post("/admin/restore/run", data=data, follow_redirects=False)

    assert "Parol" in run(password="bad").headers["location"].replace("+", " ")
    assert "so%27zi" in run(confirm="x").headers["location"]
    assert "muddati" in run(token="A" * 22).headers["location"]
    assert not calls
    r = run()
    assert r.headers["location"] == "/admin/restore/progress"
    time.sleep(0.2)
    assert calls and restore.STATE.phase == "snapshot"
    assert run().status_code == 503  # tiklash davomida sayt texnik ishlar rejimida
    assert not restore.STATE.claim("z" * 22)  # ikkinchi tiklashni boshlab bo'lmaydi
    assert client.get("/admin/restore/status").json()["phase"] == "snapshot"
    assert client.get("/admin/restore/progress").status_code == 200


def test_maintenance_blocks_requests_but_not_status(client):
    _admin_client(client)
    restore.STATE.claim("t" * 22)
    try:
        assert client.get("/cabinet").status_code == 503
        assert client.get("/healthz").status_code in (200, 503)
    finally:
        restore.STATE.phase = "idle"
    assert client.get("/cabinet").status_code == 200


def test_status_requires_matching_session(client):
    assert client.get("/admin/restore/status").status_code == 404


# ---------------------------------------------------------------- haqiqiy PostgreSQL
@pytest.fixture()
def pg(monkeypatch):
    try:
        eng = create_engine(PG_URL)
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    except Exception:
        pytest.skip("PostgreSQL mavjud emas")
    if not (restore.shutil.which("psql") and restore.shutil.which("pg_dump")):
        pytest.skip("postgresql-client yo'q")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _emails(eng):
    with eng.connect() as c:
        return sorted(r[0] for r in c.execute(text("SELECT email FROM users")))


def _add_user(eng, email):
    with eng.begin() as c:
        c.execute(
            text("INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (:e, 'x', false, now())"), {"e": email}
        )


def test_pg_snapshot_and_restore_roundtrip(pg):
    _add_user(pg, "one@x.uz")
    snap = restore.snapshot(PG_URL)
    assert snap.is_file() and restore.scan_dump(snap).rows["users"] == 1
    _add_user(pg, "two@x.uz")
    assert _emails(pg) == ["one@x.uz", "two@x.uz"]
    restore.apply_dump(snap, PG_URL)
    assert _emails(pg) == ["one@x.uz"]


def test_pg_failed_restore_leaves_database_untouched(pg, tmp_path):
    _add_user(pg, "keep@x.uz")
    broken = HEAD + TABLES + "COPY public.users (id) FROM stdin;\nnot-a-number\n\\.\n" + TAIL
    with pytest.raises(restore.RestoreError, match="baza o'zgarmadi"):
        restore.apply_dump(write(tmp_path, broken), PG_URL)
    assert _emails(pg) == ["keep@x.uz"]


def test_pg_run_restore_end_to_end_and_snapshot_pruning(pg, monkeypatch):
    monkeypatch.setattr(config, "RESTORE_KEEP_SNAPSHOTS", 1)
    _add_user(pg, "one@x.uz")
    good = restore.snapshot(PG_URL)
    pending = restore.pending_path("a" * 16)
    pending.write_bytes(good.read_bytes())
    _add_user(pg, "two@x.uz")
    hooked = []
    time.sleep(1.1)  # snapshot nomi soniyaga bog'liq
    assert restore.STATE.claim("a" * 16)
    restore.run_restore(pending, "tester", PG_URL, post_restore=lambda: hooked.append(1))
    assert restore.STATE.phase == "done" and hooked and not pending.exists()
    assert _emails(pg) == ["one@x.uz"]
    assert len(restore.list_snapshots()) == 1  # RESTORE_KEEP_SNAPSHOTS=1


def test_pg_run_restore_reports_error_state(pg):
    pending = restore.pending_path("b" * 16)
    pending.write_bytes(gzip.compress((HEAD + TABLES + "COPY public.users (id) FROM stdin;\nx\n\\.\n" + TAIL).encode()))
    assert restore.STATE.claim("b" * 16)
    restore.run_restore(pending, "tester", PG_URL)
    assert restore.STATE.phase == "error" and "o'zgarmadi" in restore.STATE.message
