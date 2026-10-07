"""Import history testlari."""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from app import service, telegram
from app.db import SessionLocal
from app.main import app
from app.models import Account
from tests.helpers import make_pro, register


def fake_history_pbx(calls, log=None):
    """OnlinePBX tarixi: har qo'ng'iroqning start_stamp'i [start, end) oraliqqa tushsa qaytariladi."""

    class P:
        def __init__(self, *a):
            pass

        def history(self, start, end):
            if log is not None:
                log.append((start, end))
            return [c for c in calls if start <= c["start_stamp"] < end]

    return P


def mk_call(i, hours_ago=5, talk=30, direction="inbound"):
    import time

    return {
        "uuid": f"h{i}",
        "caller_id_number": "998901112233" if direction == "inbound" else "105",
        "destination_number": "105" if direction == "inbound" else "998905556677",
        "accountcode": direction,
        "duration": talk + 6,
        "user_talk_time": talk,
        "start_stamp": int(time.time()) - hours_ago * 3600 - i,
    }


@pytest.fixture()
def import_env(client, monkeypatch):
    from app import service as svc

    svc._importing.clear()
    admin = client
    register(admin, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
    cust.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "k"})
    make_pro(admin, 2)
    return admin, cust, monkeypatch


def test_import_history_stores_old_calls_without_duplicates(import_env):
    from app.models import CallLog

    _, cust, mp = import_env
    calls = [
        mk_call(1, 5),
        mk_call(2, 24 * 3, talk=0),
        mk_call(3, 24 * 10, direction="outbound"),
        mk_call(4, 24 * 40),
    ]  # 40 kun oldingisi chegaradan tashqari
    mp.setattr(service, "PbxClient", fake_history_pbx(calls))
    with SessionLocal() as db:  # bittasi webhook orqali allaqachon saqlangan
        db.add(
            CallLog(
                account_id=2,
                uuid="h1",
                status="sent",
                direction="inbound",
                caller="x",
                callee="y",
                started_at=datetime_now_utc(),
                duration=1,
                talk=1,
            )
        )
        db.commit()
    service.import_history(2)
    with SessionLocal() as db:
        rows = {r.uuid: r for r in db.query(CallLog).filter_by(account_id=2)}
        assert set(rows) == {"h1", "h2", "h3"}  # h4 (40 kun) olinmadi; h1 takrorlanmadi
        assert rows["h1"].status == "sent" and rows["h1"].caller == "x"  # mavjud qator o'zgarmadi
        assert rows["h2"].status == "imported" and rows["h2"].talk == 0 and rows["h2"].direction == "inbound"
        assert rows["h3"].direction == "outbound" and rows["h3"].duration == 36 and rows["h3"].created_at == rows["h3"].started_at
        acc = db.get(Account, 2)
        assert acc.import_at is not None and "2 ta" in acc.import_note
    service.import_history(2)  # takror ishga tushirish xavfsiz
    with SessionLocal() as db:
        assert db.query(CallLog).filter_by(account_id=2).count() == 3
        assert "0 ta" in db.get(Account, 2).import_note


def datetime_now_utc():
    from datetime import datetime

    return datetime.now(UTC).replace(tzinfo=None)


def test_import_splits_big_windows(import_env):
    _, _, mp = import_env
    import time

    t0 = int(time.time()) - 3 * 3600
    calls = [{**mk_call(i), "start_stamp": t0 + i * 5} for i in range(700)]  # 700 ta: bir oynada API qirqadigan hajm
    seen = []

    class P:
        def __init__(self, *a):
            pass

        def history(self, start, end):
            seen.append((start, end))
            rows = [c for c in calls if start <= c["start_stamp"] < end]
            return (
                rows[: service.IMPORT_SPLIT_AT] if len(rows) >= service.IMPORT_SPLIT_AT else rows
            )  # katta oynada qirqilgan javob

    mp.setattr(service, "PbxClient", P)
    service.import_history(2)
    from app.models import CallLog

    with SessionLocal() as db:
        assert db.query(CallLog).filter_by(account_id=2, status="imported").count() == 700  # bo'lingani uchun hammasi olindi
    assert len(seen) > 30  # 30 kunlik oynalar + bo'linganlar


def test_import_does_not_break_webhook_log_or_daily_cap(import_env):
    from app.models import CallLog

    _, cust, mp = import_env
    cust.post("/cabinet/chat", data={"chat_id": "1", "lang": "uz"})
    calls = [mk_call(i, hours_ago=2 + i // 50) for i in range(40)]
    mp.setattr(service, "PbxClient", fake_history_pbx(calls))
    service.import_history(2)
    page = cust.get("/cabinet").text
    assert "imported" not in page and "hali kelmagan" in page  # "oxirgi webhook" yuklangan qatorlardan xabarsiz qoldi
    from app import config

    mp.setattr(config, "MAX_CALLS_PER_DAY", 5)  # 40 ta yuklangan, lekin kunlik limitga kirmaydi
    mp.setattr(
        service,
        "PbxClient",
        type(
            "F",
            (),
            {
                "__init__": lambda s, *a: None,
                "call_info": lambda s, u: {
                    "uuid": u,
                    "caller_id_number": "9",
                    "destination_number": "1",
                    "accountcode": "inbound",
                    "duration": 3,
                    "user_talk_time": 3,
                    "start_stamp": 1791358224,
                },
                "record": lambda s, u: b"x",
            },
        ),
    )
    mp.setattr(telegram, "send_audio", lambda *a: None)
    with SessionLocal() as db:
        service.process_call(db, 2, "new1")
        assert db.query(CallLog).filter_by(uuid="new1").one().status == "sent"  # "skipped" emas


def test_import_errors_are_reported_and_not_marked_done(import_env):
    _, _, mp = import_env
    from app.pbx import PbxError

    class Bad:
        def __init__(self, *a):
            pass

        def history(self, s, e):
            raise PbxError("OnlinePBX'ga ulanib bo'lmadi")

    mp.setattr(service, "PbxClient", Bad)
    service.import_history(2)
    with SessionLocal() as db:
        a = db.get(Account, 2)
        assert a.import_at is None and "ulanib bo'lmadi" in a.import_note
    mp.setattr(service, "IMPORT_MAX_ROWS", 3)
    mp.setattr(service, "PbxClient", fake_history_pbx([mk_call(i) for i in range(10)]))
    service.import_history(2)
    with SessionLocal() as db:
        a = db.get(Account, 2)
        assert a.import_at is None and "juda ko'p" in a.import_note
    assert not service.is_importing(2)  # xatodan keyin ham qulf ochiladi
    service._importing.add(2)
    service.import_history(2)  # allaqachon ketayotgan bo'lsa hech narsa qilmaydi
    service._importing.discard(2)


def test_import_route_rules_and_live_event(import_env):
    import asyncio

    from app import live

    admin, cust, mp = import_env
    mp.setattr(service, "PbxClient", fake_history_pbx([mk_call(1), mk_call(2, talk=0)]))
    free = TestClient(app)
    register(free, "f@x.uz")
    assert free.post("/reports/import").status_code == 403  # Pro emas
    nopbx = TestClient(app)
    register(nopbx, "n@x.uz")
    make_pro(admin, 4)
    assert "OnlinePBX domeni" in nopbx.post("/reports/import", follow_redirects=True).text

    async def scenario():
        sub = live.subscribe(2, asyncio.get_running_loop())
        r = await asyncio.get_running_loop().run_in_executor(None, lambda: cust.post("/reports/import", follow_redirects=False))
        ev = await asyncio.wait_for(sub.queue.get(), 3)  # tugagach ochiq sahifalar yangilanadi
        live.unsubscribe(sub)
        return r, ev

    r, ev = asyncio.run(scenario())
    assert r.status_code == 303 and r.headers["location"] == "/reports?ok=import" and ev == {"id": 0, "import": True}
    page = cust.get("/reports?period=30d").text
    assert (
        "Tarix yuklangan" in page
        and "2 ta qo&#39;ng&#39;iroq qo&#39;shildi" in page.replace("'", "&#39;")
        and "Yozuv fayllari serverda saqlanmaydi" not in page
    )
    assert "Jami qo'ng'iroq" in page and ">2<" in page.replace(" ", "")  # statistika eski qo'ng'iroqlarni hisobladi
    assert "Yaqinda yuklangan" in cust.post("/reports/import", follow_redirects=True).text  # tanaffus (10 daqiqa)
    assert "998901112233" in cust.get("/calls").text  # qidiruvda ham ko'rinadi


def test_import_card_shown_before_import(import_env):
    _, cust, _ = import_env
    page = cust.get("/reports").text
    assert "Yozuv fayllari serverda saqlanmaydi" in page and "Oxirgi 30 kunni yuklash" in page
