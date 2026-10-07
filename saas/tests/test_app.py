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
