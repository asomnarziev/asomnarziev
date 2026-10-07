"""Payments testlari."""

import pytest

from fastapi.testclient import TestClient

from app import service
from app import telegram
from app.db import SessionLocal
from app.main import app
from app.models import Account
from app.models import Payment
from app.models import now
from tests.helpers import register


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


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
def pay(client, tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "555")
    monkeypatch.setattr(config, "PAYMENT_CARD", "8600123412341234")
    sent = []
    monkeypatch.setattr(telegram, "send_file", lambda chat, cap, data, name, mime, markup=None: sent.append((chat, cap, mime, markup)))
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: sent.append((chat, text, None, markup)))
    monkeypatch.setattr(telegram, "answer_callback", lambda *a: sent.append(("cb", a[1])))
    monkeypatch.setattr(telegram, "clear_buttons", lambda *a: None)
    return sent


def upload(c, data=PNG, name="chek.png", plan="pro", months="3"):
    return c.post("/billing/checkout", data={"plan": plan, "months": months}, files={"receipt": (name, data, "image/png")},
                  follow_redirects=False)


def test_receipt_upload_creates_pending_and_notifies_admin(client, pay):
    from app.models import Payment
    register(client, "c@x.uz")
    page = client.get("/billing").text
    assert "8600 1234 1234 1234" in page and 'data-copy="8600123412341234"' in page  # ko'rinishi guruhlangan, nusxasi bo'sh joysiz
    r = upload(client)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    with SessionLocal() as db:
        p = db.query(Payment).one()
        assert (p.status, p.plan, p.months, p.amount, p.provider) == ("pending", "pro", 3, 249000 * 3, "card")
        assert p.receipt.endswith(".png")
    chat, caption, mime, markup = pay[0]
    assert chat == "555" and "c@x.uz" in caption and "747 000" in caption and mime == "image/png"
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == ["pay:ok:1", "pay:no:1"]
    assert "tekshirilmoqda" in client.get("/billing").text


def test_receipt_rejects_bad_files_and_pending_limit(client, pay):
    from app.models import Payment
    register(client, "c@x.uz")
    assert "Faqat+JPG" in upload(client, b"<script>alert(1)</script>", "x.png").headers["location"]
    assert "Faqat+JPG" in upload(client, b"GIF89a....", "x.png").headers["location"]
    assert "katta" in __import__("urllib.parse").parse.unquote_plus(upload(client, PNG + b"0" * (8 * 1024 * 1024), "big.png").headers["location"])
    assert client.post("/billing/checkout", data={"plan": "bad", "months": "1"}, files={"receipt": ("a.png", PNG)}).status_code == 400
    assert client.post("/billing/checkout", data={"plan": "pro", "months": "7"}, files={"receipt": ("a.png", PNG)}).status_code == 400
    for _ in range(3):
        upload(client)
    assert "ko%27p" in upload(client).headers["location"]
    with SessionLocal() as db:
        assert db.query(Payment).count() == 3


def test_receipt_access_control(client, pay):
    register(client, "admin@x.uz")
    owner, other = TestClient(app), TestClient(app)
    register(owner, "o@x.uz"); register(other, "z@x.uz")
    upload(owner)
    assert owner.get("/receipt/1").headers["content-type"] == "image/png"
    r = client.get("/receipt/1")  # admin ko'ra oladi
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    assert other.get("/receipt/1").status_code == 404
    assert TestClient(app).get("/receipt/1", follow_redirects=False).status_code == 303
    assert client.get("/receipt/999").status_code == 404


def test_admin_confirm_and_reject_with_months(client, pay):
    from app.models import Payment
    register(client, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    cust.post("/cabinet/chat", data={"chat_id": "42", "lang": "ru"})
    upload(cust, months="3"); upload(cust, months="1")
    assert "Chekni ko" in client.get("/admin").text
    pay.clear()
    client.post("/admin/payment/1/confirm")
    with SessionLocal() as db:
        acc = db.query(Account).filter_by(id=2).one()
        assert 89 <= (acc.paid_until - now()).days <= 90 and acc.plan == "pro"  # 3 oy = 90 kun
        assert db.get(Payment, 1).status == "paid"
    assert any(c == "42" and "подтверждён" in t for c, t, *_ in pay if t)  # mijoz chatiga ruscha xabar
    client.post("/admin/payment/2/reject", data={"note": "Summa kam"})
    with SessionLocal() as db:
        p = db.get(Payment, 2)
        assert (p.status, p.note) == ("rejected", "Summa kam")
    assert "Summa kam" in cust.get("/billing").text
    client.post("/admin/payment/1/confirm")  # takror tasdiqlash obunani yana uzaytirmaydi
    with SessionLocal() as db:
        assert (db.get(Account, 2).paid_until - now()).days <= 90


def test_telegram_admin_buttons(client, pay):
    from app.models import Payment
    register(client, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    upload(cust); upload(cust)
    cb = lambda data, chat: {"callback_query": {"id": "9", "data": data, "message": {"message_id": 7, "chat": {"id": chat}}}}
    client.post("/tg/tg-secret", json=cb("pay:ok:1", 999))   # begona chat: e'tiborsiz
    with SessionLocal() as db:
        assert db.get(Payment, 1).status == "pending"
    client.post("/tg/tg-secret", json=cb("pay:ok:1", 555))
    client.post("/tg/tg-secret", json=cb("pay:no:2", 555))
    client.post("/tg/tg-secret", json=cb("pay:ok:2", 555))   # allaqachon ko'rib chiqilgan
    with SessionLocal() as db:
        assert db.get(Payment, 1).status == "paid" and db.get(Payment, 2).status == "rejected"


def test_receipts_path_traversal(tmp_path, monkeypatch):
    from app import config, receipts
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "up"))
    name = receipts.save(PNG)
    assert receipts.path(name).is_file()
    for bad in ("../x.png", "/etc/passwd", "a/../../b.png"):
        with pytest.raises(FileNotFoundError):
            receipts.path(bad)


def test_card_number_with_spaces_in_env(client, monkeypatch):
    import importlib
    from app import config
    monkeypatch.setenv("PAYMENT_CARD", "8600 1234 5678 9012")
    importlib.reload(config)
    assert config.PAYMENT_CARD == "8600123456789012"
    register(client, "k@x.uz")
    page = client.get("/billing").text
    assert "8600 1234 5678 9012" in page and 'data-copy="8600123456789012"' in page


def test_no_receipt_requires_comment(client, pay):
    from urllib.parse import unquote_plus
    from app.models import Payment
    register(client, "c@x.uz")
    # brauzer chek tanlanmasa bo'sh fayl qismi yuboradi
    empty = {"receipt": ("", b"", "application/octet-stream")}
    r = client.post("/billing/checkout", data={"plan": "start", "months": "1", "comment": "   "}, files=empty, follow_redirects=False)
    assert r.status_code == 303 and "izohga" in unquote_plus(r.headers["location"])
    r = client.post("/billing/checkout", data={"plan": "start", "months": "1"}, follow_redirects=False)  # umuman fayl qismi yo'q
    assert "izohga" in unquote_plus(r.headers["location"])
    with SessionLocal() as db:
        assert db.query(Payment).count() == 0 and pay == []


def test_comment_only_payment_notifies_admin_with_buttons(client, pay):
    from app.models import Payment
    register(client, "c@x.uz")
    r = client.post("/billing/checkout", data={"plan": "pro", "months": "1", "comment": "Click orqali o'tkazdim <b>Ali</b>"},
                    files={"receipt": ("", b"", "application/octet-stream")}, follow_redirects=False)
    assert "ok=1" in r.headers["location"]
    with SessionLocal() as db:
        p = db.query(Payment).one()
        assert p.receipt == "" and p.status == "pending" and "Click" in p.comment
    chat, text, mime, markup = pay[0]
    assert chat == "555" and mime is None  # matnli xabar
    assert "Izoh: Click orqali o'tkazdim &lt;b&gt;Ali&lt;/b&gt;" in text and "Chek yuklanmagan" in text  # HTML tozalangan
    assert markup["inline_keyboard"][0][0]["callback_data"] == "pay:ok:1"
    admin = TestClient(app); register(admin, "admin@x.uz")
    page = admin.get("/admin").text
    assert "Click orqali" in page and "chek yuklanmagan" in page and "/receipt/1" not in page
    assert admin.get("/receipt/1").status_code == 404  # fayl yo'q


def test_receipt_with_comment_and_comment_shown_to_customer(client, pay):
    register(client, "c@x.uz")
    r = client.post("/billing/checkout", data={"plan": "start", "months": "1", "comment": "to'lov 14:20"},
                    files={"receipt": ("chek.png", PNG, "image/png")}, follow_redirects=False)
    assert "ok=1" in r.headers["location"]
    assert pay[0][2] == "image/png" and "Izoh: to'lov 14:20" in pay[0][1]
    assert "to'lov 14:20" in client.get("/billing").text.replace("&#39;", "'")


def test_plan_features_and_billing_perks(client):
    from app import config
    register(client, "c@x.uz")
    assert "Hisobotlar" in client.get("/billing").text
    assert config.PLANS["pro"]["features"] == ("reports", "missed_alerts", "search") and config.PLANS["start"]["features"] == ()
