"""Xavfsizlik middleware'lari. Sof ASGI: BaseHTTPMiddleware'dan farqli o'laroq SSE oqimini buzmaydi."""
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

# Sahifalarda inline <style>/<script> ishlatiladi (tashqi kutubxona yo'q), shuning uchun 'unsafe-inline' kerak;
# qolgani qat'iy: tashqi resurslar, iframe ichiga joylash va tashqi formaga yuborish taqiqlangan.
CSP = ("default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")


class SecurityHeadersMiddleware:
    """Har javobga xavfsizlik sarlavhalarini qo'shadi (marshrut o'zi qo'ygan bo'lsa, tegmaydi)."""

    def __init__(self, app, hsts: bool = False):
        self.app, self.hsts = app, hsts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("X-Frame-Options", "DENY")
                h.setdefault("Referrer-Policy", "same-origin")
                h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
                h.setdefault("Content-Security-Policy", CSP)
                if self.hsts:
                    h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class OriginCheckMiddleware:
    """CSRF'dan himoya: holatni o'zgartiruvchi so'rovlar (POST va h.k.) boshqa saytdan kelsa rad etiladi.

    Brauzer POST'da `Origin` (yoki `Referer`) yuboradi; uning xosti bizniki bo'lmasa — cross-site so'rov.
    SameSite=Lax cookie bilan birga ishlaydi (ikki qatlam). Webhook'lar (server -> server) bundan mustasno.
    """

    SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
    EXEMPT_PREFIXES = ("/hook/", "/tg/")

    def __init__(self, app, extra_hosts: tuple[str, ...] = ()):
        self.app = app
        self.extra_hosts = {h.lower() for h in extra_hosts if h}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in self.SAFE and not scope["path"].startswith(self.EXEMPT_PREFIXES):
            headers = Headers(scope=scope)
            origin = headers.get("origin") or headers.get("referer")
            if origin:
                allowed = {headers.get("host", "").lower()} | self.extra_hosts
                if urlsplit(origin).netloc.lower() not in allowed:
                    response = JSONResponse({"detail": "Cross-site so'rov rad etildi"}, status_code=403)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
