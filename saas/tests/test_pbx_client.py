"""Pbx client testlari."""


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
