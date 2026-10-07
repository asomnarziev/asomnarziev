"""OnlinePBX API mijozi (har mijozning o'z domeni va kaliti bilan) — limitlarga tegmaslik himoyasi bilan.

Himoya: (1) auth kaliti keshlanadi — har qo'ng'iroqda qayta auth qilinmaydi;
(2) har mijoz va umumiy so'rov tezligi cheklangan (PBX_RPS_*);
(3) 429/5xx da Retry-After yoki eksponensial kutish (+jitter), cheklangan urinishlar;
(4) noto'g'ri domen/kalit bo'lsa auth qayta-qayta urilmaydi (cooldown).
Rasmiy limitlar noma'lum, shuning uchun hammasi muhit o'zgaruvchilari bilan sozlanadi.
"""
import hashlib
import os
import random
import threading
import time

import requests

HOSTS = [h.strip() for h in os.environ.get("PBX_API_HOSTS", "api.onlinepbx.ru,api2.onlinepbx.ru").split(",") if h.strip()]
RPS_TENANT = float(os.environ.get("PBX_RPS_PER_TENANT", "2"))   # bitta mijoz uchun so'rov/soniya
RPS_GLOBAL = float(os.environ.get("PBX_RPS_GLOBAL", "10"))      # barcha mijozlar uchun umumiy
KEY_TTL = int(os.environ.get("PBX_KEY_TTL", "21600"))           # auth kaliti keshda necha soniya turadi
AUTH_COOLDOWN = int(os.environ.get("PBX_AUTH_COOLDOWN", "300")) # noto'g'ri kalitdan keyin auth'ga tanaffus
MAX_RETRIES = int(os.environ.get("PBX_MAX_RETRIES", "3"))

_now = time.monotonic  # testlarda almashtiriladi
_sleep = time.sleep


class PbxError(Exception):
    pass


class Gate:
    """Soniyasiga ko'pi bilan `rps` ta so'rovga ruxsat beradi (so'rovlar orasida minimal interval)."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            t = _now()
            start = max(t, self.next)
            self.next = start + self.interval
        if start - t > 0:
            _sleep(start - t)

    def penalize(self, seconds: float):
        """429 kelganda: shu darvoza `seconds` soniya yopiladi."""
        with self.lock:
            self.next = max(self.next, _now() + seconds)


_state = threading.Lock()
_gates: dict[str, Gate] = {}
_keys: dict[tuple, tuple] = {}      # ident -> (base, key, expires)
_fail: dict[tuple, float] = {}      # ident -> cooldown tugaydigan vaqt
_auth_locks: dict[tuple, threading.Lock] = {}
GLOBAL_GATE = Gate(RPS_GLOBAL)


def _gate(domain: str) -> Gate:
    with _state:
        return _gates.setdefault(domain, Gate(RPS_TENANT))


def reset_state():
    """Testlar uchun."""
    with _state:
        _gates.clear(); _keys.clear(); _fail.clear(); _auth_locks.clear()


def _retry_after(r) -> float | None:
    try:
        return max(0.0, float(r.headers.get("Retry-After", "")))
    except (TypeError, ValueError):
        return None


class PbxClient:
    def __init__(self, domain: str, auth_key: str):
        domain = (domain or "").strip().removeprefix("https://").removeprefix("http://").strip("/")
        if not domain:
            raise PbxError("OnlinePBX domeni kabinetda kiritilmagan")
        if not auth_key:
            raise PbxError("OnlinePBX API kaliti kabinetda kiritilmagan")
        self.domain, self.auth_key = domain, auth_key
        self.ident = (domain, hashlib.sha256(auth_key.encode()).hexdigest()[:16])  # kalit o'zgarsa kesh/cooldown yangilanadi
        self.gate = _gate(domain)
        self.s = requests.Session()
        self.base = None
        self.key = None

    # --- yordamchilar ---
    def _throttle(self):
        self.gate.wait()
        GLOBAL_GATE.wait()

    def _backoff(self, attempt: int, retry_after: float | None):
        delay = retry_after if retry_after is not None else min(60, 2 ** (attempt + 1)) + random.random()
        self.gate.penalize(delay)
        _sleep(delay)

    # --- auth (keshlangan) ---
    def _load_cached(self) -> bool:
        with _state:
            ent = _keys.get(self.ident)
        if ent and ent[2] > _now():
            self.base, self.key = ent[0], ent[1]
            return True
        return False

    def _auth(self):
        if self._load_cached():
            return
        with _state:
            lock = _auth_locks.setdefault(self.ident, threading.Lock())
        with lock:  # bir vaqtda bitta auth; qolganlar natijani keshdan oladi
            if self._load_cached():
                return
            with _state:
                until = _fail.get(self.ident, 0)
            if until > _now():
                raise PbxError(f"OnlinePBX auth vaqtincha to'xtatilgan (domen/kalit noto'g'ri bo'lishi mumkin), {int(until - _now())} soniyadan keyin qayta uriniladi")
            errors, cred = [], []  # cred: har xost uchun "xato domen/kalit tufayli" belgisi
            for host in HOSTS:
                base = f"https://{host}/{self.domain}"
                try:
                    self._throttle()
                    r = self.s.post(f"{base}/auth.json", data={"auth_key": self.auth_key, "new": "true"}, timeout=30)
                    if r.status_code == 429:
                        self.gate.penalize(_retry_after(r) or 30)
                        errors.append(f"{host}: 429 so'rovlar limiti"); cred.append(False)
                        continue
                    r.raise_for_status()
                    body = r.json()
                    if str(body.get("status")) not in ("1", "True", "true") and "data" not in body:
                        errors.append(f"{host}: auth rad etildi: {str(body)[:200]}"); cred.append(True)
                        continue
                    d = body["data"]
                    self.base, self.key = base, f"{d['key_id']}:{d['key']}"
                    with _state:
                        _keys[self.ident] = (self.base, self.key, _now() + KEY_TTL)
                        _fail.pop(self.ident, None)
                    return
                except requests.HTTPError as e:
                    code = e.response.status_code if e.response is not None else 0
                    errors.append(f"{host}: {e}"); cred.append(code in (400, 401, 403, 404))
                except (requests.RequestException, ValueError, KeyError) as e:
                    errors.append(f"{host}: {e}"); cred.append(False)
            if cred and all(cred):  # hamma xost 4xx/rad etdi — domen yoki kalit noto'g'ri: tanaffus (tarmoq xatosida emas)
                with _state:
                    _fail[self.ident] = _now() + AUTH_COOLDOWN
            raise PbxError("OnlinePBX'ga ulanib bo'lmadi (domen yoki API kalitni tekshiring): " + " | ".join(errors))

    def _drop_key(self):
        with _state:
            _keys.pop(self.ident, None)
        self.key = None

    # --- so'rovlar ---
    def _post(self, path: str, data: dict) -> dict:
        reauthed = False
        for attempt in range(MAX_RETRIES + 1):
            if not self.key:
                self._auth()
            self._throttle()
            try:
                r = self.s.post(f"{self.base}/{path}", data=data, headers={"x-pbx-authentication": self.key}, timeout=60)
            except requests.RequestException as e:
                if attempt >= MAX_RETRIES:
                    raise PbxError(f"OnlinePBX tarmoq xatosi: {e}")
                self._backoff(attempt, None)
                continue
            if r.status_code in (401, 403) and not reauthed:
                reauthed = True  # kalit eskirgan bo'lishi mumkin: bir marta qayta auth
                self._drop_key()
                continue
            if r.status_code in (401, 403):
                raise PbxError(f"OnlinePBX kalitni rad etdi ({r.status_code})")
            if r.status_code == 429 or r.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    raise PbxError(f"OnlinePBX {'limiti (429)' if r.status_code == 429 else 'xatosi'} {r.status_code}: keyinroq qayta uriniladi")
                self._backoff(attempt, _retry_after(r))
                continue
            r.raise_for_status()
            return r.json()
        raise PbxError("OnlinePBX so'rovi bajarilmadi")

    def call_info(self, uuid: str) -> dict:
        data = self._post("mongo_history/search.json", {"uuid": uuid}).get("data") or []
        return data[0] if data and isinstance(data[0], dict) else {}

    def record(self, uuid: str) -> bytes | None:
        data = self._post("mongo_history/search.json", {"uuid": uuid, "download": 1}).get("data")
        url = data[0] if isinstance(data, list) and data else data
        if not isinstance(url, str) or not url.startswith("http"):
            return None
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            r = self.s.get(url, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    raise PbxError(f"Yozuvni yuklashda xato {r.status_code}")
                self._backoff(attempt, _retry_after(r))
                continue
            r.raise_for_status()
            return r.content
        return None
