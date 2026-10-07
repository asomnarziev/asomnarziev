import os

os.environ.update(DATABASE_URL="sqlite:///./test.db", ADMIN_EMAIL="admin@x.uz", BOT_USERNAME="b", BASE_URL="http://t")

import pytest
from fastapi.testclient import TestClient

from app import service, telegram
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Account, Chat, Payment, now


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def register(c, email="a@x.uz"):
    return c.post("/register", data={"email": email, "password": "12345678"}, follow_redirects=False)


def test_register_cabinet_and_isolation(client):
    assert register(client).status_code == 303
    assert "Kabinet" in client.get("/cabinet").text
    client.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "secret"})
    with SessionLocal() as db:
        acc = db.query(Account).one()
        assert acc.pbx_domain == "d.onpbx.ru" and "secret" not in acc.pbx_key_enc
    client.post("/cabinet/chat", data={"chat_id": "-100123", "lang": "ru"})
    with SessionLocal() as db:
        assert db.query(Chat).one().lang == "ru"
    # boshqa mijoz birovning chatini o'zgartira olmaydi
    other = TestClient(app)
    register(other, "b@x.uz")
    assert other.post("/cabinet/chat/1/delete", follow_redirects=False).status_code == 404
    assert client.get("/admin").status_code == 403


@pytest.fixture(autouse=True)
def fake_tg(monkeypatch):
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: None)


def test_channel_link_and_username(client, monkeypatch):
    register(client)
    msgs = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: msgs.append((chat, markup)))
    with SessionLocal() as db:
        code = db.query(Account).one().link_code
    client.post("/tg/tg-secret", json={"channel_post": {"text": f"/start {code}", "chat": {"id": -1009, "type": "channel"}}})
    assert msgs == [("-1009", None)]  # kanalga til tugmalari yuborilmaydi
    client.post("/cabinet/chat", data={"chat_id": "@mychannel", "lang": "uz"})
    with SessionLocal() as db:
        assert {c.chat_id for c in db.query(Chat)} == {"-1009", "@mychannel"}


def test_manual_add_rejected_when_bot_cannot_write(client, monkeypatch):
    register(client)
    def boom(*a, **k): raise RuntimeError("403")
    monkeypatch.setattr(telegram, "send_message", boom)
    assert client.post("/cabinet/chat", data={"chat_id": "-1005", "lang": "uz"}).status_code == 400


def test_chat_limit(client):
    register(client)
    for i in range(5):
        client.post("/cabinet/chat", data={"chat_id": str(i + 1), "lang": "uz"})
    with SessionLocal() as db:
        assert db.query(Chat).count() == 3  # sinov limiti


def test_webhook_sends_per_chat_language(client, monkeypatch):
    register(client)
    client.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "k"})
    client.post("/cabinet/chat", data={"chat_id": "1", "lang": "uz"})
    client.post("/cabinet/chat", data={"chat_id": "2", "lang": "ru"})
    sent = []

    class FakePbx:
        def __init__(self, *a): pass
        def call_info(self, u): return {"uuid": u, "caller_id_number": "998", "destination_number": "101",
                                        "accountcode": "inbound", "duration": 5, "start_stamp": 1790000000}
        def record(self, u): return b"mp3"

    monkeypatch.setattr(service, "PbxClient", FakePbx)
    monkeypatch.setattr(telegram, "send_audio", lambda chat, cap, a, fn: sent.append((chat, cap)))
    with SessionLocal() as db:
        token = db.query(Account).one().hook_token
    assert client.post(f"/hook/{token}", data={"uuid": "u1"}).status_code == 200
    client.post(f"/hook/{token}", data={"uuid": "u1"})  # takror — e'tiborsiz
    assert [c for c, _ in sent] == ["1", "2"]
    assert "Qo'ng'iroq yozuvi" in sent[0][1] and "Запись звонка" in sent[1][1]
    assert client.post("/hook/bad", data={"uuid": "u"}).status_code == 404


def test_telegram_link_and_lang(client, monkeypatch):
    register(client)
    msgs = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: msgs.append(text))
    monkeypatch.setattr(telegram, "answer_callback", lambda *a: None)
    with SessionLocal() as db:
        code = db.query(Account).one().link_code
    upd = {"message": {"text": f"/start {code}", "chat": {"id": 77}}}
    client.post("/tg/tg-secret", json=upd)
    client.post("/tg/tg-secret", json={"callback_query": {"id": "1", "data": "lang:ru", "message": {"chat": {"id": 77}}}})
    with SessionLocal() as db:
        assert db.query(Chat).one().lang == "ru"
    assert client.post("/tg/wrong", json=upd).status_code == 404


def test_expired_skips_and_payment_extends(client, monkeypatch):
    register(client)
    with SessionLocal() as db:
        acc = db.query(Account).one()
        acc.trial_ends = now().replace(year=2020)
        db.commit()
        assert not acc.active
        service.process_call(db, acc.id, "u9")
        assert acc.logs[0].status == "skipped"
        p = Payment(account_id=acc.id, plan="pro", amount=1)
        db.add(p); db.commit()
        service.confirm_payment(db, p)
        assert acc.active and acc.max_chats == 20
