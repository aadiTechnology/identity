"""Demo products for showing the sign-in flow end to end. Mounted only when APP_ENV is demo or test.

Each page behaves like a real product would: it starts OIDC with PKCE, exchanges the code, verifies the token and shows who
you are, which company you are in, and a product switcher built from /api/me/products.
"""
import json
import secrets
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import models as m, security, service
from ..config import get_settings
from ..db import get_db
from ..web import render, cookie_args

router = APIRouter(prefix="/demo")


def _client_id(key: str) -> str:
    return f"demo-{key}"


@router.get("/{key}/login")
def start(key: str, request: Request, tenant: str = "", db: Session = Depends(get_db)):
    if not db.get(m.OAuthClient, _client_id(key)):
        return render(request, "message.html", {"title": "Unknown demo product", "text": key}, 404)
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(12)
    iss = get_settings().issuer
    q = {"response_type": "code", "client_id": _client_id(key), "redirect_uri": f"{iss}/demo/{key}/callback", "scope": "openid profile email",
         "state": state, "nonce": secrets.token_urlsafe(8), "code_challenge": security.b64u(__import__("hashlib").sha256(verifier.encode()).digest()),
         "code_challenge_method": "S256"}
    if tenant:
        q["tenant"] = tenant
    resp = RedirectResponse(f"/authorize?{urlencode(q)}", 303)
    resp.set_cookie(f"demo_pkce_{key}", f"{verifier}.{state}", max_age=300, **cookie_args())
    return resp


@router.get("/{key}/callback")
def callback(key: str, request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    from .oidc import token

    saved = request.cookies.get(f"demo_pkce_{key}", ".").split(".")
    if error or not code or len(saved) != 2 or saved[1] != state:
        return render(request, "message.html", {"title": "Sign-in did not complete", "text": error or "The sign-in answer did not match.", "link": "/", "link_text": "Back to products"}, 400)
    iss = get_settings().issuer
    resp = token(request, grant_type="authorization_code", code=code, redirect_uri=f"{iss}/demo/{key}/callback", code_verifier=saved[0],
                 refresh_token="", client_id=_client_id(key), client_secret="", authorization=None, db=db)
    body = json.loads(resp.body)
    if "access_token" not in body:
        return render(request, "message.html", {"title": "Sign-in did not complete", "text": body.get("error", "error")}, 400)
    claims = security.verify(db, body["access_token"], audience=key)
    user = db.get(m.User, claims["sub"])
    pairs = service.eligible_pairs(db, user)
    for p in pairs:
        p.url = service.product_entry_url(p)  # type: ignore[attr-defined]
    r = render(request, "demo_product.html", {"key": key, "product": db.get(m.Product, key), "claims": claims, "user": user, "pairs": pairs,
                                              "raw": json.dumps({k: claims[k] for k in ("sub", "tid", "tenant_slug", "product", "email")}, indent=2)})
    r.delete_cookie(f"demo_pkce_{key}", path="/")
    return r


@router.get("/{key}/signed-out")
def signed_out(key: str, request: Request):
    return render(request, "message.html", {"title": "Signed out", "text": "You have left every Aadi product on this browser.", "link": "/login", "link_text": "Sign in again"})
