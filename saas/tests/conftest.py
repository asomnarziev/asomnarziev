"""Umumiy test sozlamalari: muhit (ilovadan oldin o'rnatiladi) va ikki asosiy fixtura."""

import os

# Ilova import qilinishidan OLDIN: fon rejalashtiruvchi o'chiq, alohida sinov bazasi
os.environ.update(SCHEDULER="0", DATABASE_URL="sqlite:///./test.db", BOT_USERNAME="b", BASE_URL="http://t")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import live, ratelimit, service, telegram  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    """Har test uchun toza baza va ilova (lifespan bilan)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolate_process_state(monkeypatch):
    """Telegram'ga haqiqiy so'rov yuborilmasin; jarayon xotirasidagi holat testlar orasida aralashmasin."""
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: None)
    ratelimit.login_throttle._hits.clear()
    service._importing.clear()
    live._subs.clear()
    yield
    ratelimit.login_throttle._hits.clear()


def pytest_sessionfinish(session, exitstatus):
    if os.path.exists("test.db"):
        os.remove("test.db")
