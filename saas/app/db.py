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


_PAYMENT_COLUMNS = {"months": "INTEGER DEFAULT 1", "receipt": "VARCHAR(100) DEFAULT ''", "note": "TEXT DEFAULT ''", "comment": "TEXT DEFAULT ''"}


def init_db():
    """Jadvallarni yaratadi va eski bazaga yangi ustunlarni qo'shadi (Alembic o'rniga yengil migratsiya)."""
    from sqlalchemy import inspect, text

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    if insp.has_table("payments"):
        have = {c["name"] for c in insp.get_columns("payments")}
        with engine.begin() as conn:
            for name, ddl in _PAYMENT_COLUMNS.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE payments ADD COLUMN {name} {ddl}"))
