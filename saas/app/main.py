"""ASGI ilova: middleware, marshrutlar, ishga tushish/to'xtash va sog'liq tekshiruvi."""
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, config, hooks, scheduler, web
from .db import SessionLocal, init_db
from .middleware import OriginCheckMiddleware, SecurityHeadersMiddleware

# Ilova loglari (INFO) journal'ga tushishi uchun: uvicorn faqat o'z loglarini sozlaydi
logging.basicConfig(level=config.LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.scheduler = scheduler.start() if config.SCHEDULER else None
    log.info("started v%s (scheduler=%s)", __version__, bool(app.state.scheduler))
    yield
    if app.state.scheduler:
        app.state.scheduler[1].set()


# /docs, /redoc va /openapi.json productionda kerak emas: ortiqcha ma'lumot oshkor qilmaymiz
app = FastAPI(title="PBX → Telegram", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# Tartib: oxirgi qo'shilgani eng tashqi (xavfsizlik sarlavhalari 403 javoblarga ham qo'shilsin)
https = config.BASE_URL.startswith("https")
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="pbx_session", same_site="lax",
                   https_only=https, max_age=14 * 24 * 3600)
app.add_middleware(OriginCheckMiddleware, extra_hosts=(urlsplit(config.BASE_URL).netloc,))
app.add_middleware(SecurityHeadersMiddleware, hsts=https)

app.include_router(hooks.router)
app.include_router(web.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Monitoring uchun: baza ishlayaptimi, fon rejalashtiruvchi tirikmi. Maxfiy ma'lumot qaytarmaydi."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        log.exception("healthz: database check failed")
        db_ok = False
    sched = app.state.scheduler
    sched_state = "disabled" if sched is None else ("ok" if sched[0].is_alive() else "dead")
    healthy = db_ok and sched_state != "dead"
    return JSONResponse({"status": "ok" if healthy else "degraded", "db": db_ok, "scheduler": sched_state,
                         "version": __version__}, status_code=200 if healthy else 503)
