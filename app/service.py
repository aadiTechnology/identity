"""Business rules: who may reach which product, sessions, audit."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models as m
from .config import get_settings
from .security import hash_token, new_token


@dataclass
class Pair:
    tenant_id: str
    tenant_name: str
    tenant_slug: str
    product_key: str
    product_name: str
    description: str
    initiate_url: str


def audit(db: Session, action: str, actor_id: str | None = None, tenant_id: str | None = None, **detail):
    db.add(m.AuditLog(actor_id=actor_id, tenant_id=tenant_id, action=action, detail=detail))


def subscription_live(s: m.Subscription) -> bool:
    if s.status not in ("active", "trial"):
        return False
    until = m.aware(s.valid_until)
    return until is None or until > m.now()


def eligible_pairs(db: Session, user: m.User) -> list[Pair]:
    """Every (company, product) this person may open: active membership, active company, live subscription, active product."""
    rows = db.execute(
        select(m.TenantMember, m.Tenant).join(m.Tenant, m.Tenant.id == m.TenantMember.tenant_id)
        .where(m.TenantMember.user_id == user.id, m.TenantMember.status == "active", m.Tenant.status.in_(["active", "trial"]))
        .order_by(m.Tenant.name)
    ).all()
    out: list[Pair] = []
    for _, t in rows:
        for s in sorted(t.subscriptions, key=lambda x: x.product_key):
            p = db.get(m.Product, s.product_key)
            if p and p.active and subscription_live(s):
                out.append(Pair(t.id, t.name, t.slug, p.key, p.name, p.description, p.initiate_url))
    return out


def next_step(pairs: list[Pair]) -> str:
    """none -> no subscription page; one -> straight into the product; many -> chooser."""
    return "none" if not pairs else "direct" if len(pairs) == 1 else "choose"


def product_entry_url(pair: Pair) -> str:
    sep = "&" if "?" in pair.initiate_url else "?"
    return f"{pair.initiate_url}{sep}{urlencode({'tenant': pair.tenant_id})}"


# ---- login sessions ----
def create_session(db: Session, user: m.User) -> tuple[str, m.LoginSession]:
    tok, h = new_token()
    s = m.LoginSession(user_id=user.id, token_hash=h, epoch=user.token_epoch, csrf=new_token()[0],
                       expires_at=m.now() + timedelta(hours=get_settings().session_hours))
    db.add(s)
    db.flush()
    return tok, s


def load_session(db: Session, cookie: str | None) -> tuple[m.LoginSession, m.User] | None:
    if not cookie:
        return None
    s = db.execute(select(m.LoginSession).where(m.LoginSession.token_hash == hash_token(cookie))).scalar_one_or_none()
    if not s or s.revoked or m.aware(s.expires_at) < m.now():
        return None
    u = db.get(m.User, s.user_id)
    if not u or u.status != "active" or u.token_epoch != s.epoch:
        return None
    return s, u


def revoke_user_everywhere(db: Session, user: m.User):
    """Sign out of every product: sessions, refresh tokens and (via epoch) outstanding access tokens."""
    user.token_epoch += 1
    for s in db.execute(select(m.LoginSession).where(m.LoginSession.user_id == user.id)).scalars():
        s.revoked = True
    for r in db.execute(select(m.RefreshToken).where(m.RefreshToken.user_id == user.id)).scalars():
        r.revoked = True


# ---- which products a person is registered in ----
def register(db: Session, tenant_id: str, user_id: str, product_key: str, source: str, by: str = "") -> m.MemberProduct:
    """Register a person in a product of a company (or make a removed registration active again)."""
    r = db.execute(select(m.MemberProduct).where(m.MemberProduct.tenant_id == tenant_id, m.MemberProduct.user_id == user_id,
                                                 m.MemberProduct.product_key == product_key)).scalar_one_or_none()
    if r is None:
        r = m.MemberProduct(tenant_id=tenant_id, user_id=user_id, product_key=product_key, status="active", source=source, created_by=by)
        db.add(r)
    else:
        r.status = "active"
    db.flush()
    return r


def register_all_subscribed(db: Session, tenant: m.Tenant, user_id: str, source: str, by: str = "") -> list[str]:
    """Register in every product the company has live (what the company page and a new company always did)."""
    keys = [s.product_key for s in tenant.subscriptions if subscription_live(s)]
    for k in keys:
        register(db, tenant.id, user_id, k, source, by)
    return keys


def registered_products(db: Session, tenant_id: str, user_id: str) -> list[str]:
    return sorted(db.scalars(select(m.MemberProduct.product_key).where(m.MemberProduct.tenant_id == tenant_id, m.MemberProduct.user_id == user_id,
                                                                       m.MemberProduct.status == "active")))


def seats_used(db: Session, tenant_id: str, product_key: str) -> int:
    """Active members of the company registered in the product (suspended or removed people do not use a seat)."""
    return db.scalar(select(func.count()).select_from(m.MemberProduct).join(
        m.TenantMember, (m.TenantMember.tenant_id == m.MemberProduct.tenant_id) & (m.TenantMember.user_id == m.MemberProduct.user_id)
    ).where(m.MemberProduct.tenant_id == tenant_id, m.MemberProduct.product_key == product_key, m.MemberProduct.status == "active",
            m.TenantMember.status == "active")) or 0


def visible_pairs(db: Session, user: m.User) -> list[Pair]:
    """What the switcher and hub show: the products the person is registered in, among the ones they may open.
    If they are registered in none of them (an older account), show everything they may open rather than nothing."""
    allowed = eligible_pairs(db, user)
    reg = {(r.tenant_id, r.product_key) for r in db.scalars(select(m.MemberProduct).where(m.MemberProduct.user_id == user.id, m.MemberProduct.status == "active"))}
    shown = [p for p in allowed if (p.tenant_id, p.product_key) in reg]
    return shown or allowed
