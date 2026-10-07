"""Kirishdagi brute-force himoyasi: ketma-ket xato urinishlar sonini (IP va email bo'yicha) cheklaydi.

Xotirada ishlaydi (bitta jarayon). Kalitlar soni chegaralangan, shuning uchun hujumchi xotirani to'ldira olmaydi.
"""
import threading
import time
from collections import deque

_now = time.monotonic  # testlarda almashtiriladi


class Throttle:
    def __init__(self, per_email: int = 5, per_ip: int = 20, window: int = 900, max_keys: int = 10_000):
        self.per_email, self.per_ip, self.window, self.max_keys = per_email, per_ip, window, max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> deque[float]:
        dq = self._hits.setdefault(key, deque())
        while dq and now - dq[0] > self.window:
            dq.popleft()
        return dq

    def _wait(self, key: str, limit: int, now: float) -> int:
        dq = self._live(key, now)
        return int(self.window - (now - dq[0])) + 1 if len(dq) >= limit else 0

    def blocked(self, ip: str, email: str) -> int:
        """Qancha soniyadan keyin qayta urinish mumkin (0 — bloklanmagan)."""
        now = _now()
        with self._lock:
            return max(self._wait(f"e:{email}", self.per_email, now), self._wait(f"i:{ip}", self.per_ip, now))

    def fail(self, ip: str, email: str) -> None:
        now = _now()
        with self._lock:
            if len(self._hits) > self.max_keys:  # xotirani himoya qilish: eskirgan kalitlarni tozalaymiz
                for k in [k for k in self._hits if not self._live(k, now)]:
                    del self._hits[k]
                if len(self._hits) > self.max_keys:
                    self._hits.clear()
            self._live(f"e:{email}", now).append(now)
            self._live(f"i:{ip}", now).append(now)

    def reset(self, email: str) -> None:
        with self._lock:
            self._hits.pop(f"e:{email}", None)


login_throttle = Throttle()
