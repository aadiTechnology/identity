"""Shared web helpers: templates, CSRF (double-submit cookie), session cookie."""
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SESSION_COOKIE = "id_session"
CSRF_COOKIE = "id_csrf"


def cookie_args() -> dict:
    return {"httponly": True, "secure": get_settings().cookie_secure, "samesite": "lax", "path": "/"}


def csrf_value(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)


def render(request: Request, name: str, ctx: dict | None = None, status: int = 200) -> HTMLResponse:
    token = csrf_value(request)
    resp = templates.TemplateResponse(request, name, {**(ctx or {}), "csrf": token}, status_code=status)
    if request.cookies.get(CSRF_COOKIE) != token:
        resp.set_cookie(CSRF_COOKIE, token, **{**cookie_args(), "httponly": False})
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def csrf_ok(request: Request, form_value: str | None) -> bool:
    c = request.cookies.get(CSRF_COOKIE)
    return bool(c) and bool(form_value) and secrets.compare_digest(c, form_value)


def safe_next(n: str | None) -> str:
    """Only same-site relative paths, to prevent open redirects after sign-in."""
    if n and n.startswith("/") and not n.startswith("//") and "\\" not in n:
        return n
    return "/"
