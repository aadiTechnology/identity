"""Business rules: who may reach which product, sessions, audit."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select
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
