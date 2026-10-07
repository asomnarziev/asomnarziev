"""Live testlari."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import make_pro, register, seed_calls


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
        live.unsubscribe(a)
        live.unsubscribe(b)
        live.unsubscribe(other)
        assert live.count(7) == 0 and live.publish(7, {"id": 2}) == 0
        subs = [live.subscribe(9, loop) for _ in range(live.MAX_PER_ACCOUNT)]
        assert live.subscribe(9, loop) is None  # limitdan oshdi
        for s_ in subs:
            live.unsubscribe(s_)
        s1 = live.subscribe(10, loop)
        for k in range(80):
            live.publish(10, {"id": k})  # navbat to'lsa ham xato bermaydi
        await asyncio.sleep(0.05)
        assert s1.queue.qsize() == 50
        live.unsubscribe(s1)

    asyncio.run(scenario())


def test_live_stream_access_control(client):
    from app import live

    anon = TestClient(app)
    assert anon.get("/live/stream").status_code == 403
    register(client, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
    assert cust.get("/live/stream").status_code == 403  # Pro emas
    assert live.count(2) == 0


@pytest.fixture()
def real_server():
    """Haqiqiy uvicorn serveri (TestClient oqimni bosqichma-bosqich bermaydi)."""
    import socket
    import threading
    import time

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)


def test_live_stream_delivers_event_to_pro_page(client, real_server, monkeypatch):
    import threading
    import time

    import httpx

    from app import live

    admin = client
    register(admin, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
    make_pro(admin, 2)
    monkeypatch.setattr(live, "KEEPALIVE", 0.2)

    def publisher():
        for _ in range(100):  # obuna ro'yxatdan o'tishini kutamiz
            if live.count(2):
                break
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
        if not live.count(2):
            break
        time.sleep(0.05)
    assert live.count(2) == 0


def test_pages_include_live_hooks(client):
    admin = client
    register(admin, "admin@x.uz")
    cust = TestClient(app)
    register(cust, "c@x.uz")
    make_pro(admin, 2)
    for path in ("/reports", "/calls"):
        html = cust.get(path).text
        assert 'id="live-area"' in html and 'id="live-dot"' in html and "/live/stream" in html and 'id="live-banner"' in html
    seed_calls(2)
    assert 'data-id="1"' in cust.get("/calls").text  # yangi qatorni aniqlash uchun
    locked = TestClient(app)
    register(locked, "n@x.uz")
    assert (
        "/live/stream" not in locked.get("/reports").text and "/live/stream" not in locked.get("/calls").text
    )  # Pro emasga ulanish yo'q
