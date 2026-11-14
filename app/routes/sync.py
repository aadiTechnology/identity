"""Snapshot of who may use a product, for the product's nightly reconcile. Authenticated with an Identity service token (scope identity:snapshot; the token's product must be the one asked about) or, for now, the product's shared secret."""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db
from .products import _bearer, token_principal

router = APIRouter()


@router.get("/api/v1/sync/snapshot")
@router.get("/api/sync/snapshot")
def snapshot(product: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    p = db.get(m.Product, product)
    token = _bearer(authorization)
    if token.count(".") == 2:
        caller = token_principal(db, token, "identity:snapshot", None)
        if not p or caller.key != p.key:
            raise HTTPException(403, "The token belongs to a different product")
    elif not p or not p.webhook_secret or not secrets.compare_digest(token, p.webhook_secret):
        raise HTTPException(401, "Invalid product credentials")
    subs = db.execute(select(m.Subscription).where(m.Subscription.product_key == product)).scalars().all()
    tenants = []
    for s in subs:
        t = s.tenant
        members = []
        for mem in t.members:
            u = mem.user
            members.append({"user_id": u.id, "email": u.email, "name": u.name, "user_status": u.status,
                            "membership_status": mem.status, "is_admin": mem.is_admin})
        tenants.append({"id": t.id, "slug": t.slug, "name": t.name, "status": t.status, "subscription_status": s.status,
                        "valid_until": s.valid_until.isoformat() if s.valid_until else None, "members": members})
    return {"product": product, "at": m.now().isoformat(), "tenants": tenants}
