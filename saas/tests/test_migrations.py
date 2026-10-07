"""Migrations testlari."""


def test_db_migration_adds_payment_columns(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text

    from app import db as dbm

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(
            text(
                "CREATE TABLE payments (id INTEGER PRIMARY KEY, account_id INTEGER, plan VARCHAR(20), amount INTEGER, "
                "provider VARCHAR(20), status VARCHAR(20), created_at DATETIME)"
            )
        )
        c.execute(text("INSERT INTO payments (account_id, plan, amount, provider, status) VALUES (1,'pro',1,'manual','paid')"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    dbm.init_db()  # ikkinchi marta zararsiz
    assert {"months", "receipt", "note"} <= {c["name"] for c in inspect(eng).get_columns("payments")}
    with eng.connect() as c:
        assert c.execute(text("SELECT months, receipt FROM payments")).one() == (1, "")


def test_migration_adds_call_log_columns(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text

    from app import db as dbm

    eng = create_engine(f"sqlite:///{tmp_path / 'old2.db'}")
    with eng.begin() as c:
        c.execute(
            text(
                "CREATE TABLE call_logs (id INTEGER PRIMARY KEY, account_id INTEGER, uuid VARCHAR(100), "
                "status VARCHAR(20), error TEXT, created_at DATETIME)"
            )
        )
        c.execute(text("INSERT INTO call_logs (account_id, uuid, status, error) VALUES (1,'u','sent','')"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    assert {"direction", "caller", "callee", "started_at", "duration", "talk"} <= {
        c["name"] for c in inspect(eng).get_columns("call_logs")
    }
    with eng.connect() as c:
        assert c.execute(text("SELECT direction, duration, talk, started_at FROM call_logs")).one() == ("", 0, None, None)


def test_migration_adds_digest_columns(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text

    from app import db as dbm

    eng = create_engine(f"sqlite:///{tmp_path / 'old3.db'}")
    with eng.begin() as c:
        c.execute(
            text(
                "CREATE TABLE accounts (id INTEGER PRIMARY KEY, user_id INTEGER, pbx_domain VARCHAR(255), pbx_key_enc TEXT, "
                "hook_token VARCHAR(64), link_code VARCHAR(64), plan VARCHAR(20), trial_ends DATETIME, "
                "paid_until DATETIME, suspended BOOLEAN)"
            )
        )
        c.execute(text("INSERT INTO accounts (user_id, plan, suspended) VALUES (1,'pro',0)"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    assert {"digest_on", "digest_hour", "digest_last"} <= {c["name"] for c in inspect(eng).get_columns("accounts")}
    with eng.connect() as c:
        assert c.execute(text("SELECT digest_on, digest_hour, digest_last FROM accounts")).one() == (1, 9, "")


def test_migration_adds_missed_on(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text

    from app import db as dbm

    eng = create_engine(f"sqlite:///{tmp_path / 'old4.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, user_id INTEGER, plan VARCHAR(20), suspended BOOLEAN)"))
        c.execute(text("INSERT INTO accounts (user_id, plan, suspended) VALUES (1,'pro',0)"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    assert "missed_on" in {c["name"] for c in inspect(eng).get_columns("accounts")}
    with eng.connect() as c:
        assert c.execute(text("SELECT missed_on FROM accounts")).scalar() == 1


def test_migration_adds_import_columns(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text

    from app import db as dbm

    eng = create_engine(f"sqlite:///{tmp_path / 'old5.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, user_id INTEGER, plan VARCHAR(20), suspended BOOLEAN)"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    assert {"import_at", "import_note"} <= {c["name"] for c in inspect(eng).get_columns("accounts")}
