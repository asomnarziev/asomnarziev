"""Reports testlari."""

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from tests.helpers import make_pro, register, seed_calls


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
