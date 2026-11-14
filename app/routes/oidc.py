"""OpenID Connect: discovery, JWKS, authorize (code + PKCE), token, userinfo, products, logout."""
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, Form, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m, ratelimit, security, service
from ..config import get_settings
from ..db import get_db
from ..security import hash_token, new_token, pkce_ok
from ..web import SESSION_COOKIE, render
from .ui import current, end_session

router = APIRouter()


@router.get("/.well-known/openid-configuration")
def discovery():
    i = get_settings().issuer
    return {"issuer": i, "authorization_endpoint": f"{i}/authorize", "token_endpoint": f"{i}/token",
            "userinfo_endpoint": f"{i}/userinfo", "jwks_uri": f"{i}/.well-known/jwks.json", "end_session_endpoint": f"{i}/logout",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
            "subject_types_supported": ["public"], "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"], "scopes_supported": ["openid", "profile", "email", "offline_access"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
            "claims_supported": ["sub", "email", "name", "tid", "tenant_slug", "product", "sid"]}


@router.get("/.well-known/jwks.json")
def jwks_doc(db: Session = Depends(get_db)):
    return JSONResponse(security.jwks(db), headers={"Cache-Control": "public, max-age=300"})


def _err_page(request, text, status=400):
    return render(request, "message.html", {"title": "Cannot sign in", "text": text}, status)


def _redirect_error(redirect_uri, error, state):
    q = {"error": error}
    if state:
        q["state"] = state
    return RedirectResponse(f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{urlencode(q)}", 303)


@router.get("/authorize")
def authorize(request: Request, response_type: str = "", client_id: str = "", redirect_uri: str = "", scope: str = "openid",
              state: str = "", nonce: str = "", code_challenge: str = "", code_challenge_method: str = "", tenant: str = "",
              db: Session = Depends(get_db)):
    c = db.get(m.OAuthClient, client_id)
    # Until client and redirect URI are verified we never redirect, to avoid open redirects.
    if not c or not c.active or redirect_uri not in (c.redirect_uris or []):
        return _err_page(request, "This application is not registered or the return address does not match.")
    if response_type != "code":
        return _redirect_error(redirect_uri, "unsupported_response_type", state)
    if not code_challenge or code_challenge_method != "S256":
        return _redirect_error(redirect_uri, "invalid_request", state)
    cur = current(request, db)
    if not cur:
        return RedirectResponse("/login?" + urlencode({"next": str(request.url.path) + "?" + str(request.url.query)}), 303)
    sess, user = cur
    pairs = [p for p in service.eligible_pairs(db, user) if p.product_key == c.product_key]
    if tenant:
        pairs = [p for p in pairs if p.tenant_id == tenant]
        if not pairs:
            return _redirect_error(redirect_uri, "access_denied", state)
    if not pairs:
        if service.eligible_pairs(db, user):  # has other products: send to the hub with an explanation
            return render(request, "message.html", {"title": "Product not available", "text": f"Your companies have no active {db.get(m.Product, c.product_key).name} subscription for you.", "link": "/", "link_text": "Choose another product"}, 403)
        return render(request, "no_access.html", {"user": user}, 403)
    if len(pairs) > 1:  # same product in several companies: choose the company
        params = dict(request.query_params)
        for p in pairs:
            p.url = "/authorize?" + urlencode({**params, "tenant": p.tenant_id})  # type: ignore[attr-defined]
            p.product_name = p.tenant_name  # type: ignore[misc]
        return render(request, "choose.html", {"user": user, "groups": [(db.get(m.Product, c.product_key).name, pairs)]})
    code, h = new_token()
    db.add(m.AuthCode(code_hash=h, client_id=c.client_id, user_id=user.id, tenant_id=pairs[0].tenant_id, session_id=sess.id,
                      redirect_uri=redirect_uri, code_challenge=code_challenge, nonce=nonce,
                      expires_at=m.now() + timedelta(seconds=get_settings().auth_code_seconds)))
    service.audit(db, "auth.code_issued", user.id, pairs[0].tenant_id, client=c.client_id)
    db.commit()
    q = {"code": code}
    if state:
        q["state"] = state
    return RedirectResponse(f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{urlencode(q)}", 303)


def _oauth_error(error: str, desc: str = "", status: int = 400):
    return JSONResponse({"error": error, "error_description": desc}, status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _client_auth(db, request: Request, client_id: str, client_secret: str, authorization: str | None):
    if authorization and authorization.lower().startswith("basic "):
        import base64
        try:
            cid, _, sec = base64.b64decode(authorization[6:]).decode().partition(":")
            client_id, client_secret = cid, sec
        except Exception:
            return None
    c = db.get(m.OAuthClient, client_id)
    if not c or not c.active:
        return None
    if c.secret_hash and not (client_secret and secrets.compare_digest(c.secret_hash, hash_token(client_secret))):
        return None
    return c


def _service_client(db, client_id: str, client_secret: str, authorization: str | None):
    if authorization and authorization.lower().startswith("basic "):
        import base64
        try:
            client_id, _, client_secret = base64.b64decode(authorization[6:]).decode().partition(":")
        except Exception:
            return None
    c = db.get(m.ServiceClient, client_id)
    if not c or not c.active or not client_secret or not secrets.compare_digest(c.secret_hash, hash_token(client_secret)):
        return None
    return c


def _identity_token(db: Session, c: m.ServiceClient, scope: str, tenant_id: str):
    """A product's server asks for a token to call Identity's own API (enrol, remove, seats, snapshot). Replaces the shared product secret.
    Tokens carry the company when one is given, and the API then refuses any other company. Only the snapshot scope may be company-free."""
    wanted = scope.split()
    if service.IDENTITY_AUDIENCE not in (c.audiences or []):
        return _oauth_error("invalid_target", "This client may not ask for a token for that service")
    if not wanted or not set(wanted) <= set(c.scopes or []) or not set(wanted) <= service.IDENTITY_SCOPES:
        return _oauth_error("invalid_scope", "Scope not allowed for this client")
    claims = {"sub": c.client_id, "client_id": c.client_id, "aud": service.IDENTITY_AUDIENCE, "scope": " ".join(wanted),
              "caller_product": c.product_key, "jti": m.new_id()}
    t = None
    if tenant_id or not set(wanted) <= service.TENANT_FREE_SCOPES:
        t = db.get(m.Tenant, tenant_id) if tenant_id else None
        if not t or t.status not in ("active", "trial"):
            return _oauth_error("invalid_request", "Unknown or inactive company (tenant_id)")
        if not service.service_entitled(db, t.id, c.product_key):  # the calling product must be live for that company
            service.audit(db, "service_token.denied", None, t.id, client=c.client_id, reason="caller_not_subscribed", audience=service.IDENTITY_AUDIENCE)
            db.commit()
            return _oauth_error("access_denied", "The company has no active subscription for the calling product")
        claims["tenant_id"] = t.id
    minutes = get_settings().service_token_minutes
    tok = security.sign(db, claims, minutes, typ="at+jwt")
    service.audit(db, "service_token.issued", None, t.id if t else None, client=c.client_id, audience=service.IDENTITY_AUDIENCE, scope=" ".join(wanted))
    db.commit()
    return JSONResponse({"token_type": "Bearer", "access_token": tok, "expires_in": minutes * 60, "scope": " ".join(wanted)},
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _client_credentials(db: Session, ip: str, client_id: str, client_secret: str, authorization: str | None, audience: str, scope: str, tenant_id: str):
    """A product's server asks for a token to call a shared service for ONE company. The token carries that company, so the service never has to trust a header."""
    c = _service_client(db, client_id, client_secret, authorization)
    if not c:
        if ratelimit.hit(f"token-bad-client:{ip}", 20):
            return _oauth_error("slow_down", "Too many failed attempts", 429)
        return _oauth_error("invalid_client", status=401)
    if audience == service.IDENTITY_AUDIENCE:
        return _identity_token(db, c, scope, tenant_id)
    svc = db.get(m.Product, audience)
    if not audience or not svc or svc.kind != "service" or not svc.active or audience not in (c.audiences or []):
        return _oauth_error("invalid_target", "This client may not ask for a token for that service")
    wanted = scope.split()
    if not wanted or not set(wanted) <= set(c.scopes or []):
        return _oauth_error("invalid_scope", "Scope not allowed for this client")
    t = db.get(m.Tenant, tenant_id) if tenant_id else None
    if not t or t.status not in ("active", "trial"):
        return _oauth_error("invalid_request", "Unknown or inactive company (tenant_id)")
    # Two checks, so a product can never obtain a token for a company that does not use it (confused deputy):
    if not service.service_entitled(db, t.id, c.product_key):
        service.audit(db, "service_token.denied", None, t.id, client=c.client_id, reason="caller_not_subscribed", audience=audience)
        db.commit()
        return _oauth_error("access_denied", "The company has no active subscription for the calling product")
    if not service.service_entitled(db, t.id, audience):
        service.audit(db, "service_token.denied", None, t.id, client=c.client_id, reason="service_not_entitled", audience=audience)
        db.commit()
        return _oauth_error("access_denied", "The company has no active subscription for this service")
    minutes = get_settings().service_token_minutes
    tok = security.sign(db, {"sub": c.client_id, "client_id": c.client_id, "aud": audience, "scope": " ".join(wanted), "tenant_id": t.id,
                             "caller_product": c.product_key, "jti": m.new_id()}, minutes, typ="at+jwt")
    service.audit(db, "service_token.issued", None, t.id, client=c.client_id, audience=audience, scope=" ".join(wanted))
    db.commit()
    return JSONResponse({"token_type": "Bearer", "access_token": tok, "expires_in": minutes * 60, "scope": " ".join(wanted)},
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _issue(db: Session, c: m.OAuthClient, user: m.User, tenant_id: str, session_id: str, nonce: str, family: str | None = None):
    cfg = get_settings()
    t = db.get(m.Tenant, tenant_id)
    base = {"sub": user.id, "email": user.email, "name": user.name, "tid": t.id, "tenant_slug": t.slug,
            "product": c.product_key, "sid": session_id, "ep": user.token_epoch}
    id_token = security.sign(db, {**base, "aud": c.client_id, **({"nonce": nonce} if nonce else {})}, cfg.id_token_minutes)
    access = security.sign(db, {**base, "aud": c.product_key, "scope": "openid profile email"}, cfg.access_token_minutes)
    rt, h = new_token()
    db.add(m.RefreshToken(token_hash=h, family=family or m.new_id(), client_id=c.client_id, user_id=user.id, tenant_id=tenant_id,
                          session_id=session_id, expires_at=m.now() + timedelta(days=cfg.refresh_token_days)))
    return {"token_type": "Bearer", "access_token": access, "id_token": id_token, "refresh_token": rt,
            "expires_in": cfg.access_token_minutes * 60}


def _still_eligible(db, user: m.User, tenant_id: str, product_key: str) -> bool:
    return user.status == "active" and any(p.tenant_id == tenant_id and p.product_key == product_key for p in service.eligible_pairs(db, user))


@router.post("/token")
def token(request: Request, grant_type: str = Form(""), code: str = Form(""), redirect_uri: str = Form(""), code_verifier: str = Form(""),
          refresh_token: str = Form(""), client_id: str = Form(""), client_secret: str = Form(""),
          audience: str = Form(""), scope: str = Form(""), tenant_id: str = Form(""), authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if ratelimit.hit(f"token:{ip}", get_settings().token_rate_per_minute):
        return _oauth_error("slow_down", "Too many requests", 429)
    if grant_type == "client_credentials":
        return _client_credentials(db, ip, client_id, client_secret, authorization, audience, scope, tenant_id)
    c = _client_auth(db, request, client_id, client_secret, authorization)
    if not c:
        if ratelimit.hit(f"token-bad-client:{ip}", 20):  # someone is guessing client secrets
            return _oauth_error("slow_down", "Too many failed attempts", 429)
        return _oauth_error("invalid_client", status=401)
    if grant_type == "authorization_code":
        ac = db.get(m.AuthCode, hash_token(code))
        if not ac or ac.client_id != c.client_id or ac.redirect_uri != redirect_uri:
            return _oauth_error("invalid_grant")
        if ac.used:  # replay: someone is guessing or re-using a code; kill what it produced
            for r in db.execute(select(m.RefreshToken).where(m.RefreshToken.session_id == ac.session_id, m.RefreshToken.client_id == c.client_id)).scalars():
                r.revoked = True
            db.commit()
            return _oauth_error("invalid_grant")
        ac.used = True
        if m.aware(ac.expires_at) < m.now() or not code_verifier or not pkce_ok(code_verifier, ac.code_challenge):
            db.commit()
            return _oauth_error("invalid_grant")
        user = db.get(m.User, ac.user_id)
        if not user or not _still_eligible(db, user, ac.tenant_id, c.product_key):
            db.commit()
            return _oauth_error("invalid_grant", "access no longer allowed")
        out = _issue(db, c, user, ac.tenant_id, ac.session_id, ac.nonce)
        service.audit(db, "auth.token_issued", user.id, ac.tenant_id, client=c.client_id)
        db.commit()
        return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    if grant_type == "refresh_token":
        r = db.execute(select(m.RefreshToken).where(m.RefreshToken.token_hash == hash_token(refresh_token))).scalar_one_or_none()
        if not r or r.client_id != c.client_id:
            return _oauth_error("invalid_grant")
        if r.used:  # reuse of a rotated token means theft: revoke the whole family
            for x in db.execute(select(m.RefreshToken).where(m.RefreshToken.family == r.family)).scalars():
                x.revoked = True
            service.audit(db, "auth.refresh_reuse", r.user_id, r.tenant_id)
            db.commit()
            return _oauth_error("invalid_grant")
        user = db.get(m.User, r.user_id)
        if r.revoked or m.aware(r.expires_at) < m.now() or not user or not _still_eligible(db, user, r.tenant_id, c.product_key):
            return _oauth_error("invalid_grant")
        r.used = True
        out = _issue(db, c, user, r.tenant_id, r.session_id, "", family=r.family)
        db.commit()
        return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    return _oauth_error("unsupported_grant_type")


def _bearer_user(db: Session, authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        return None, None
    try:
        claims = security.verify(db, authorization[7:])
    except jwt.PyJWTError:
        return None, None
    u = db.get(m.User, claims.get("sub"))
    if not u or u.status != "active" or u.token_epoch != claims.get("ep"):
        return None, None
    return u, claims


@router.get("/userinfo")
def userinfo(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    u, c = _bearer_user(db, authorization)
    if not u:
        return JSONResponse({"error": "invalid_token"}, 401, headers={"WWW-Authenticate": "Bearer"})
    return {"sub": u.id, "email": u.email, "name": u.name, "tid": c["tid"], "tenant_slug": c.get("tenant_slug"), "product": c.get("product")}


@router.get("/api/v1/me/products")
@router.get("/api/me/products")
def my_products(request: Request, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Feeds the product-header switcher. Called by a product's backend with the user's access token (or by the browser on this domain)."""
    u, _ = _bearer_user(db, authorization)
    if not u:
        cur = current(request, db)
        u = cur[1] if cur else None
    if not u:
        return JSONResponse({"error": "unauthorized"}, 401)
    pairs = service.visible_pairs(db, u)
    return {"next": service.next_step(pairs), "hub": get_settings().issuer + "/",
            "items": [{"tenant_id": p.tenant_id, "tenant": p.tenant_name, "product": p.product_key, "product_name": p.product_name,
                       "url": service.product_entry_url(p)} for p in pairs]}


@router.get("/logout")
def logout_redirect(request: Request, client_id: str = "", post_logout_redirect_uri: str = "", state: str = "", db: Session = Depends(get_db)):
    """RP-initiated logout. Only a registered return address is honoured."""
    c = db.get(m.OAuthClient, client_id) if client_id else None
    end_session(request, db)
    if c and post_logout_redirect_uri in (c.post_logout_redirect_uris or []):
        url = post_logout_redirect_uri + (("&" if "?" in post_logout_redirect_uri else "?") + urlencode({"state": state}) if state else "")
        resp = RedirectResponse(url, 303)
    else:
        resp = RedirectResponse("/login", 303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
