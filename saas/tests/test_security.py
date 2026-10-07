"""Xavfsizlik: sarlavhalar, CSRF (Origin), kirishdagi brute-force himoyasi, sog'liq tekshiruvi."""
import re

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.main import app
from tests.helpers import make_pro, register


# ---------- Xavfsizlik sarlavhalari ----------
def test_security_headers_on_pages_and_errors(client):
    for resp in (client.get("/login"), client.get("/nope"), client.get("/healthz")):
        h = resp.headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "same-origin" and "camera=()" in h["permissions-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "default-src 'self'" in h["content-security-policy"]
    assert "strict-transport-security" not in client.get("/login").headers  # test BASE_URL http


def test_route_specific_csp_is_not_overridden(client, tmp_path, monkeypatch):
    """Chek fayli o'zining qat'iy CSP'si bilan beriladi (middleware ustidan yozmaydi)."""
    from app import config
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path))
    register(client, "c@x.uz")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    client.post("/billing/checkout", data={"plan": "pro", "months": "1"}, files={"receipt": ("a.png", png, "image/png")})
    r = client.get("/receipt/1")
    assert r.status_code == 200 and r.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_hsts_only_on_https(monkeypatch):
    from starlette.testclient import TestClient as TC

    from app.middleware import SecurityHeadersMiddleware

    async def plain(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    assert "strict-transport-security" not in TC(SecurityHeadersMiddleware(plain, hsts=False)).get("/").headers
    assert "max-age=31536000" in TC(SecurityHeadersMiddleware(plain, hsts=True)).get("/").headers["strict-transport-security"]


def test_api_docs_are_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# ---------- CSRF: Origin tekshiruvi ----------
def test_cross_site_post_rejected_but_same_origin_allowed(client):
    register(client, "c@x.uz")
    evil = {"Origin": "https://evil.example"}
    r = client.post("/cabinet/pbx", data={"domain": "x.onpbx.ru"}, headers=evil)
    assert r.status_code == 403 and "Cross-site" in r.text
    assert client.post("/cabinet/pbx", data={"domain": "x.onpbx.ru"}, headers={"Referer": "https://evil.example/attack.html"}).status_code == 403
    assert client.post("/cabinet/pbx", data={"domain": "x.onpbx.ru"}, headers={"Origin": "null"}).status_code == 403  # sandboxed iframe
    for ok_origin in ("http://testserver", "http://t"):  # so'rov xosti va BASE_URL xosti
        r = client.post("/cabinet/pbx", data={"domain": "good.onpbx.ru"}, headers={"Origin": ok_origin}, follow_redirects=False)
        assert r.status_code == 303
    r = client.post("/cabinet/pbx", data={"domain": "ref.onpbx.ru"}, headers={"Referer": "http://testserver/cabinet"}, follow_redirects=False)
    assert r.status_code == 303
    from app.db import SessionLocal
    from app.models import Account
    with SessionLocal() as db:
        assert db.get(Account, 1).pbx_domain == "ref.onpbx.ru"  # begona so'rovlar hech narsani o'zgartirmagan


def test_cross_site_logout_login_and_safe_methods(client):
    register(client, "c@x.uz")
    assert client.post("/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/cabinet").status_code == 200  # sessiya saqlanib qoldi
    assert client.post("/login", data={"email": "c@x.uz", "password": "12345678"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/cabinet", headers={"Origin": "https://evil.example"}).status_code == 200  # GET — xavfsiz usul


def test_webhooks_exempt_from_origin_check(client):
    register(client, "c@x.uz")
    from app.db import SessionLocal
    from app.models import Account
    with SessionLocal() as db:
        token = db.get(Account, 1).hook_token
    r = client.post(f"/hook/{token}", data={"event": "test"}, headers={"Origin": "https://pbx.onlinepbx.ru"})
    assert r.status_code == 200 and r.json()["ignored"] is True  # server -> server, brauzer emas
    assert client.post("/tg/tg-secret", json={}, headers={"Origin": "https://telegram.org"}).status_code == 200


def test_telegram_secret_compared_safely(client):
    assert client.post("/tg/wrong", json={}).status_code == 404
    assert client.post("/tg/tg-secre", json={}).status_code == 404
    assert client.post("/tg/tg-secret", json={}).status_code == 200


# ---------- Kirishdagi brute-force himoyasi ----------
def login(c, email, password):
    return c.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def test_login_wrong_password_is_401_and_does_not_reveal_user_existence(client):
    register(client, "c@x.uz")
    a = login(TestClient(app), "c@x.uz", "wrong-password")
    b = login(TestClient(app), "nobody@x.uz", "wrong-password")
    err = lambda r: re.search(r'<p class="err">(.*?)</p>', r.text).group(1)  # noqa: E731
    assert a.status_code == b.status_code == 401
    assert err(a) == err(b) and "Email yoki parol" in err(a)  # mavjud va mavjud bo'lmagan email uchun xabar bir xil


def test_login_locked_after_repeated_failures_even_with_correct_password(client, monkeypatch):
    register(client, "c@x.uz")
    clock = [1000.0]
    monkeypatch.setattr(ratelimit, "_now", lambda: clock[0])
    c = TestClient(app)
    assert [login(c, "c@x.uz", "bad").status_code for _ in range(5)] == [401] * 5
    r = login(c, "c@x.uz", "12345678")  # to'g'ri parol ham o'tmaydi
    assert r.status_code == 429 and "daqiqadan keyin" in r.text
    assert login(c, "other@x.uz", "bad").status_code == 401  # boshqa email bloklanmagan
    clock[0] += 901  # oyna o'tdi
    assert login(c, "c@x.uz", "12345678").status_code == 303


def test_successful_login_resets_email_counter(client, monkeypatch):
    register(client, "c@x.uz")
    c = TestClient(app)
    for _ in range(4):
        login(c, "c@x.uz", "bad")
    assert login(c, "c@x.uz", "12345678").status_code == 303
    assert [login(TestClient(app), "c@x.uz", "bad").status_code for _ in range(4)] == [401] * 4  # hisob noldan boshlandi


def test_login_ip_limit_blocks_password_spraying(client, monkeypatch):
    register(client, "c@x.uz")
    c = TestClient(app)
    for i in range(20):  # har safar boshqa email: IP bo'yicha to'planadi
        assert login(c, f"user{i}@x.uz", "bad").status_code == 401
    assert login(c, "c@x.uz", "12345678").status_code == 429


def test_throttle_memory_is_bounded():
    t = ratelimit.Throttle(max_keys=50)
    for i in range(500):
        t.fail("1.1.1.1", f"e{i}@x.uz")
    assert len(t._hits) <= 60  # hujumchi xotirani to'ldira olmaydi
    assert t.blocked("1.1.1.1", "e1@x.uz") >= 0


def test_session_cookie_flags(client):
    register(client, "c@x.uz")
    cookie = login(TestClient(app), "c@x.uz", "12345678").headers["set-cookie"].lower()
    assert cookie.startswith("pbx_session=") and "httponly" in cookie and "samesite=lax" in cookie and "max-age=1209600" in cookie
    assert "secure" not in cookie  # testlarda BASE_URL http; productionda (https) Secure qo'shiladi


# ---------- Sog'liq tekshiruvi ----------
def test_healthz_ok_and_no_secrets(client):
    r = client.get("/healthz")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok" and body["db"] is True and body["scheduler"] == "disabled"
    assert set(body) == {"status", "db", "scheduler", "version"}  # maxfiy ma'lumot yo'q


def test_healthz_503_when_database_down(client, monkeypatch):
    import app.main as main

    class Broken:
        def __enter__(self): raise RuntimeError("db down")
        def __exit__(self, *a): return False
    monkeypatch.setattr(main, "SessionLocal", Broken)
    r = client.get("/healthz")
    assert r.status_code == 503 and r.json()["status"] == "degraded" and r.json()["db"] is False


def test_healthz_reports_dead_scheduler(client):
    class DeadThread:
        def is_alive(self): return False
    client.app.state.scheduler = (DeadThread(), None)
    try:
        r = client.get("/healthz")
        assert r.status_code == 503 and r.json()["scheduler"] == "dead"
    finally:
        client.app.state.scheduler = None


@pytest.mark.parametrize("path", ["/cabinet", "/reports", "/calls", "/billing", "/admin", "/live/stream", "/reports.csv"])
def test_private_pages_require_login(client, path):
    r = client.get(path, follow_redirects=False)
    assert (r.status_code, r.headers.get("location")) == (303, "/login") or r.status_code == 403
