"""Admin testlari."""

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Account, Payment, now
from tests.helpers import register


def test_admin_manages_subscription(client):
    register(client, "admin@x.uz")
    client.post("/admin/user", data={"email": "c@x.uz", "password": "parol12345"})
    with SessionLocal() as db:
        aid = db.query(Account).filter(Account.id != 1).one().id
    r = client.post(
        f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "pro", "days": "30"}, follow_redirects=False
    )
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
    assert (
        client.post(f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "bad", "days": "5"}).status_code
        == 400
    )
    assert (
        client.post(f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "pro", "days": "0"}).status_code
        == 400
    )
    assert "Boshqarish" in client.get("/admin").text
    cust = TestClient(app)
    cust.post("/login", data={"email": "c@x.uz", "password": "parol12345"})
    assert (
        cust.post(f"/admin/account/{aid}/subscription", data={"action": "extend", "plan": "pro", "days": "30"}).status_code == 403
    )
