"""Cabinet testlari."""

from fastapi.testclient import TestClient

from app import service
from app import telegram
from app.db import SessionLocal
from app.main import app
from app.models import Account
from app.models import Chat
from tests.helpers import register


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
    r = client.post("/cabinet/chat", data={"chat_id": "-1005", "lang": "uz"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/cabinet?error=")
    with SessionLocal() as db:
        assert db.query(Chat).count() == 0


def test_chat_limit(client):
    register(client)
    for i in range(5):
        client.post("/cabinet/chat", data={"chat_id": str(i + 1), "lang": "uz"})
    with SessionLocal() as db:
        assert db.query(Chat).count() == 3  # sinov limiti


def test_telegram_link_and_language_only_in_panel(client, monkeypatch):
    register(client)
    msgs = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: msgs.append(markup))
    with SessionLocal() as db:
        code = db.query(Account).one().link_code
    upd = {"message": {"text": f"/start {code}", "chat": {"id": 77}}}
    client.post("/tg/tg-secret", json=upd)
    assert msgs == [None]  # Telegramda til tugmalari yo'q
    client.post("/cabinet/chat/1/lang", data={"lang": "ru"})
    with SessionLocal() as db:
        assert db.query(Chat).one().lang == "ru"
    assert client.post("/tg/wrong", json=upd).status_code == 404


def test_each_tenant_has_own_hook_and_regenerate(client):
    register(client)
    other = TestClient(app)
    register(other, "b@x.uz")
    h1, h2 = (c.get("/cabinet").text.split("/hook/")[1].split("<")[0] for c in (client, other))
    assert h1 != h2 and len(h1) > 20
    assert "http://t/hook/" in client.get("/cabinet").text
    client.post("/cabinet/hook/regenerate")
    assert h1 not in client.get("/cabinet").text
    assert client.post(f"/hook/{h1}", data={"uuid": "x"}).status_code == 404


def test_id_command_and_no_admin_chat(client, monkeypatch):
    from app import config
    out = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: out.append((chat, text)))
    client.post("/tg/tg-secret", json={"message": {"text": "/id", "chat": {"id": 12345}}})
    assert out == [("12345", "Chat ID: <code>12345</code>")]
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "")
    service.notify_admin_payment(1)  # sozlanmagan bo'lsa jim o'tadi
    assert len(out) == 1


def test_manual_chat_errors_are_shown_on_page_with_telegram_reason(client, monkeypatch):
    register(client, "c@x.uz")
    monkeypatch.undo()  # umumiy soxta send_message'ni olib tashlaymiz: haqiqiy telegram._call sinaladi

    class Resp:
        status_code = 400
        def json(self): return {"ok": False, "description": "Bad Request: chat not found"}
        def raise_for_status(self): raise AssertionError("raise_for_status ishlatilmasligi kerak")

    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: Resp())
    r = client.post("/cabinet/chat", data={"chat_id": "123456", "lang": "uz"}, follow_redirects=True)
    page = r.text.replace("&#39;", "'")  # Jinja ' ni &#39; ga aylantiradi, brauzer to'g'ri ko'rsatadi
    assert r.status_code == 200 and "Chat qo'shilmadi" in page
    assert "Chat topilmadi" in page and "chat not found" in page  # tushuntirish + Telegram sababi
    assert '{"detail"' not in r.text  # xom JSON emas
    bad = client.post("/cabinet/chat", data={"chat_id": "+998901234567", "lang": "uz"}, follow_redirects=True)
    assert "Telefon raqam" in bad.text


def test_telegram_explain_hints():
    e = telegram.TelegramError("Forbidden: bot can't initiate conversation with a user")
    assert "/start" in telegram.explain(e)
    assert "administrator" in telegram.explain(telegram.TelegramError("Forbidden: bot is not a member of the channel chat"))
    assert "Telegram: Something odd" in telegram.explain(telegram.TelegramError("Something odd"))
