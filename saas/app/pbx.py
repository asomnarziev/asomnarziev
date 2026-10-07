"""OnlinePBX API mijozi (har mijozning o'z domeni va kaliti bilan)."""
import os

import requests

# API xosti bir nechta bo'lishi mumkin; auth muvaffaqiyatli bo'lgani eslab qolinadi
HOSTS = [h.strip() for h in os.environ.get("PBX_API_HOSTS", "api.onlinepbx.ru,api2.onlinepbx.ru").split(",") if h.strip()]


class PbxError(Exception):
    pass


class PbxClient:
    def __init__(self, domain: str, auth_key: str):
        domain = (domain or "").strip().removeprefix("https://").removeprefix("http://").strip("/")
        if not domain:
            raise PbxError("OnlinePBX domeni kabinetda kiritilmagan")
        if not auth_key:
            raise PbxError("OnlinePBX API kaliti kabinetda kiritilmagan")
        self.domain, self.auth_key = domain, auth_key
        self.s = requests.Session()
        self.base = None
        self.key = None

    def _auth(self):
        errors = []
        for host in HOSTS:
            base = f"https://{host}/{self.domain}"
            try:
                r = self.s.post(f"{base}/auth.json", data={"auth_key": self.auth_key, "new": "true"}, timeout=30)
                r.raise_for_status()
                body = r.json()
                if str(body.get("status")) not in ("1", "True", "true") and "data" not in body:
                    raise PbxError(f"auth rad etildi: {str(body)[:200]}")
                d = body["data"]
                self.base, self.key = base, f"{d['key_id']}:{d['key']}"
                return
            except (requests.RequestException, ValueError, KeyError, PbxError) as e:
                errors.append(f"{host}: {e}")
        raise PbxError("OnlinePBX'ga ulanib bo'lmadi (domen yoki API kalitni tekshiring): " + " | ".join(errors))

    def _post(self, path: str, data: dict) -> dict:
        if not self.key:
            self._auth()
        r = self.s.post(f"{self.base}/{path}", data=data, headers={"x-pbx-authentication": self.key}, timeout=60)
        r.raise_for_status()
        return r.json()

    def call_info(self, uuid: str) -> dict:
        data = self._post("mongo_history/search.json", {"uuid": uuid}).get("data") or []
        return data[0] if data and isinstance(data[0], dict) else {}

    def record(self, uuid: str) -> bytes | None:
        data = self._post("mongo_history/search.json", {"uuid": uuid, "download": 1}).get("data")
        url = data[0] if isinstance(data, list) and data else data
        if not isinstance(url, str) or not url.startswith("http"):
            return None
        r = self.s.get(url, timeout=120)
        r.raise_for_status()
        return r.content
