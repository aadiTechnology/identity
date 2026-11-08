from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import SessionLocal, engine
from . import events
from .routes import admin, company, oidc, sync, ui
from .security import ensure_signing_key


def migrate():
    """Apply Alembic migrations (used at container start)."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    bad = get_settings().problems()
    if bad:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(bad))
    with SessionLocal() as db:
        ensure_signing_key(db)
    if get_settings().webhooks_enabled:
        events.start_worker(SessionLocal)
    yield
    events.stop_worker()


app = FastAPI(title="Aadi Identity", docs_url=None if get_settings().is_prod else "/docs", redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self' https: http:")
    if get_settings().cookie_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(ui.router)
app.include_router(oidc.router)
app.include_router(admin.router)
app.include_router(company.router)
app.include_router(sync.router)
if not get_settings().is_prod:
    from .routes import demo

    app.include_router(demo.router)
