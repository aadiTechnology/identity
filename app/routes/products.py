"""What a product may ask Identity to do for its own people. Authenticated with the product's shared secret (Bearer).

enrol   a product administrator adds a person: Identity finds or creates the account, makes them a member of the company, registers them
        in THIS product only, counts a seat, sends the invitation, and tells the company's administrators. Needs no company-admin approval.
remove  the product removes the person from itself: their registration here ends; their company membership ends only when no product is left.
seats   how many seats the company has in the product and how many are used.

A product can only act for companies that have it (a live subscription), never makes anyone a company administrator, and cannot
override a company administrator's suspension."""
import secrets

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events, mail, ratelimit, security, service
from .. import models as m
from ..config import get_settings
from ..db import get_db
from .admin import invite
from .company import _end_tenant_tokens

router = APIRouter()
HOURLY_LIMIT = 300  # enrolments and removals per product per hour; a bulk import should be paced


def _bearer(authorization: str | None) -> str:
    return authorization[7:] if authorization and authorization.lower().startswith("bearer ") else ""


def token_principal(db: Session, token: str, scope: str, tenant_id: str | None) -> m.Product:
    """A product's server calling with an Identity service token (audience identity). Checks signature, issuer, audience, expiry, the scope, that the
    token is a service token, and, when the call names a company, that it is the company the token was issued for."""
    try:
        claims = security.verify(db, token, audience=service.IDENTITY_AUDIENCE)
        if jwt.get_unverified_header(token).get("typ") != "at+jwt":
            raise jwt.InvalidTokenError("not a service token")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token") from None
    if scope not in set(str(claims.get("scope", "")).split()):
        raise HTTPException(403, f"The token does not allow {scope}")
    p = db.get(m.Product, claims.get("caller_product") or "")
    if not p or not p.active:
        raise HTTPException(401, "Invalid product credentials")
    if tenant_id is not None and claims.get("tenant_id") != tenant_id:
        raise HTTPException(403, "The token was issued for a different company")
    return p


def product_of(authorization: str | None, db: Session, scope: str = "", tenant_id: str | None = None) -> m.Product:
    """Who is calling. Two ways, both accepted for now: an Identity service token (preferred), or the product's shared secret (to be retired)."""
    token = _bearer(authorization)
    if token.count(".") == 2:  # a JWT: shared secrets never look like this
        p = token_principal(db, token, scope, tenant_id)
        if ratelimit.hit(f"enrol:{p.key}", HOURLY_LIMIT, 3600.0):
            raise HTTPException(429, "Too many requests from this product. Try again later.")
        return p
    for p in db.execute(select(m.Product).where(m.Product.active.is_(True))).scalars():
        if p.webhook_secret and token and secrets.compare_digest(token, p.webhook_secret):
            if ratelimit.hit(f"enrol:{p.key}", HOURLY_LIMIT, 3600.0):
                raise HTTPException(429, "Too many requests from this product. Try again later.")
            return p
    raise HTTPException(401, "Invalid product credentials")


def _tenant_and_sub(db: Session, tenant_id: str, product: m.Product) -> tuple[m.Tenant, m.Subscription]:
    t = db.get(m.Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "Unknown company")
    if t.status not in ("active", "trial"):
        raise HTTPException(403, "This company is not active")
    sub = next((s for s in t.subscriptions if s.product_key == product.key), None)
    if sub is None or not service.subscription_live(sub):
        raise HTTPException(403, f"{product.name} is not enabled for this company")
    return t, sub


class Enrol(BaseModel):
    tenant_id: str
    email: EmailStr
    name: str = ""
    actor: str = ""  # the product administrator who asked (email), for the audit log and the notice to the company


@router.post("/api/v1/products/enrol")
@router.post("/api/products/enrol")
def enrol(b: Enrol, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    p = product_of(authorization, db, "identity:enrol", b.tenant_id)
    t, sub = _tenant_and_sub(db, b.tenant_id, p)
    email = b.email.lower()
    user = db.execute(select(m.User).where(m.User.email == email)).scalar_one_or_none()
    created = user is None
    if created:
        user = m.User(email=email, name=(b.name or email.split("@")[0]).strip(), status="invited")
        db.add(user)
        db.flush()
    elif user.status == "suspended":
        raise HTTPException(409, "This person's account is suspended")
    mem = db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id, m.TenantMember.user_id == user.id)).scalar_one_or_none()
    if mem is not None and mem.status == "suspended":
        raise HTTPException(409, "A company administrator suspended this person; they must reactivate them")
    already = db.execute(select(m.MemberProduct).where(m.MemberProduct.tenant_id == t.id, m.MemberProduct.user_id == user.id,
                                                       m.MemberProduct.product_key == p.key, m.MemberProduct.status == "active")).scalar_one_or_none()
    if already is None and sub.seats is not None and service.seats_used(db, t.id, p.key) >= sub.seats:
        raise HTTPException(409, f"All {sub.seats} seats of {p.name} are in use")
    new_member = mem is None or mem.status == "removed"
    if mem is None:
        mem = m.TenantMember(tenant_id=t.id, user_id=user.id)
        db.add(mem)
    elif mem.status == "removed":
        mem.status = "active"
    db.flush()
    service.register(db, t.id, user.id, p.key, "product", b.actor or p.key)
    invited = False
    if user.status == "invited":  # no password yet: send (or re-send) the invitation
        invite(db, user)
        invited = True
    if new_member:
        events.emit(db, "membership.added", tenant=t, user=user, member=mem)
    service.audit(db, "member.enrolled", None, t.id, email=email, product=p.key, by=b.actor or p.key, created=created, invited=invited)
    db.commit()
    if already is None:
        _notify_admins(db, t, p, email, b.actor)
    return {"identity_id": user.id, "email": email, "user_status": user.status, "invited": invited, "created": created,
            "membership_status": mem.status, "registered_products": service.registered_products(db, t.id, user.id),
            "seats": {"total": sub.seats, "used": service.seats_used(db, t.id, p.key)}}


def _notify_admins(db: Session, t: m.Tenant, p: m.Product, email: str, actor: str) -> None:
    """Tell the company's administrators who was added (best effort: a mail problem never undoes the enrolment)."""
    try:
        for mem in t.members:
            if mem.is_admin and mem.status == "active" and mem.user.status == "active":
                mail.send(mem.user.email, f"{email} was added to {p.name}",
                          f"{actor or 'A ' + p.name + ' administrator'} added {email} to {p.name} for {t.name}.\n\n"
                          f"If this was not expected, open {get_settings().issuer}/company and remove them.\n")
    except Exception:  # noqa: BLE001
        pass


class Removal(BaseModel):
    tenant_id: str
    identity_id: str | None = None
    email: EmailStr | None = None
    actor: str = ""


@router.post("/api/v1/products/remove")
@router.post("/api/products/remove")
def remove(b: Removal, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    p = product_of(authorization, db, "identity:remove", b.tenant_id)
    t = db.get(m.Tenant, b.tenant_id)
    if not t:
        raise HTTPException(404, "Unknown company")
    user = db.get(m.User, b.identity_id) if b.identity_id else (db.execute(select(m.User).where(m.User.email == b.email.lower())).scalar_one_or_none() if b.email else None)
    if not user:
        raise HTTPException(404, "Unknown person")
    reg = db.execute(select(m.MemberProduct).where(m.MemberProduct.tenant_id == t.id, m.MemberProduct.user_id == user.id,
                                                   m.MemberProduct.product_key == p.key)).scalar_one_or_none()
    if reg is None or reg.status != "active":
        raise HTTPException(404, "This person is not registered in this product")
    reg.status = "removed"
    db.flush()
    left = service.registered_products(db, t.id, user.id)
    mem = db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id, m.TenantMember.user_id == user.id)).scalar_one_or_none()
    ended = False
    if not left and mem is not None and mem.status == "active" and not mem.is_admin:  # no product left: the company membership ends (never an administrator's)
        mem.status = "removed"
        ended = True
        _end_tenant_tokens(db, user.id, t.id)
        events.emit(db, "membership.removed", tenant=t, user=user, member=mem)
    service.audit(db, "member.unregistered", None, t.id, email=user.email, product=p.key, by=b.actor or p.key, membership_ended=ended)
    db.commit()
    return {"registered_products": left, "membership_ended": ended}


@router.get("/api/v1/products/seats")
@router.get("/api/products/seats")
def seats(tenant_id: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    p = product_of(authorization, db, "identity:seats", tenant_id)
    t, sub = _tenant_and_sub(db, tenant_id, p)
    return {"product": p.key, "seats": sub.seats, "used": service.seats_used(db, t.id, p.key)}
