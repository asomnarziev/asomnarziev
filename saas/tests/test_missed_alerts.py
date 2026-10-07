"""Missed alerts testlari."""

import pytest
from fastapi.testclient import TestClient

from app import service, telegram
from app.db import SessionLocal
from app.main import app
from app.models import Account
from tests.helpers import make_pro, register

MISSED = {
    "uuid": "m1",
    "caller_id_number": "998901112233",
    "destination_number": "105",
    "accountcode": "inbound",
    "duration": "14",
    "user_talk_time": "0",
    "start_stamp": 1791358224,
}


def run_call(client, monkeypatch, info, pro=True, missed_on=True):
    """Mijoz (account 2) uchun qo'ng'iroqni qayta ishlaydi; (yuborilgan xabarlar, yozuv so'raldimi) qaytaradi."""
    from app.models import CallLog

    admin = client
    register(admin, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
    cust.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "k"})
    cust.post("/cabinet/chat", data={"chat_id": "1", "lang": "uz"})
    cust.post("/cabinet/chat", data={"chat_id": "2", "lang": "ru"})
    if pro:
        make_pro(admin, 2)
        if not missed_on:
            cust.post("/cabinet/missed-alerts", data={})
    sent, rec = [], []

    class FakePbx:
        def __init__(self, *a):
            pass

        def call_info(self, u):
            return info

        def record(self, u):
            rec.append(1)
            return None

    monkeypatch.setattr(service, "PbxClient", FakePbx)
    monkeypatch.setattr(service, "RECORD_RETRY_DELAYS", ())
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: sent.append((chat, text)))
    with SessionLocal() as db:
        service.process_call(db, 2, "m1")
        status = db.query(CallLog).filter_by(account_id=2).one().status
    return sent, rec, status


def test_missed_alert_pro_inbound_sent_in_chat_languages_without_record_fetch(client, monkeypatch):
    sent, rec, status = run_call(client, monkeypatch, MISSED)
    assert rec == [] and status == "sent"  # yozuv yo'q: OnlinePBX API'ga qo'shimcha so'rov yuborilmagan
    assert [c for c, _ in sent] == ["1", "2"]
    assert "Javobsiz qo'ng'iroq" in sent[0][1] and "<code>998901112233</code>" in sent[0][1] and "12:30:24" in sent[0][1]
    assert "Пропущенный звонок" in sent[1][1] and "Звонил: 00:14" in sent[1][1]


@pytest.mark.parametrize(
    "name,kwargs,info",
    [
        ("pro emas", {"pro": False}, MISSED),
        ("ogohlantirish o'chirilgan", {"missed_on": False}, MISSED),
        ("chiquvchi", {}, {**MISSED, "accountcode": "outbound"}),
        ("javob berilgan", {}, {**MISSED, "user_talk_time": "40", "duration": "50"}),
        ("ma'lumot noma'lum", {}, {"uuid": "m1"}),
    ],
)
def test_missed_alert_not_triggered(client, monkeypatch, name, kwargs, info):
    sent, rec, _ = run_call(client, monkeypatch, info, **kwargs)
    assert rec == [1], name  # oddiy oqim: yozuv so'raldi
    assert all("Javobsiz qo'ng'iroq" not in t and "Пропущенный" not in t for _, t in sent), name


def test_missed_alert_toggle_requires_pro(client):
    admin = client
    register(admin, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
    assert cust.post("/cabinet/missed-alerts", data={"on": "1"}).status_code == 403
    make_pro(admin, 2)
    cust.post("/cabinet/missed-alerts", data={})
    with SessionLocal() as db:
        assert db.get(Account, 2).missed_on is False
    cust.post("/cabinet/missed-alerts", data={"on": "1"})
    with SessionLocal() as db:
        assert db.get(Account, 2).missed_on is True
    assert "Javobsiz qo&#39;ng&#39;iroq ogohlantirishi" in cust.get("/cabinet").text.replace("'", "&#39;")
