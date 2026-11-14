"""Platform administration: companies, subscriptions, people. Platform admins only; mutating calls need the CSRF header."""
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events, importer, mail, models as m, security, service
from ..config import get_settings
from ..db import get_db
from ..security import new_token
from ..web import CSRF_COOKIE, render
from .ui import current

router = APIRouter()


def admin_user(request: Request, db: Session = Depends(get_db)) -> m.User:
    cur = current(request, db)
    if not cur or not cur[1].is_platform_admin:
        raise HTTPException(403, "Platform administrator only")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-csrf-token") != request.cookies.get(CSRF_COOKIE):
        raise HTTPException(403, "Missing CSRF token")
    return cur[1]


@router.get("/admin")
def admin_page(request: Request, db: Session = Depends(get_db)):
    cur = current(request, db)
    if not cur:
        return RedirectResponse("/login?next=/admin", 303)
    if not cur[1].is_platform_admin:
        return render(request, "message.html", {"title": "Not allowed", "text": "Only platform administrators can open this page."}, 403)
    return render(request, "admin.html", {"user": cur[1]})


def _tenant_dict(t: m.Tenant) -> dict:
    return {"id": t.id, "name": t.name, "slug": t.slug, "status": t.status, "members": len([x for x in t.members if x.status == "active"]),
            "subscriptions": [{"product": s.product_key, "status": s.status, "valid_until": s.valid_until.isoformat() if s.valid_until else None} for s in t.subscriptions]}


@router.get("/admin/api/overview")
def overview(_: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    tenants = db.execute(select(m.Tenant).order_by(m.Tenant.name)).scalars().all()
    return {"products": [{"key": p.key, "name": p.name, "active": p.active, "kind": p.kind} for p in db.execute(select(m.Product)).scalars()],
            "tenants": [_tenant_dict(t) for t in tenants], "users": db.query(m.User).count(),
            "audit": [{"at": a.at.isoformat(), "action": a.action, "detail": a.detail} for a in db.execute(select(m.AuditLog).order_by(m.AuditLog.at.desc()).limit(15)).scalars()]}


class NewTenant(BaseModel):
    name: str
    owner_email: EmailStr
    owner_name: str
    products: list[str] = []


def invite(db: Session, user: m.User):
    tok, h = new_token()
    db.add(m.AuthToken(user_id=user.id, purpose="invite", token_hash=h, expires_at=m.now() + timedelta(days=7)))
    mail.send(user.email, "You have been invited to Aadi", f"Set your password (valid 7 days):\n{get_settings().issuer}/reset?token={tok}\n")


@router.post("/admin/api/tenants")
def create_tenant(b: NewTenant, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    slug = re.sub(r"[^a-z0-9]+", "-", b.name.lower()).strip("-")[:60]
    if not slug or db.execute(select(m.Tenant).where(m.Tenant.slug == slug)).scalar_one_or_none():
        raise HTTPException(409, "A company with a similar name already exists")
    for k in b.products:
        if not db.get(m.Product, k):
            raise HTTPException(422, f"Unknown product {k}")
    t = m.Tenant(name=b.name.strip(), slug=slug)
    db.add(t)
    db.flush()
    email = b.owner_email.lower()
    u = db.execute(select(m.User).where(m.User.email == email)).scalar_one_or_none()
    if not u:
        u = m.User(email=email, name=b.owner_name.strip(), status="invited")
        db.add(u)
        db.flush()
        invite(db, u)
    mem = m.TenantMember(tenant_id=t.id, user_id=u.id, is_admin=True)
    db.add(mem)
    for k in b.products:
        db.add(m.Subscription(tenant_id=t.id, product_key=k))
    db.flush()
    db.refresh(t)
    service.register_all_subscribed(db, t, u.id, "admin", actor.email)
    events.emit(db, "membership.added", tenant=t, user=u, member=mem)
    service.audit(db, "tenant.created", actor.id, t.id, name=t.name, products=b.products)
    db.commit()
    db.refresh(t)
    return _tenant_dict(t)


class SubChange(BaseModel):
    product: str
    status: str = "active"  # active | trial | suspended | cancelled
    valid_until: datetime | None = None


@router.post("/admin/api/tenants/{tid}/subscriptions")
def set_subscription(tid: str, b: SubChange, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    t = db.get(m.Tenant, tid)
    if not t or not db.get(m.Product, b.product):
        raise HTTPException(404, "Not found")
    if b.status not in ("active", "trial", "suspended", "cancelled"):
        raise HTTPException(422, "Bad status")
    s = db.execute(select(m.Subscription).where(m.Subscription.tenant_id == tid, m.Subscription.product_key == b.product)).scalar_one_or_none()
    if not s:
        s = m.Subscription(tenant_id=tid, product_key=b.product)
        db.add(s)
    s.status, s.valid_until = b.status, b.valid_until
    db.flush()
    if service.subscription_live(s):  # the company's administrators can reach a product the company has just got (to become its first administrator)
        for mem in t.members:
            if mem.is_admin and mem.status == "active":
                service.register(db, tid, mem.user_id, b.product, "admin", actor.email)
    events.emit(db, "subscription.changed", tenant=t, subscription=s, only_product=b.product)
    service.audit(db, "subscription.changed", actor.id, tid, product=b.product, status=b.status)
    db.commit()
    db.refresh(t)
    return _tenant_dict(t)


class TenantStatus(BaseModel):
    status: str


@router.post("/admin/api/tenants/{tid}/status")
def set_tenant_status(tid: str, b: TenantStatus, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    t = db.get(m.Tenant, tid)
    if not t or b.status not in ("active", "trial", "suspended"):
        raise HTTPException(404, "Not found")
    t.status = b.status
    events.emit(db, "tenant.status_changed", tenant=t)
    service.audit(db, "tenant.status", actor.id, tid, status=b.status)
    db.commit()
    return _tenant_dict(t)


@router.post("/admin/api/users/{uid}/revoke")
def revoke(uid: str, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    u = db.get(m.User, uid)
    if not u:
        raise HTTPException(404, "Not found")
    service.revoke_user_everywhere(db, u)
    events.emit(db, "user.revoked", user=u)
    service.audit(db, "user.revoked", actor.id, user=uid)
    db.commit()
    return {"ok": True}


class UserStatus(BaseModel):
    status: str  # active | suspended


@router.post("/admin/api/users/{uid}/status")
def set_user_status(uid: str, b: UserStatus, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    """Suspend or reactivate a person everywhere. Suspending signs them out of every product."""
    u = db.get(m.User, uid)
    if not u or b.status not in ("active", "suspended") or u.id == actor.id:
        raise HTTPException(404 if not u else 422, "Not allowed")
    u.status = b.status
    if b.status == "suspended":
        service.revoke_user_everywhere(db, u)
    events.emit(db, "user.suspended" if b.status == "suspended" else "user.reactivated", user=u)
    service.audit(db, "user.status", actor.id, user=uid, status=b.status)
    db.commit()
    return {"id": u.id, "status": u.status}


@router.post("/admin/api/keys/rotate")
def rotate(actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    k = security.rotate_signing_key(db)
    service.audit(db, "key.rotated", actor.id, kid=k.kid)
    db.commit()
    return {"kid": k.kid}


# ---- service clients: how a product's server gets tokens for a shared service ----
def _client_dict(c: m.ServiceClient) -> dict:
    return {"client_id": c.client_id, "name": c.name, "product": c.product_key, "audiences": c.audiences, "scopes": c.scopes, "active": c.active,
            "secret_rotated_at": m.aware(c.secret_rotated_at).isoformat()}


@router.get("/admin/api/service-clients")
def list_service_clients(_: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    return {"clients": [_client_dict(c) for c in db.execute(select(m.ServiceClient).order_by(m.ServiceClient.client_id)).scalars()],
            "services": [{"key": service.IDENTITY_AUDIENCE, "name": "Aadi Identity (enrol, remove, seats, snapshot)"}]
                        + [{"key": p.key, "name": p.name} for p in db.execute(select(m.Product).where(m.Product.kind == "service")).scalars()]}


class NewServiceClient(BaseModel):
    client_id: str
    name: str
    product_key: str
    audiences: list[str]
    scopes: list[str]


@router.post("/admin/api/service-clients")
def create_service_client(b: NewServiceClient, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    """Creates the credential. The secret is returned once and only its hash is kept."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,60}", b.client_id) or db.get(m.ServiceClient, b.client_id):
        raise HTTPException(409, "Use 3-60 lowercase letters, digits or dashes; the name must be new")
    if not db.get(m.Product, b.product_key):
        raise HTTPException(422, "Unknown calling product")
    for a in b.audiences:
        p = db.get(m.Product, a)
        if a != service.IDENTITY_AUDIENCE and (not p or p.kind != "service"):
            raise HTTPException(422, f"{a} is not a registered service")
    if service.IDENTITY_AUDIENCE in b.audiences and not {x for x in b.scopes if x.startswith("identity:")} <= service.IDENTITY_SCOPES:
        raise HTTPException(422, "Unknown Identity scope")
    if not b.audiences or not b.scopes:
        raise HTTPException(422, "Choose at least one service and one scope")
    secret = new_token()[0]
    c = m.ServiceClient(client_id=b.client_id, name=b.name.strip(), product_key=b.product_key, secret_hash=security.hash_token(secret),
                        audiences=b.audiences, scopes=b.scopes)
    db.add(c)
    service.audit(db, "service_client.created", actor.id, None, client=c.client_id, audiences=b.audiences, scopes=b.scopes)
    db.commit()
    return {**_client_dict(c), "secret": secret}


@router.post("/admin/api/service-clients/{cid}/rotate")
def rotate_service_client(cid: str, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    c = db.get(m.ServiceClient, cid)
    if not c:
        raise HTTPException(404, "Not found")
    secret = new_token()[0]
    c.secret_hash, c.secret_rotated_at = security.hash_token(secret), m.now()
    service.audit(db, "service_client.rotated", actor.id, None, client=cid)
    db.commit()
    return {**_client_dict(c), "secret": secret}


class ClientActive(BaseModel):
    active: bool


@router.post("/admin/api/service-clients/{cid}/status")
def set_service_client_status(cid: str, b: ClientActive, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    c = db.get(m.ServiceClient, cid)
    if not c:
        raise HTTPException(404, "Not found")
    c.active = b.active
    service.audit(db, "service_client.enabled" if b.active else "service_client.disabled", actor.id, None, client=cid)
    db.commit()
    return _client_dict(c)


@router.get("/admin/api/ops")
def ops_status(_: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    """Is Identity healthy, and are products receiving their events?"""
    from sqlalchemy import func

    now = m.now()
    pending = db.execute(select(m.Outbox).where(m.Outbox.delivered_at.is_(None), m.Outbox.failed.is_(False)).order_by(m.Outbox.created_at)).scalars().all()
    failed = db.execute(select(func.count()).select_from(m.Outbox).where(m.Outbox.failed.is_(True))).scalar()
    last = db.execute(select(func.max(m.Outbox.delivered_at))).scalar()
    keys = db.execute(select(m.SigningKey).order_by(m.SigningKey.created_at.desc())).scalars().all()
    active = next((k for k in keys if k.active), None)
    return {
        "database": "ok",
        "events": {"pending": len(pending), "failed": failed,
                   "oldest_pending_seconds": int((now - m.aware(pending[0].created_at)).total_seconds()) if pending else 0,
                   "last_delivered": last.isoformat() if last else None},
        "signing_key": {"kid": active.kid if active else None, "age_days": (now - m.aware(active.created_at)).days if active else None,
                        "published": len([k for k in keys if k.published])},
        "products": [{"key": p.key, "webhook": bool(p.webhook_url)} for p in db.execute(select(m.Product)).scalars()],
        "counts": {"users": db.execute(select(func.count()).select_from(m.User)).scalar(), "companies": db.execute(select(func.count()).select_from(m.Tenant)).scalar(),
                   "live_sessions": db.execute(select(func.count()).select_from(m.LoginSession).where(m.LoginSession.revoked.is_(False), m.LoginSession.expires_at > now)).scalar()},
    }


@router.post("/admin/api/maintenance/prune")
def run_prune(actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    from ..maintenance import prune

    out = prune(db)
    service.audit(db, "maintenance.prune", actor.id, **out)
    db.commit()
    return out


class ImportRequest(BaseModel):
    dry_run: bool = True
    companies: list[str] = []  # slugs to bring across; a real import needs at least one


@router.post("/admin/api/import/tracker")
def import_from_tracker(b: ImportRequest, actor: m.User = Depends(admin_user), db: Session = Depends(get_db)):
    """Brings Project Tracker's people and companies (with their existing passwords) into Identity.
    Project Tracker must have its export switched on and share the secret. A preview (dry run) writes nothing."""
    import urllib.error

    p = db.get(m.Product, "tracker")
    if not p or not p.webhook_url or not p.webhook_secret:
        raise HTTPException(409, "Project Tracker is not connected yet: set TRACKER_URL and TRACKER_WEBHOOK_SECRET on Identity first.")
    url = p.webhook_url.replace("/identity-events", "/identity-export")
    try:
        data = importer.fetch_tracker_export(url, p.webhook_secret)
    except urllib.error.HTTPError as e:
        raise HTTPException(502, {401: "Project Tracker refused the secret: it must be the same on both sides.",
                                  404: "Project Tracker's export is switched off (set IDENTITY_EXPORT_ENABLED=true there for the import, then turn it off)."}.get(e.code, f"Project Tracker answered {e.code}."))
    except Exception as e:
        raise HTTPException(502, f"Could not reach Project Tracker: {e}")
    companies = importer.summarize(data)
    if not b.companies:
        if not b.dry_run:
            raise HTTPException(422, "Choose at least one company to import.")
        return {"dry_run": True, "companies": companies, "users_created": 0, "users_updated": 0, "tenants_created": 0, "tenants_updated": 0,
                "members_created": 0, "subscriptions_created": 0, "skipped": []}
    known = {c["slug"] for c in companies}
    if not set(b.companies) <= known:
        raise HTTPException(422, "Unknown company in the selection.")
    report = importer.import_payload(db, importer.only(data, b.companies))
    if b.dry_run:
        db.rollback()
    else:
        service.audit(db, "import.tracker", actor.id, companies=b.companies, **{k: v for k, v in report.as_dict().items() if k != "skipped"})
        db.commit()
    return {**report.as_dict(), "dry_run": b.dry_run, "companies": companies}
