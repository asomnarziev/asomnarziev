from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import config, hooks, web
from .db import init_db


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="PBX → Telegram", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, same_site="lax",
                   https_only=config.BASE_URL.startswith("https"))
app.include_router(hooks.router)
app.include_router(web.router)
