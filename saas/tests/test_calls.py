"""Calls testlari."""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from app import telegram
from app.db import SessionLocal
from app.main import app
from tests.helpers import make_pro, register, seed_calls


@pytest.fixture()
def calls_env(client, monkeypatch):
    from app.web import calls as calls_web

    calls_web._fetches.clear()
    admin = client
    register(admin, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
    cust.post("/cabinet/pbx", data={"domain": "d.onpbx.ru", "key": "k"})
    seed_calls(2)  # a(id1) kiruvchi javob, b(id2) chiquvchi javob, c(id3) chiquvchi javobsiz, d(id4) kiruvchi javobsiz
    make_pro(admin, 2)
    return admin, cust


def n_rows(resp):
    return resp.text.count("<code>") // 2


def test_calls_search_locked_for_non_pro(client):
    register(client, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
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
    assert (
        "Hech narsa topilmadi" in cust.get("/calls?frm=2020-01-01&to=2020-01-02").text
        or n_rows(cust.get("/calls?frm=2020-01-01&to=2020-01-02")) == 0
    )


def test_calls_pagination_and_isolation(calls_env):
    from datetime import datetime, timedelta

    from app.models import CallLog

    admin, cust = calls_env
    with SessionLocal() as db:
        t0 = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        for k in range(120):
            db.add(
                CallLog(
                    account_id=2,
                    uuid=f"p{k}",
                    status="sent",
                    direction="inbound",
                    caller=f"9989000{k:05d}",
                    callee="105",
                    started_at=t0 - timedelta(seconds=k),
                    duration=5,
                    talk=5,
                )
            )
        db.commit()
    assert n_rows(cust.get("/calls")) == 50 and "1 / 3" in cust.get("/calls").text
    assert (
        n_rows(cust.get("/calls?page=3")) == 24 and n_rows(cust.get("/calls?page=99")) == 24
    )  # 124 ta; oxirgi sahifaga qisiladi
    other = TestClient(app)
    register(other, "z@x.uz")
    make_pro(admin, 3)
    assert n_rows(other.get("/calls")) == 0 and "998901112233" not in other.get("/calls").text
    assert other.get("/calls/1/record").status_code == 404 and other.post("/calls/1/send").status_code == 404


def test_call_record_proxy_and_hourly_cap(calls_env, monkeypatch):
    from app.pbx import PbxError
    from app.web import calls as web

    _, cust = calls_env
    mode = {"v": b"mp3data"}

    class FakePbx:
        def __init__(self, *a):
            pass

        def record(self, u):
            if isinstance(mode["v"], Exception):
                raise mode["v"]
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
    from app.web import calls as web

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
    nochat = TestClient(app)
    register(nochat, "n@x.uz")
    assert nochat.post("/calls/1/send").status_code == 403  # Pro emas
