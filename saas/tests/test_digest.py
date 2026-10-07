"""Digest testlari."""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from app import telegram
from app.db import SessionLocal
from app.main import app
from app.models import Account
from tests.helpers import make_pro, register, seed_calls


def at_local(h, m=0, days=0):
    from datetime import datetime, time, timedelta

    from app import reports
    from app.messages import LOCAL_TZ

    return datetime.combine(reports.today_local() + timedelta(days=days), time(h, m), LOCAL_TZ).astimezone(UTC)


@pytest.fixture()
def digest_env(client, monkeypatch):
    from app import scheduler

    scheduler._attempts.clear()
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: sent.append((chat, text)))
    admin = client
    register(admin, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
    cust.post("/cabinet/chat", data={"chat_id": "1", "lang": "uz"})
    cust.post("/cabinet/chat", data={"chat_id": "2", "lang": "ru"})
    sent.clear()
    return admin, cust, sent


def test_daily_digest_sent_once_in_chat_languages(digest_env):
    from datetime import timedelta

    from app import reports, scheduler

    admin, cust, sent = digest_env
    make_pro(admin, 2)
    seed_calls(2, reports.today_local() - timedelta(days=1))  # kechagi qo'ng'iroqlar
    with SessionLocal() as db:
        assert scheduler.run_due(db, at_local(8, 59)) == 0 and sent == []  # soat 9:00 dan oldin yuborilmaydi
        assert scheduler.run_due(db, at_local(9, 5)) == 1
        acc = db.get(Account, 2)
        assert acc.digest_last == (reports.today_local() - timedelta(days=1)).isoformat()
    assert [c for c, _ in sent] == ["1", "2"]
    uz, ru = sent[0][1], sent[1][1]
    assert (
        "Kunlik hisobot" in uz
        and "Jami: <b>4</b>" in uz
        and "javob berilgan: <b>2</b> (50%)" in uz
        and "Suhbat vaqti: 01:30" in uz
    )
    assert "<code>105</code> — 3 ta, javobsiz 1" in uz and "Javobsiz raqamlar" in uz and "998901112233" in uz
    assert "Дневной отчёт" in ru and "Всего: <b>4</b>" in ru and "пропущено: <b>2</b>" in ru
    with SessionLocal() as db:
        assert scheduler.run_due(db, at_local(9, 30)) == 0  # ikkinchi marta yuborilmaydi
    assert len(sent) == 2
    with SessionLocal() as db:  # ertasi kuni: kecha qo'ng'iroq yo'q -> xabar yo'q, lekin kun belgilanadi
        assert scheduler.run_due(db, at_local(9, 5, days=1)) == 1
    assert len(sent) == 2


def test_digest_skips_non_pro_disabled_and_no_chats(digest_env):
    from datetime import timedelta

    from app import reports, scheduler

    admin, cust, sent = digest_env
    seed_calls(2, reports.today_local() - timedelta(days=1))
    with SessionLocal() as db:
        assert scheduler.run_due(db, at_local(9, 5)) == 0  # Pro emas (sinov)
    make_pro(admin, 2)
    cust.post("/cabinet/digest", data={"hour": "9"})  # "on" yo'q = o'chirilgan
    with SessionLocal() as db:
        assert scheduler.run_due(db, at_local(9, 5)) == 0
    cust.post("/cabinet/digest", data={"on": "1", "hour": "10"})
    with SessionLocal() as db:
        assert scheduler.run_due(db, at_local(9, 5)) == 0  # soat 10:00 qo'yilgan
        assert scheduler.run_due(db, at_local(10, 0)) == 1
    assert [c for c, _ in sent] == ["1", "2"]  # ikki chatga ketdi


def test_digest_retries_then_gives_up(digest_env, monkeypatch):
    from datetime import timedelta

    from app import reports, scheduler

    admin, cust, sent = digest_env
    make_pro(admin, 2)
    seed_calls(2, reports.today_local() - timedelta(days=1))

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(telegram, "send_message", boom)
    with SessionLocal() as db:
        assert [scheduler.run_due(db, at_local(9, 5)) for _ in range(3)] == [0, 0, 1]  # 3-urinishdan keyin tashlanadi
        assert scheduler.run_due(db, at_local(9, 6)) == 0


def test_digest_settings_and_test_button(digest_env):
    from app import reports

    admin, cust, sent = digest_env
    assert cust.post("/cabinet/digest", data={"on": "1", "hour": "9"}).status_code == 403  # Pro emas
    assert cust.post("/cabinet/digest/test").status_code == 403
    assert "Pro tarifda" in cust.get("/cabinet").text
    make_pro(admin, 2)
    assert cust.post("/cabinet/digest", data={"on": "1", "hour": "24"}).status_code == 400
    r = cust.post("/cabinet/digest", data={"on": "1", "hour": "7"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        a = db.get(Account, 2)
        assert (a.digest_on, a.digest_hour) == (True, 7)
    assert "07:00" in cust.get("/cabinet").text
    seed_calls(2)
    cust.post("/cabinet/digest/test")
    assert len(sent) == 2 and "(sinov)" in sent[0][1] and reports.today_local().strftime("%d.%m.%Y") in sent[0][1]
    nochat = TestClient(app)
    register(nochat, "n@x.uz")
    make_pro(admin, 3)
    r = nochat.post("/cabinet/digest/test", follow_redirects=True)
    assert "Avval Telegram chat ulang" in r.text


def test_scheduler_thread_starts_and_stops():
    from app import scheduler

    t, stop = scheduler.start()
    assert t.is_alive()
    stop.set()
    t.join(3)
    assert not t.is_alive()
