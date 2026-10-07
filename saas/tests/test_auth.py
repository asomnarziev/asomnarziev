"""Auth testlari."""

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from tests.helpers import register


def test_admin_creates_customer_and_no_registration(client):
    register(client, "admin@x.uz")
    anon = TestClient(app)
    assert anon.get("/register").status_code == 404
    assert anon.post("/register", data={"email": "z@x.uz", "password": "12345678"}).status_code in (404, 405)
    assert "Ro'yxatdan" not in anon.get("/login").text
    r = client.post("/admin/user", data={"email": "cust@x.uz", "password": "parol12345"})
    assert "parol12345" in r.text
    cust = TestClient(app)
    assert (
        cust.post("/login", data={"email": "cust@x.uz", "password": "parol12345"}, follow_redirects=False).headers["location"]
        == "/cabinet"
    )
    assert cust.get("/admin").status_code == 403
    assert "band" in client.post("/admin/user", data={"email": "cust@x.uz", "password": "parol12345"}).text
    aid = 2
    client.post(f"/admin/account/{aid}/password", data={"password": "yangiparol1"})
    assert (
        TestClient(app)
        .post("/login", data={"email": "cust@x.uz", "password": "yangiparol1"}, follow_redirects=False)
        .headers["location"]
        == "/cabinet"
    )


def test_create_user_cli(client, capsys):
    from app import create_user
    from app.models import User

    create_user.main(["boss@x.uz", "12345678", "--admin"])
    create_user.main(["boss@x.uz", "yangiparol1"])  # mavjud: parolni yangilaydi, adminligini saqlaydi
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="boss@x.uz").one()
        assert u.is_admin and u.account is not None
    c = TestClient(app)
    assert (
        c.post("/login", data={"email": "boss@x.uz", "password": "yangiparol1"}, follow_redirects=False).headers["location"]
        == "/cabinet"
    )


def test_login_shows_admin_contacts(client):
    t = TestClient(app).get("/login").text
    assert "https://t.me/asom_narziev" in t and "tel:+998948302407" in t and "mailto:asomnarziev07@gmail.com" in t
    assert "+998 94 830 24 07" in t
