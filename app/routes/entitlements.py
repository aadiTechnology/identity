"""What a company may use of the calling product: plan, features, limits, seats. Products read this (and cache it a few minutes) instead of keeping a plan of their own."""
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .. import models as m, service
from ..db import get_db
from .products import product_of

router = APIRouter()


@router.get("/api/v1/entitlements")
def get_entitlements(tenant_id: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Bearer: an Identity service token for audience identity with scope identity:entitlements for THIS company, or the product's shared secret (for now).
    A product only ever learns about its own entitlement."""
    p = product_of(authorization, db, "identity:entitlements", tenant_id)
    t = db.get(m.Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "Unknown company")
    return service.entitlements(db, t, p.key)
