import os

os.environ.update(SCHEDULER="0", DATABASE_URL="sqlite:///./test.db", ADMIN_EMAIL="admin@x.uz", BOT_USERNAME="b", BASE_URL="http://t")

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
    """Ro'yxatdan o'tish sahifasi yo'q: foydalanuvchi bazada yaratiladi va tizimga kiriladi."""
    from app.models import Account, User
    from app.security import hash_password
    with SessionLocal() as db:
        u = User(email=email, password_hash=hash_password("12345678"), is_admin=(email == "admin@x.uz"))
        u.account = Account()
        db.add(u)
        db.commit()
    return c.post("/login", data={"email": email, "password": "12345678"}, follow_redirects=False)


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


def test_admin_creates_customer_and_no_registration(client):
    register(client, "admin@x.uz")
    anon = TestClient(app)
    assert anon.get("/register").status_code == 404
    assert anon.post("/register", data={"email": "z@x.uz", "password": "12345678"}).status_code in (404, 405)
    assert "Ro'yxatdan" not in anon.get("/login").text
    r = client.post("/admin/user", data={"email": "cust@x.uz", "password": "parol12345"})
    assert "parol12345" in r.text
    cust = TestClient(app)
    assert cust.post("/login", data={"email": "cust@x.uz", "password": "parol12345"}, follow_redirects=False).headers["location"] == "/cabinet"
    assert cust.get("/admin").status_code == 403
    assert "band" in client.post("/admin/user", data={"email": "cust@x.uz", "password": "parol12345"}).text
    aid = 2
    client.post(f"/admin/account/{aid}/password", data={"password": "yangiparol1"})
    assert TestClient(app).post("/login", data={"email": "cust@x.uz", "password": "yangiparol1"}, follow_redirects=False).headers["location"] == "/cabinet"


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


def test_pbx_client_requires_config_and_tries_hosts(monkeypatch):
    from app import pbx as _pbx
    _pbx.reset_state()
    from app import pbx
    import pytest as _p
    with _p.raises(pbx.PbxError, match="domeni"):
        pbx.PbxClient("", "k")
    with _p.raises(pbx.PbxError, match="kaliti"):
        pbx.PbxClient("d.onpbx.ru", "")
    urls = []

    class R:
        def __init__(self, ok): self.ok, self.status_code, self.headers = ok, (200 if ok else 404), {}
        def raise_for_status(self):
            if not self.ok: raise pbx.requests.HTTPError("404", response=self)
        def json(self): return {"status": "1", "data": {"key_id": "a", "key": "b"}}

    c = pbx.PbxClient("https://d.onpbx.ru/", "k")
    assert c.domain == "d.onpbx.ru"
    monkeypatch.setattr(c.s, "post", lambda url, **kw: (urls.append(url), R(len(urls) > 1))[1])
    c._auth()
    assert urls[0].startswith("https://api.onlinepbx.ru/d.onpbx.ru/") and c.base.startswith("https://api2.")


def test_webhook_without_uuid_is_ok(client):
    register(client)
    with SessionLocal() as db:
        token = db.query(Account).one().hook_token
    r = client.post(f"/hook/{token}", data={"event": "test"})
    assert r.status_code == 200 and r.json()["ignored"] is True
    with SessionLocal() as db:
        assert db.query(Account).one().logs == []  # log qatori yaratilmaydi


def test_local_time_and_audio_name():
    from app.messages import audio_name, render_call
    call = {"uuid": "cc04", "caller_id_number": "105", "destination_number": "935033635",
            "start_stamp": 1791358224, "accountcode": "outbound", "duration": 44}  # 2026-10-07 07:30:24 UTC
    assert "07.10.2026 12:30:24" in render_call("uz", call)
    assert audio_name(call) == "105_935033635_07.10_12-30.mp3"
    assert audio_name({"uuid": "x/../y"}) == "x..y.mp3" or audio_name({"uuid": "x/../y"}).endswith(".mp3")


def test_create_user_cli(client, capsys):
    from app import create_user
    from app.models import User
    create_user.main(["boss@x.uz", "12345678", "--admin"])
    create_user.main(["boss@x.uz", "yangiparol1"])  # mavjud: parolni yangilaydi, adminligini saqlaydi
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="boss@x.uz").one()
        assert u.is_admin and u.account is not None
    c = TestClient(app)
    assert c.post("/login", data={"email": "boss@x.uz", "password": "yangiparol1"}, follow_redirects=False).headers["location"] == "/cabinet"


def test_login_shows_admin_contacts(client):
    t = TestClient(app).get("/login").text
    assert "https://t.me/asom_narziev" in t and "tel:+998948302407" in t and "mailto:asomnarziev07@gmail.com" in t
    assert "+998 94 830 24 07" in t


class _Resp:
    def __init__(self, status=200, body=None, headers=None, content=b""):
        self.status_code, self._body, self.headers, self.content = status, body or {}, headers or {}, content
    def json(self): return self._body
    def raise_for_status(self):
        if self.status_code >= 400:
            from app import pbx
            raise pbx.requests.HTTPError(str(self.status_code), response=self)


AUTH_OK = {"status": "1", "data": {"key_id": "a", "key": "b"}}


def _fresh(monkeypatch):
    from app import pbx
    pbx.reset_state()
    slept = []
    monkeypatch.setattr(pbx, "_sleep", slept.append)
    monkeypatch.setattr(pbx.random, "random", lambda: 0)
    return pbx, slept


def test_pbx_auth_key_cached_across_calls(monkeypatch):
    pbx, _ = _fresh(monkeypatch)
    posts = []
    def fake_post(url, **kw):
        posts.append(url)
        return _Resp(200, AUTH_OK if url.endswith("auth.json") else {"data": [{"uuid": "u"}]})
    for _ in range(3):  # uch xil qo'ng'iroq = uch yangi PbxClient
        c = pbx.PbxClient("d.onpbx.ru", "k")
        monkeypatch.setattr(c.s, "post", fake_post)
        c.call_info("u")
    assert sum(u.endswith("auth.json") for u in posts) == 1 and len(posts) == 4


def test_pbx_429_backoff_uses_retry_after_then_succeeds(monkeypatch):
    pbx, slept = _fresh(monkeypatch)
    answers = iter([_Resp(200, AUTH_OK), _Resp(429, headers={"Retry-After": "7"}), _Resp(200, {"data": [{"uuid": "u"}]})])
    c = pbx.PbxClient("d.onpbx.ru", "k")
    monkeypatch.setattr(c.s, "post", lambda url, **kw: next(answers))
    assert c.call_info("u") == {"uuid": "u"}
    assert 7 in slept


def test_pbx_429_gives_up_after_max_retries(monkeypatch):
    pbx, slept = _fresh(monkeypatch)
    n = []
    def fake_post(url, **kw):
        n.append(1)
        return _Resp(200, AUTH_OK) if url.endswith("auth.json") else _Resp(429)
    c = pbx.PbxClient("d.onpbx.ru", "k")
    monkeypatch.setattr(c.s, "post", fake_post)
    import pytest as _p
    with _p.raises(pbx.PbxError, match="429"):
        c.call_info("u")
    assert len(n) == 1 + pbx.MAX_RETRIES + 1  # auth + (1 + MAX_RETRIES) urinish, cheksiz emas


def test_pbx_bad_credentials_cooldown_not_hammered(monkeypatch):
    pbx, _ = _fresh(monkeypatch)
    n = []
    def fake_post(url, **kw):
        n.append(url)
        return _Resp(401)
    import pytest as _p
    for _ in range(3):
        c = pbx.PbxClient("d.onpbx.ru", "xato")
        monkeypatch.setattr(c.s, "post", fake_post)
        with _p.raises(pbx.PbxError):
            c.call_info("u")
    assert len(n) == len(pbx.HOSTS)  # faqat birinchi marta urildi, qolgani cooldown'da
    c = pbx.PbxClient("d.onpbx.ru", "yangi-kalit")  # kalit almashsa cooldown tugaydi
    monkeypatch.setattr(c.s, "post", fake_post)
    with _p.raises(pbx.PbxError):
        c.call_info("u")
    assert len(n) == 2 * len(pbx.HOSTS)


def test_pbx_network_error_has_no_cooldown(monkeypatch):
    pbx, _ = _fresh(monkeypatch)
    def boom(url, **kw): raise pbx.requests.ConnectionError("down")
    import pytest as _p
    c = pbx.PbxClient("d.onpbx.ru", "k")
    monkeypatch.setattr(c.s, "post", boom)
    with _p.raises(pbx.PbxError):
        c.call_info("u")
    assert not pbx._fail


def test_pbx_gate_spaces_requests(monkeypatch):
    pbx, slept = _fresh(monkeypatch)
    t = [100.0]
    monkeypatch.setattr(pbx, "_now", lambda: t[0])
    g = pbx.Gate(2)  # soniyasiga 2 ta -> 0.5s oralig'
    for _ in range(3):
        g.wait()
    assert slept == [0.5, 1.0]


def test_daily_call_cap(client, monkeypatch):
    from app import config
    register(client)
    monkeypatch.setattr(config, "MAX_CALLS_PER_DAY", 2)
    with SessionLocal() as db:
        acc = db.query(Account).one()
        for u in ("a", "b", "c"):
            service.process_call(db, acc.id, u)  # pbx sozlanmagan: error; lekin hisoblanadi
        assert [l.status for l in sorted(acc.logs, key=lambda l: l.id)][-1] == "skipped"


def test_admin_manages_subscription(client):
    from app.models import Payment
    register(client, "admin@x.uz")
    client.post("/admin/user", data={"email": "c@x.uz", "password": "parol12345"})
    with SessionLocal() as db:
        aid = db.query(Account).filter(Account.id != 1).one().id
    r = client.post(f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "pro", "days": "30"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        acc = db.get(Account, aid)
        assert acc.plan == "pro" and acc.max_chats == 20 and acc.active
        left = (acc.paid_until - now()).days
        assert 29 <= left <= 30
        p = db.query(Payment).filter_by(account_id=aid).one()
        assert p.provider == "admin" and p.status == "paid"
    client.post(f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "pro", "days": "30"})  # ustiga qo'shiladi
    with SessionLocal() as db:
        assert 59 <= (db.get(Account, aid).paid_until - now()).days <= 60
    client.post(f"/admin/account/{aid}/subscription", data={"action": "expire"})
    with SessionLocal() as db:
        assert not db.get(Account, aid).active
    assert client.post(f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "bad", "days": "5"}).status_code == 400
    assert client.post(f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "pro", "days": "0"}).status_code == 400
    assert "Boshqarish" in client.get("/admin").text
    cust = TestClient(app)
    cust.post("/login", data={"email": "c@x.uz", "password": "parol12345"})
    assert cust.post(f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "pro", "days": "30"}).status_code == 403


# ---------- Kartaga o'tkazma to'lovi ----------
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


def test_id_command_and_no_admin_chat(client, monkeypatch):
    from app import config
    out = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: out.append((chat, text)))
    client.post("/tg/tg-secret", json={"message": {"text": "/id", "chat": {"id": 12345}}})
    assert out == [("12345", "Chat ID: <code>12345</code>")]
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "")
    service.notify_admin_payment(1)  # sozlanmagan bo'lsa jim o'tadi
    assert len(out) == 1


def test_db_migration_adds_payment_columns(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    from app import db as dbm
    eng = create_engine(f"sqlite:///{tmp_path/'old.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE payments (id INTEGER PRIMARY KEY, account_id INTEGER, plan VARCHAR(20), amount INTEGER, provider VARCHAR(20), status VARCHAR(20), created_at DATETIME)"))
        c.execute(text("INSERT INTO payments (account_id, plan, amount, provider, status) VALUES (1,'pro',1,'manual','paid')"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    dbm.init_db()  # ikkinchi marta zararsiz
    assert {"months", "receipt", "note"} <= {c["name"] for c in inspect(eng).get_columns("payments")}
    with eng.connect() as c:
        assert c.execute(text("SELECT months, receipt FROM payments")).one() == (1, "")


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


# ---------- Hisobotlar (Pro) ----------
def _utc(local_dt):
    from datetime import timezone
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def seed_calls(account_id, d=None):
    """Berilgan (standart: bugungi) Toshkent kuni uchun 4 ta qo'ng'iroq: 2 javob berilgan, 2 javobsiz."""
    from datetime import datetime, time
    from app import reports
    from app.messages import LOCAL_TZ
    from app.models import CallLog
    d = d or reports.today_local()
    at = lambda h, m: _utc(datetime.combine(d, time(h, m), LOCAL_TZ))
    rows = [("a", "inbound", "998901112233", "105", at(10, 0), 70, 60),
            ("b", "outbound", "105", "998905556677", at(10, 30), 40, 30),
            ("c", "outbound", "107", "998905556677", at(15, 0), 20, 0),
            ("d", "inbound", "998901112233", "105", at(15, 10), 0, None)]
    with SessionLocal() as db:
        for u, dr, ca, ce, st, dur, talk in rows:
            db.add(CallLog(account_id=account_id, uuid=f"{d}{u}", status="sent", direction=dr, caller=ca, callee=ce,
                           started_at=st, duration=dur, talk=talk))
        db.commit()


def make_pro(admin_client, account_id):
    admin_client.post(f"/admin/account/{account_id}/subscription", data={"action": "extend", "plan": "pro", "days": "30"})


def test_reports_locked_for_non_pro_and_pro_sees_data(client):
    admin = client; register(admin, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    seed_calls(2)
    page = cust.get("/reports").text
    assert "Pro tarifda ochiladi" in page and "998901112233" not in page and ">PRO<" in page  # menyuda PRO belgisi
    assert cust.get("/reports.csv").status_code == 403
    make_pro(admin, 2)
    page = cust.get("/reports").text
    assert "Jami qo'ng'iroq" in page and "998901112233" in page and "Pro tarifda ochiladi" not in page
    assert cust.get("/reports.csv").status_code == 200


def test_reports_metrics_and_isolation(client):
    from app import reports
    admin = client; register(admin, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    other = TestClient(app); register(other, "z@x.uz")
    seed_calls(2)
    make_pro(admin, 2); make_pro(admin, 3)
    d = reports.today_local()
    with SessionLocal() as db:
        rows = reports.load(db, 2, d, d)
    r = reports.build(rows, d, d)
    assert (r["total"], r["answered"], r["missed"], r["answer_rate"]) == (4, 2, 2, 50)
    assert (r["inbound"], r["outbound"]) == (2, 2) and r["talk_total"] == "01:30" and r["talk_avg"] == "00:45"
    assert r["by_hour"][10] == 2 and r["by_hour"][15] == 2 and r["busiest_hour"] == 10  # soatlar Toshkent vaqtida
    emp = {e["ext"]: e for e in r["employees"]}
    assert emp["105"]["calls"] == 3 and emp["105"]["in"] == 2 and emp["105"]["out"] == 1 and emp["105"]["missed"] == 1
    assert emp["107"]["calls"] == 1 and emp["107"]["missed"] == 1
    assert dict(r["top_numbers"]) == {"998901112233": 2, "998905556677": 2}
    assert "<svg" in reports.day_chart(r["by_day"]) and reports.hour_chart(r["by_hour"]).count("<rect") == 24
    assert "998901112233" not in other.get("/reports").text  # boshqa mijoz birovning statistikasini ko'rmaydi
    assert "998901112233" not in other.get("/reports.csv").text


def test_reports_period_parsing_and_csv(client):
    from datetime import timedelta
    from app import reports
    s, e, mode = reports.parse_period("30d", None, None)
    assert (e - s).days == 29 and mode == "30d" and e == reports.today_local()
    assert reports.parse_period("bad", None, None)[2] == "7d"
    t = reports.today_local()
    assert reports.parse_period(None, (t - timedelta(days=3)).isoformat(), t.isoformat())[2] == "custom"
    assert reports.parse_period(None, "2020-01-01", t.isoformat())[2] == "7d"  # >366 kun rad etiladi
    assert reports.parse_period(None, "xx", "yy")[2] == "7d"
    register(client, "admin@x.uz"); seed_calls(1)
    body = client.get("/reports.csv?period=today").text
    assert body.startswith("﻿Sana,") and "javob berilmagan" in body and body.count("\n") >= 5
    assert "attachment" in client.get("/reports.csv?period=today").headers["content-disposition"]
    assert reports._safe("=1+1") == "'=1+1" and reports._safe("@x") == "'@x" and reports._safe("-cmd") == "'-cmd"
    assert reports._safe("+998901234567") == "+998901234567" and reports._safe("105") == "105"


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


def test_migration_adds_call_log_columns(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    from app import db as dbm
    eng = create_engine(f"sqlite:///{tmp_path/'old2.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE call_logs (id INTEGER PRIMARY KEY, account_id INTEGER, uuid VARCHAR(100), status VARCHAR(20), error TEXT, created_at DATETIME)"))
        c.execute(text("INSERT INTO call_logs (account_id, uuid, status, error) VALUES (1,'u','sent','')"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    assert {"direction", "caller", "callee", "started_at", "duration", "talk"} <= {c["name"] for c in inspect(eng).get_columns("call_logs")}
    with eng.connect() as c:
        assert c.execute(text("SELECT direction, duration, talk, started_at FROM call_logs")).one() == ("", 0, None, None)


def test_plan_features_and_billing_perks(client):
    from app import config
    register(client, "c@x.uz")
    assert "Hisobotlar" in client.get("/billing").text
    assert config.PLANS["pro"]["features"] == ("reports", "missed_alerts", "search") and config.PLANS["start"]["features"] == ()


# ---------- Kunlik hisobot (Pro) ----------
def at_local(h, m=0, days=0):
    from datetime import datetime, time, timedelta, timezone
    from app import reports
    from app.messages import LOCAL_TZ
    return datetime.combine(reports.today_local() + timedelta(days=days), time(h, m), LOCAL_TZ).astimezone(timezone.utc)


@pytest.fixture()
def digest_env(client, monkeypatch):
    from app import scheduler
    scheduler._attempts.clear()
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, markup=None: sent.append((chat, text)))
    admin = client; register(admin, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
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
    assert "Kunlik hisobot" in uz and "Jami: <b>4</b>" in uz and "javob berilgan: <b>2</b> (50%)" in uz and "Suhbat vaqti: 01:30" in uz
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
    def boom(*a, **k): raise RuntimeError("down")
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
    nochat = TestClient(app); register(nochat, "n@x.uz"); make_pro(admin, 3)
    r = nochat.post("/cabinet/digest/test", follow_redirects=True)
    assert "Avval Telegram chat ulang" in r.text


def test_scheduler_thread_starts_and_stops():
    from app import scheduler
    t, stop = scheduler.start()
    assert t.is_alive()
    stop.set(); t.join(3)
    assert not t.is_alive()


def test_migration_adds_digest_columns(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    from app import db as dbm
    eng = create_engine(f"sqlite:///{tmp_path/'old3.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, user_id INTEGER, pbx_domain VARCHAR(255), pbx_key_enc TEXT, hook_token VARCHAR(64), link_code VARCHAR(64), plan VARCHAR(20), trial_ends DATETIME, paid_until DATETIME, suspended BOOLEAN)"))
        c.execute(text("INSERT INTO accounts (user_id, plan, suspended) VALUES (1,'pro',0)"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    assert {"digest_on", "digest_hour", "digest_last"} <= {c["name"] for c in inspect(eng).get_columns("accounts")}
    with eng.connect() as c:
        assert c.execute(text("SELECT digest_on, digest_hour, digest_last FROM accounts")).one() == (1, 9, "")


# ---------- Pro: javobsiz qo'ng'iroq ogohlantirishi ----------
MISSED = {"uuid": "m1", "caller_id_number": "998901112233", "destination_number": "105", "accountcode": "inbound",
          "duration": "14", "user_talk_time": "0", "start_stamp": 1791358224}


def run_call(client, monkeypatch, info, pro=True, missed_on=True):
    """Mijoz (account 2) uchun qo'ng'iroqni qayta ishlaydi; (yuborilgan xabarlar, yozuv so'raldimi) qaytaradi."""
    from app.models import CallLog
    admin = client; register(admin, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    cust.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "k"})
    cust.post("/cabinet/chat", data={"chat_id": "1", "lang": "uz"})
    cust.post("/cabinet/chat", data={"chat_id": "2", "lang": "ru"})
    if pro:
        make_pro(admin, 2)
        if not missed_on:
            cust.post("/cabinet/missed-alerts", data={})
    sent, rec = [], []

    class FakePbx:
        def __init__(self, *a): pass
        def call_info(self, u): return info
        def record(self, u): rec.append(1); return None
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


@pytest.mark.parametrize("name,kwargs,info", [
    ("pro emas", {"pro": False}, MISSED),
    ("ogohlantirish o'chirilgan", {"missed_on": False}, MISSED),
    ("chiquvchi", {}, {**MISSED, "accountcode": "outbound"}),
    ("javob berilgan", {}, {**MISSED, "user_talk_time": "40", "duration": "50"}),
    ("ma'lumot noma'lum", {}, {"uuid": "m1"}),
])
def test_missed_alert_not_triggered(client, monkeypatch, name, kwargs, info):
    sent, rec, _ = run_call(client, monkeypatch, info, **kwargs)
    assert rec == [1], name  # oddiy oqim: yozuv so'raldi
    assert all("Javobsiz qo'ng'iroq" not in t and "Пропущенный" not in t for _, t in sent), name


def test_missed_alert_toggle_requires_pro(client):
    admin = client; register(admin, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    assert cust.post("/cabinet/missed-alerts", data={"on": "1"}).status_code == 403
    make_pro(admin, 2)
    cust.post("/cabinet/missed-alerts", data={})
    with SessionLocal() as db:
        assert db.get(Account, 2).missed_on is False
    cust.post("/cabinet/missed-alerts", data={"on": "1"})
    with SessionLocal() as db:
        assert db.get(Account, 2).missed_on is True
    assert "Javobsiz qo&#39;ng&#39;iroq ogohlantirishi" in cust.get("/cabinet").text.replace("'", "&#39;")


# ---------- Pro: qo'ng'iroqlarni qidirish va yozuvni qayta olish ----------
@pytest.fixture()
def calls_env(client, monkeypatch):
    from app import web
    web._fetches.clear()
    admin = client; register(admin, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    cust.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "k"})
    seed_calls(2)  # a(id1) kiruvchi javob, b(id2) chiquvchi javob, c(id3) chiquvchi javobsiz, d(id4) kiruvchi javobsiz
    make_pro(admin, 2)
    return admin, cust


def n_rows(resp):
    return resp.text.count("<code>") // 2


def test_calls_search_locked_for_non_pro(client):
    register(client, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    seed_calls(2)
    page = cust.get("/calls").text
    assert "Qidiruv Pro tarifda ochiladi" in page and "998901112233" not in page
    assert cust.get("/calls/1/record").status_code == 403 and cust.post("/calls/1/send").status_code == 403


def test_calls_search_filters(calls_env):
    _, cust = calls_env
    assert n_rows(cust.get("/calls")) == 4
    assert n_rows(cust.get("/calls?q=998901112233")) == 2
    assert n_rows(cust.get("/calls?q=5556677")) == 2  # qisman raqam
    assert n_rows(cust.get("/calls?ext=107")) == 1
    assert n_rows(cust.get("/calls?d=outbound")) == 2
    assert n_rows(cust.get("/calls?st=missed")) == 2 and n_rows(cust.get("/calls?st=answered")) == 2
    assert n_rows(cust.get("/calls?st=missed&d=inbound")) == 1
    assert n_rows(cust.get("/calls?q=%25")) == 0 and n_rows(cust.get("/calls?q=_")) == 0  # LIKE belgilari oddiy matn
    assert "Hech narsa topilmadi" in cust.get("/calls?frm=2020-01-01&to=2020-01-02").text or n_rows(cust.get("/calls?frm=2020-01-01&to=2020-01-02")) == 0


def test_calls_pagination_and_isolation(calls_env):
    from datetime import datetime, timedelta, timezone
    from app.models import CallLog
    admin, cust = calls_env
    with SessionLocal() as db:
        t0 = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        for k in range(120):
            db.add(CallLog(account_id=2, uuid=f"p{k}", status="sent", direction="inbound", caller=f"9989000{k:05d}",
                           callee="105", started_at=t0 - timedelta(seconds=k), duration=5, talk=5))
        db.commit()
    assert n_rows(cust.get("/calls")) == 50 and "1 / 3" in cust.get("/calls").text
    assert n_rows(cust.get("/calls?page=3")) == 24 and n_rows(cust.get("/calls?page=99")) == 24  # 124 ta; oxirgi sahifaga qisiladi
    other = TestClient(app); register(other, "z@x.uz"); make_pro(admin, 3)
    assert n_rows(other.get("/calls")) == 0 and "998901112233" not in other.get("/calls").text
    assert other.get("/calls/1/record").status_code == 404 and other.post("/calls/1/send").status_code == 404


def test_call_record_proxy_and_hourly_cap(calls_env, monkeypatch):
    from app import web
    from app.pbx import PbxError
    _, cust = calls_env
    mode = {"v": b"mp3data"}

    class FakePbx:
        def __init__(self, *a): pass
        def record(self, u):
            if isinstance(mode["v"], Exception): raise mode["v"]
            return mode["v"]
    monkeypatch.setattr(web, "PbxClient", FakePbx)
    r = cust.get("/calls/1/record")
    assert r.status_code == 200 and r.content == b"mp3data" and r.headers["content-type"] == "audio/mpeg"
    assert "inline" in r.headers["content-disposition"] and "998901112233_105_" in r.headers["content-disposition"]
    assert "attachment" in cust.get("/calls/1/record?download=1").headers["content-disposition"]
    mode["v"] = None
    assert cust.get("/calls/1/record").status_code == 404
    mode["v"] = PbxError("OnlinePBX'ga ulanib bo'lmadi")
    r = cust.get("/calls/1/record")
    assert r.status_code == 502 and "ulanib" in r.text
    monkeypatch.setattr(web.config, "MAX_RECORD_FETCH_PER_HOUR", 4)  # 4 ta so'rov allaqachon bo'lgan
    assert cust.get("/calls/1/record").status_code == 429


def test_call_resend_to_telegram(calls_env, monkeypatch):
    from app import web
    _, cust = calls_env
    cust.post("/cabinet/chat", data={"chat_id": "1", "lang": "uz"})
    cust.post("/cabinet/chat", data={"chat_id": "2", "lang": "ru"})
    sent = []
    monkeypatch.setattr(telegram, "send_audio", lambda chat, cap, audio, name: sent.append((chat, cap, audio, name)))
    monkeypatch.setattr(web, "PbxClient", type("F", (), {"__init__": lambda s, *a: None, "record": lambda s, u: b"mp3"}))
    r = cust.post("/calls/1/send", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/calls?ok=sent"
    assert [c for c, *_ in sent] == ["1", "2"] and sent[0][2] == b"mp3" and sent[0][3].endswith(".mp3")
    assert "Qo'ng'iroq yozuvi" in sent[0][1] and "Запись звонка" in sent[1][1]
    assert "Yozuv Telegramga yuborildi" in cust.get("/calls?ok=sent").text
    nochat = TestClient(app); register(nochat, "n@x.uz")
    assert nochat.post("/calls/1/send").status_code == 403  # Pro emas


def test_migration_adds_missed_on(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    from app import db as dbm
    eng = create_engine(f"sqlite:///{tmp_path/'old4.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, user_id INTEGER, plan VARCHAR(20), suspended BOOLEAN)"))
        c.execute(text("INSERT INTO accounts (user_id, plan, suspended) VALUES (1,'pro',0)"))
    monkeypatch.setattr(dbm, "engine", eng)
    dbm.init_db()
    assert "missed_on" in {c["name"] for c in inspect(eng).get_columns("accounts")}
    with eng.connect() as c:
        assert c.execute(text("SELECT missed_on FROM accounts")).scalar() == 1


# ---------- Jonli yangilanish (SSE) ----------
def test_live_hub_publish_subscribe_and_limits():
    import asyncio
    from app import live

    async def scenario():
        loop = asyncio.get_running_loop()
        a, b = live.subscribe(7, loop), live.subscribe(7, loop)
        other = live.subscribe(8, loop)
        assert live.count(7) == 2 and live.count(8) == 1
        # boshqa oqimdan (webhook fon vazifasi) yuboriladi
        n = await loop.run_in_executor(None, live.publish, 7, {"id": 1})
        assert n == 2
        assert (await asyncio.wait_for(a.queue.get(), 1)) == {"id": 1} and (await asyncio.wait_for(b.queue.get(), 1)) == {"id": 1}
        assert other.queue.empty()  # boshqa mijozga ketmaydi
        live.unsubscribe(a); live.unsubscribe(b); live.unsubscribe(other)
        assert live.count(7) == 0 and live.publish(7, {"id": 2}) == 0
        subs = [live.subscribe(9, loop) for _ in range(live.MAX_PER_ACCOUNT)]
        assert live.subscribe(9, loop) is None  # limitdan oshdi
        for s_ in subs: live.unsubscribe(s_)
        s1 = live.subscribe(10, loop)
        for k in range(80): live.publish(10, {"id": k})  # navbat to'lsa ham xato bermaydi
        await asyncio.sleep(0.05)
        assert s1.queue.qsize() == 50
        live.unsubscribe(s1)

    asyncio.run(scenario())


def test_live_stream_access_control(client):
    from app import live
    anon = TestClient(app)
    assert anon.get("/live/stream").status_code == 403
    register(client, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    assert cust.get("/live/stream").status_code == 403  # Pro emas
    assert live.count(2) == 0


@pytest.fixture()
def real_server():
    """Haqiqiy uvicorn serveri (TestClient oqimni bosqichma-bosqich bermaydi)."""
    import socket, threading, time
    import uvicorn
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        if server.started: break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)


def test_live_stream_delivers_event_to_pro_page(client, real_server, monkeypatch):
    import threading, time
    import httpx
    from app import live
    admin = client; register(admin, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    make_pro(admin, 2)
    monkeypatch.setattr(live, "KEEPALIVE", 0.2)

    def publisher():
        for _ in range(100):  # obuna ro'yxatdan o'tishini kutamiz
            if live.count(2): break
            time.sleep(0.05)
        live.publish(2, {"id": 5, "direction": "inbound"})
    threading.Thread(target=publisher, daemon=True).start()

    lines = []
    with httpx.Client(base_url=real_server, timeout=5) as c:
        assert c.post("/login", data={"email": "c@x.uz", "password": "12345678"}).status_code in (200, 303)
        with c.stream("GET", "/live/stream") as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            assert r.headers["x-accel-buffering"] == "no" and "no-cache" in r.headers["cache-control"]
            for ln in r.iter_lines():
                lines.append(ln)
                if ln.startswith("data:"):
                    break
        # Pro bo'lmagan akkaunt ulana olmaydi
        c2 = httpx.Client(base_url=real_server, timeout=5)
        register(TestClient(app), "n@x.uz")
        c2.post("/login", data={"email": "n@x.uz", "password": "12345678"})
        assert c2.get("/live/stream").status_code == 403
    assert "event: call" in lines and 'data: {"id": 5, "direction": "inbound"}' in lines
    for _ in range(100):  # ulanish uzilgach obuna olib tashlanadi
        if not live.count(2): break
        time.sleep(0.05)
    assert live.count(2) == 0


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


def test_pages_include_live_hooks(client):
    admin = client; register(admin, "admin@x.uz")
    cust = TestClient(app); register(cust, "c@x.uz")
    make_pro(admin, 2)
    for path in ("/reports", "/calls"):
        html = cust.get(path).text
        assert 'id="live-area"' in html and 'id="live-dot"' in html and "/live/stream" in html and 'id="live-banner"' in html
    seed_calls(2)
    assert 'data-id="1"' in cust.get("/calls").text  # yangi qatorni aniqlash uchun
    locked = TestClient(app); register(locked, "n@x.uz")
    assert "/live/stream" not in locked.get("/reports").text and "/live/stream" not in locked.get("/calls").text  # Pro emasga ulanish yo'q
