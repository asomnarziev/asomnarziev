"""Testlar uchun umumiy yordamchilar (akkaunt yaratish, Pro qilish, namunaviy qo'ng'iroqlar)."""

from datetime import UTC

from app.db import SessionLocal
from app.models import Account


def register(c, email="a@x.uz"):
    """Ro'yxatdan o'tish sahifasi yo'q: foydalanuvchi bazada yaratiladi va tizimga kiriladi."""
    from app.models import User
    from app.security import hash_password

    with SessionLocal() as db:
        u = User(email=email, password_hash=hash_password("12345678"), is_admin=(email == "admin@x.uz"))
        u.account = Account()
        db.add(u)
        db.commit()
    return c.post("/login", data={"email": email, "password": "12345678"}, follow_redirects=False)


def _utc(local_dt):
    return local_dt.astimezone(UTC).replace(tzinfo=None)


def seed_calls(account_id, d=None):
    """Berilgan (standart: bugungi) Toshkent kuni uchun 4 ta qo'ng'iroq: 2 javob berilgan, 2 javobsiz."""
    from datetime import datetime, time

    from app import reports
    from app.messages import LOCAL_TZ
    from app.models import CallLog

    d = d or reports.today_local()

    def at(h, m):
        return _utc(datetime.combine(d, time(h, m), LOCAL_TZ))

    rows = [
        ("a", "inbound", "998901112233", "105", at(10, 0), 70, 60),
        ("b", "outbound", "105", "998905556677", at(10, 30), 40, 30),
        ("c", "outbound", "107", "998905556677", at(15, 0), 20, 0),
        ("d", "inbound", "998901112233", "105", at(15, 10), 0, None),
    ]
    with SessionLocal() as db:
        for u, dr, ca, ce, st, dur, talk in rows:
            db.add(
                CallLog(
                    account_id=account_id,
                    uuid=f"{d}{u}",
                    status="sent",
                    direction=dr,
                    caller=ca,
                    callee=ce,
                    started_at=st,
                    duration=dur,
                    talk=talk,
                )
            )
        db.commit()


def make_pro(admin_client, account_id):
    admin_client.post(f"/admin/account/{account_id}/subscription", data={"action": "extend", "plan": "pro", "days": "30"})
