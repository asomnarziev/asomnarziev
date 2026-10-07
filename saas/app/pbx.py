"""OnlinePBX API mijozi (har mijozning o'z domeni va kaliti bilan)."""
import requests


class PbxClient:
    def __init__(self, domain: str, auth_key: str):
        self.base = f"https://api.onlinepbx.ru/{domain}"
        self.auth_key = auth_key
        self.s = requests.Session()
        self.key = None

    def _auth(self):
        r = self.s.post(f"{self.base}/auth.json", data={"auth_key": self.auth_key, "new": "true"}, timeout=30)
        r.raise_for_status()
        d = r.json()["data"]
        self.key = f"{d['key_id']}:{d['key']}"

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
