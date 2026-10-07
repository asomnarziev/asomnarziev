from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=_args, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Eski bazaga qo'shiladigan ustunlar (jadval -> {ustun: DDL}); Alembic o'rniga yengil migratsiya
_NEW_COLUMNS = {
    "payments": {"months": "INTEGER DEFAULT 1", "receipt": "VARCHAR(100) DEFAULT ''", "note": "TEXT DEFAULT ''",
                 "comment": "TEXT DEFAULT ''"},
    "accounts": {"digest_on": "BOOLEAN DEFAULT TRUE", "digest_hour": "INTEGER DEFAULT 9", "digest_last": "VARCHAR(10) DEFAULT ''"},
    "call_logs": {"direction": "VARCHAR(10) DEFAULT ''", "caller": "VARCHAR(40) DEFAULT ''",
                  "callee": "VARCHAR(40) DEFAULT ''", "started_at": "TIMESTAMP", "duration": "INTEGER DEFAULT 0",
                  "talk": "INTEGER"},
}


def init_db():
    """Jadvallarni yaratadi va eski bazaga yangi ustunlarni qo'shadi."""
    from sqlalchemy import inspect, text

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
