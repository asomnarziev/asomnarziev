"""Webhook testlari."""

from app import service
from app import telegram
from app.db import SessionLocal
from app.models import Account
from tests.helpers import register


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


def test_record_retried_until_ready(monkeypatch):
    calls = []

    class Pbx:
        def record(self, u):
            calls.append(1)
            return b"mp3" if len(calls) == 3 else None

    sleeps = []
    monkeypatch.setattr(service.time, "sleep", sleeps.append)
    assert service.fetch_record(Pbx(), "u") == b"mp3"
    assert len(calls) == 3 and sleeps == [5, 15]


def test_webhook_without_uuid_is_ok(client):
    register(client)
    with SessionLocal() as db:
        token = db.query(Account).one().hook_token
    r = client.post(f"/hook/{token}", data={"event": "test"})
    assert r.status_code == 200 and r.json()["ignored"] is True
    with SessionLocal() as db:
        assert db.query(Account).one().logs == []  # log qatori yaratilmaydi


def test_daily_call_cap(client, monkeypatch):
    from app import config
    register(client)
    monkeypatch.setattr(config, "MAX_CALLS_PER_DAY", 2)
    with SessionLocal() as db:
        acc = db.query(Account).one()
        for u in ("a", "b", "c"):
            service.process_call(db, acc.id, u)  # pbx sozlanmagan: error; lekin hisoblanadi
        assert [l.status for l in sorted(acc.logs, key=lambda l: l.id)][-1] == "skipped"


def test_call_stats_stored_even_if_telegram_fails(client, monkeypatch):
    from app.models import CallLog
    register(client, "c@x.uz")
    client.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "k"})
    client.post("/cabinet/chat", data={"chat_id": "1", "lang": "uz"})

    class FakePbx:
        def __init__(self, *a): pass
        def call_info(self, u): return {"uuid": u, "caller_id_number": "105", "destination_number": "935033635",
                                        "accountcode": "outbound", "duration": "44", "user_talk_time": "40", "start_stamp": 1791358224}
        def record(self, u): return b"mp3"

    def boom(*a, **k): raise RuntimeError("telegram down")
    monkeypatch.setattr(service, "PbxClient", FakePbx)
    monkeypatch.setattr(telegram, "send_audio", boom)
    with SessionLocal() as db:
        service.process_call(db, 1, "u1")
        row = db.query(CallLog).one()
        assert row.status == "error"  # yuborilmadi, lekin statistika saqlangan
        assert (row.direction, row.caller, row.callee, row.duration, row.talk) == ("outbound", "105", "935033635", 44, 40)
        assert row.started_at.isoformat() == "2026-10-07T07:30:24"  # UTC


def test_process_call_publishes_live_event(client, monkeypatch):
    import asyncio
    from app import live
    register(client, "c@x.uz")
    client.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "k"})
    client.post("/cabinet/chat", data={"chat_id": "1", "lang": "uz"})
    monkeypatch.setattr(service, "PbxClient", type("F", (), {
        "__init__": lambda s, *a: None, "call_info": lambda s, u: {"uuid": u, "caller_id_number": "9", "destination_number": "105",
                                                                   "accountcode": "inbound", "duration": 5, "user_talk_time": 5, "start_stamp": 1791358224},
        "record": lambda s, u: b"mp3"}))
    monkeypatch.setattr(telegram, "send_audio", lambda *a: None)

    async def scenario():
        sub = live.subscribe(1, asyncio.get_running_loop())
        def work():
            with SessionLocal() as db:
                service.process_call(db, 1, "live1")
        await asyncio.get_running_loop().run_in_executor(None, work)  # webhook fon vazifasi kabi boshqa oqimda
        ev = await asyncio.wait_for(sub.queue.get(), 2)
        live.unsubscribe(sub)
        return ev
    ev = asyncio.run(scenario())
    assert ev["direction"] == "inbound" and ev["id"] >= 1
