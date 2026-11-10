"""Company self-service: a company administrator invites, suspends and removes their own people. Changes reach products as events."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events, models as m, service
from ..db import get_db
from ..web import CSRF_COOKIE, render
from .admin import invite
from .ui import current

router = APIRouter()


def administered(db: Session, user: m.User) -> list[m.Tenant]:
    if user.is_platform_admin:
        return db.execute(select(m.Tenant).order_by(m.Tenant.name)).scalars().all()
    rows = db.execute(select(m.Tenant).join(m.TenantMember, m.TenantMember.tenant_id == m.Tenant.id)
                      .where(m.TenantMember.user_id == user.id, m.TenantMember.is_admin.is_(True), m.TenantMember.status == "active")
                      .order_by(m.Tenant.name)).scalars().all()
    return rows


def company_admin(tid: str, request: Request, db: Session = Depends(get_db)):
    cur = current(request, db)
    if not cur:
        raise HTTPException(401, "Sign in")
    user = cur[1]
    tenant = next((t for t in administered(db, user) if t.id == tid), None)
    if not tenant:
        raise HTTPException(403, "You do not administer this company")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-csrf-token") != request.cookies.get(CSRF_COOKIE):
        raise HTTPException(403, "Missing CSRF token")
    return user, tenant


@router.get("/company")
def page(request: Request, tenant: str = "", db: Session = Depends(get_db)):
    cur = current(request, db)
    if not cur:
        return RedirectResponse("/login?next=/company", 303)
    mine = administered(db, cur[1])
    if not mine:
        return render(request, "message.html", {"title": "Not allowed", "text": "Only company administrators can manage people. Ask your administrator.", "link": "/", "link_text": "Back to my products"}, 403)
    chosen = next((t for t in mine if t.id == tenant), mine[0])
    return render(request, "company.html", {"user": cur[1], "tenants": mine, "tenant": chosen})


def member_dict(mem: m.TenantMember) -> dict:
    u = mem.user
    return {"user_id": u.id, "email": u.email, "name": u.name, "user_status": u.status, "status": mem.status, "is_admin": mem.is_admin}


@router.get("/company/api/{tid}/members")
def members(ctx=Depends(company_admin), db: Session = Depends(get_db)):
    _, t = ctx
    rows = db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id)).scalars().all()
    subs = [{"product": s.product_key, "status": s.status} for s in t.subscriptions]
    return {"tenant": {"id": t.id, "name": t.name}, "subscriptions": subs,
            "members": sorted((member_dict(x) for x in rows if x.status != "removed"), key=lambda d: d["name"].lower())}


class Invite(BaseModel):
    email: EmailStr
    name: str


@router.post("/company/api/{tid}/members")
def invite_member(b: Invite, ctx=Depends(company_admin), db: Session = Depends(get_db)):
    actor, t = ctx
    email = b.email.lower()
    u = db.execute(select(m.User).where(m.User.email == email)).scalar_one_or_none()
    if not u:
        u = m.User(email=email, name=b.name.strip(), status="invited")
        db.add(u)
        db.flush()
        invite(db, u)
    mem = db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id, m.TenantMember.user_id == u.id)).scalar_one_or_none()
    if mem and mem.status == "active":
        raise HTTPException(409, "That person is already in this company")
    if mem:
        mem.status = "active"
    else:
        mem = m.TenantMember(tenant_id=t.id, user_id=u.id)
        db.add(mem)
    db.flush()
    service.register_all_subscribed(db, t, u.id, "company", actor.email)
    events.emit(db, "membership.added", tenant=t, user=u, member=mem)
    service.audit(db, "member.invited", actor.id, t.id, email=email)
    db.commit()
    return member_dict(mem)


def _others_admin(db: Session, t: m.Tenant, except_user: str) -> bool:
    return db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id, m.TenantMember.user_id != except_user,
                                                   m.TenantMember.is_admin.is_(True), m.TenantMember.status == "active")).first() is not None


def _target(db: Session, t: m.Tenant, uid: str) -> m.TenantMember:
    mem = db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id, m.TenantMember.user_id == uid)).scalar_one_or_none()
    if not mem or mem.status == "removed":
        raise HTTPException(404, "Not found")
    return mem


def _end_tenant_tokens(db: Session, uid: str, tid: str):
    """A suspended or removed person cannot refresh tokens for this company."""
    for r in db.execute(select(m.RefreshToken).where(m.RefreshToken.user_id == uid, m.RefreshToken.tenant_id == tid)).scalars():
        r.revoked = True


class MemberStatus(BaseModel):
    status: str  # active | suspended


@router.post("/company/api/{tid}/members/{uid}/status")
def set_status(uid: str, b: MemberStatus, ctx=Depends(company_admin), db: Session = Depends(get_db)):
    actor, t = ctx
    mem = _target(db, t, uid)
    if b.status not in ("active", "suspended"):
        raise HTTPException(422, "Bad status")
    if b.status == "suspended" and mem.is_admin and not _others_admin(db, t, uid):
        raise HTTPException(409, "A company needs at least one active administrator")
    mem.status = b.status
    if b.status == "suspended":
        _end_tenant_tokens(db, uid, t.id)
    events.emit(db, "membership.suspended" if b.status == "suspended" else "membership.reactivated", tenant=t, user=mem.user, member=mem)
    service.audit(db, "member.status", actor.id, t.id, user=uid, status=b.status)
    db.commit()
    return member_dict(mem)


class AdminFlag(BaseModel):
    is_admin: bool


@router.post("/company/api/{tid}/members/{uid}/admin")
def set_admin(uid: str, b: AdminFlag, ctx=Depends(company_admin), db: Session = Depends(get_db)):
    actor, t = ctx
    mem = _target(db, t, uid)
    if not b.is_admin and mem.is_admin and not _others_admin(db, t, uid):
        raise HTTPException(409, "A company needs at least one active administrator")
    mem.is_admin = b.is_admin
    service.audit(db, "member.admin", actor.id, t.id, user=uid, is_admin=b.is_admin)
    db.commit()
    return member_dict(mem)


@router.post("/company/api/{tid}/members/{uid}/remove")
def remove(uid: str, ctx=Depends(company_admin), db: Session = Depends(get_db)):
    actor, t = ctx
    mem = _target(db, t, uid)
    if mem.is_admin and mem.status == "active" and not _others_admin(db, t, uid):
        raise HTTPException(409, "A company needs at least one active administrator")
    mem.status = "removed"
    _end_tenant_tokens(db, uid, t.id)
    events.emit(db, "membership.removed", tenant=t, user=mem.user, member=mem)
    service.audit(db, "member.removed", actor.id, t.id, user=uid)
    db.commit()
    return {"ok": True}
