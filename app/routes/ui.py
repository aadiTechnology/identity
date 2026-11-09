"""Browser-facing pages: sign in, product hub, password reset, sign out."""
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import mail, models as m, ratelimit, service
from ..config import get_settings
from ..db import get_db
from ..security import burn_time, check_password_strength, hash_password, hash_token, new_token, verify_password
from ..web import SESSION_COOKIE, cookie_args, csrf_ok, render, safe_next

router = APIRouter()
def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _rate_limited(ip: str) -> bool:
    return ratelimit.hit(f"login:{ip}", get_settings().login_rate_per_minute)


def current(request: Request, db: Session):
    return service.load_session(db, request.cookies.get(SESSION_COOKIE))


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    """Ready = the database answers and there is a signing key. (Delivery health is on the admin status page.)"""
    from fastapi.responses import JSONResponse

    try:
        db.execute(select(1))
        key = db.execute(select(m.SigningKey).where(m.SigningKey.active.is_(True))).scalars().first()
    except Exception:
        return JSONResponse({"status": "database unavailable"}, 503)
    return {"status": "ready"} if key else JSONResponse({"status": "no signing key"}, 503)


@router.get("/")
def hub(request: Request, db: Session = Depends(get_db)):
    """After sign-in: no product -> explain; one -> go straight in; several -> chooser."""
    cur = current(request, db)
    if not cur:
        return RedirectResponse("/login", 303)
    _, user = cur
    pairs = service.eligible_pairs(db, user)
    step = service.next_step(pairs)
    if step == "none":
        if user.is_platform_admin:  # administrators belong to no company: send them to the admin page, not an error
            return RedirectResponse("/admin", 303)
        return render(request, "no_access.html", {"user": user})
    if step == "direct":
        return RedirectResponse(service.product_entry_url(pairs[0]), 303)
    groups: dict[str, list] = {}
    for p in pairs:
        p.url = service.product_entry_url(p)  # type: ignore[attr-defined]
        groups.setdefault(p.tenant_name, []).append(p)
    return render(request, "choose.html", {"user": user, "groups": list(groups.items())})


@router.get("/login")
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if current(request, db):
        return RedirectResponse(safe_next(next), 303)
    return render(request, "login.html", {"next": safe_next(next)})


@router.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/"),
          csrf: str = Form(""), db: Session = Depends(get_db)):
    cfg = get_settings()
    bad = lambda status=401: render(request, "login.html", {"error": "Email or password is incorrect.", "email": email, "next": safe_next(next)}, status)
    if not csrf_ok(request, csrf):
        return render(request, "login.html", {"error": "Your page expired. Please try again.", "next": safe_next(next)}, 400)
    if _rate_limited(request.client.host if request.client else "?"):
        return render(request, "login.html", {"error": "Too many attempts. Wait a minute and try again.", "next": safe_next(next)}, 429)
    u = db.execute(select(m.User).where(m.User.email == email.strip().lower())).scalar_one_or_none()
    if not u:
        burn_time()
        return bad()
    now = m.now()
    locked = m.aware(u.locked_until) and m.aware(u.locked_until) > now
    ok, rehash = verify_password(password, u.password_hash) if not locked else (False, False)
    if locked or u.status != "active" or not ok:
        if not locked and u.status == "active":
            u.failed_logins += 1
            if u.failed_logins >= cfg.lock_after_failures:
                u.locked_until, u.failed_logins = now + timedelta(minutes=cfg.lock_minutes), 0
                service.audit(db, "auth.locked", u.id)
            db.commit()
        return bad()
    u.failed_logins, u.locked_until = 0, None
    if rehash:
        u.password_hash = hash_password(password)
    tok, _ = service.create_session(db, u)
    service.audit(db, "auth.login", u.id)
    db.commit()
    resp = RedirectResponse(safe_next(next), 303)
    resp.set_cookie(SESSION_COOKIE, tok, max_age=cfg.session_hours * 3600, **cookie_args())
    return resp


@router.post("/logout")
def logout(request: Request, csrf: str = Form(""), db: Session = Depends(get_db)):
    if not csrf_ok(request, csrf):
        return render(request, "login.html", {"error": "Your page expired. Please try again."}, 400)
    end_session(request, db)
    resp = RedirectResponse("/login", 303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def end_session(request: Request, db: Session):
    cur = current(request, db)
    if cur:
        s, u = cur
        s.revoked = True
        for r in db.execute(select(m.RefreshToken).where(m.RefreshToken.session_id == s.id)).scalars():
            r.revoked = True
        service.audit(db, "auth.logout", u.id)
        db.commit()


@router.get("/forgot")
def forgot_page(request: Request):
    return render(request, "forgot.html")


@router.post("/forgot")
def forgot(request: Request, email: str = Form(""), csrf: str = Form(""), db: Session = Depends(get_db)):
    if not csrf_ok(request, csrf):
        return render(request, "forgot.html", status=400)
    if ratelimit.hit(f"forgot:{_ip(request)}", 10):
        return render(request, "message.html", {"title": "Too many requests", "text": "Please wait a minute and try again."}, 429)
    quiet = ratelimit.hit(f"forgot-email:{email.strip().lower()}", 3, 600)  # a fourth request in ten minutes sends nothing, but answers the same
    u = db.execute(select(m.User).where(m.User.email == email.strip().lower())).scalar_one_or_none()
    if u and u.status in ("active", "invited") and not quiet:
        tok, h = new_token()
        db.add(m.AuthToken(user_id=u.id, purpose="reset", token_hash=h, expires_at=m.now() + timedelta(hours=1)))
        db.commit()
        mail.send(u.email, "Reset your Aadi password", f"Use this link within 1 hour:\n{get_settings().issuer}/reset?token={tok}\n")
    return render(request, "forgot.html", {"sent": True})  # same answer whether or not the account exists


def _valid_token(db: Session, token: str) -> m.AuthToken | None:
    t = db.execute(select(m.AuthToken).where(m.AuthToken.token_hash == hash_token(token))).scalar_one_or_none()
    return t if t and not t.used and m.aware(t.expires_at) > m.now() else None


@router.get("/reset")
def reset_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    t = _valid_token(db, token)
    if not t:
        return render(request, "message.html", {"title": "Link expired", "text": "This link has already been used or has expired.", "link": "/forgot", "link_text": "Request a new link"}, 400)
    return render(request, "reset.html", {"token": token, "purpose": t.purpose})


@router.post("/reset")
def reset(request: Request, token: str = Form(""), password: str = Form(""), csrf: str = Form(""), db: Session = Depends(get_db)):
    if not csrf_ok(request, csrf):
        return render(request, "message.html", {"title": "Page expired", "text": "Please open the link again."}, 400)
    if ratelimit.hit(f"reset:{_ip(request)}", 10):
        return render(request, "message.html", {"title": "Too many requests", "text": "Please wait a minute and try again."}, 429)
    t = _valid_token(db, token)
    if not t:
        return render(request, "message.html", {"title": "Link expired", "text": "This link has already been used or has expired.", "link": "/forgot", "link_text": "Request a new link"}, 400)
    weak = check_password_strength(password)
    if weak:
        return render(request, "reset.html", {"token": token, "purpose": t.purpose, "error": weak}, 422)
    u = db.get(m.User, t.user_id)
    u.password_hash, u.status = hash_password(password), "active"
    u.failed_logins, u.locked_until = 0, None
    t.used = True
    service.revoke_user_everywhere(db, u)  # a reset signs the person out everywhere
    service.audit(db, "auth.password_set", u.id, purpose=t.purpose)
    db.commit()
    return render(request, "message.html", {"title": "Password saved", "text": "You can now sign in.", "link": "/login", "link_text": "Sign in"})
