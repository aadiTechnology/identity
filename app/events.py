"""Change events: what happened in Identity that a product must react to.

Events are written to an outbox in the same transaction as the change, so an event exists if and only if the change was saved.
A background worker then delivers them, signed (HMAC-SHA256), to each product that has a webhook, retrying with back-off.
Products must treat events as idempotent (the event id is stable) and reconcile from the snapshot to repair anything missed.
"""
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m

MAX_ATTEMPTS = 8
BACKOFF_SECONDS = 30  # doubled after each failure
TOLERANCE_SECONDS = 300  # receivers reject events older than this


def sign(secret: str, timestamp: int, body: str) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()


def tenant_dict(t: m.Tenant) -> dict:
    return {"id": t.id, "slug": t.slug, "name": t.name, "status": t.status}


def user_dict(u: m.User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "status": u.status}


def emit(db: Session, type: str, tenant: m.Tenant | None = None, user: m.User | None = None, member: m.TenantMember | None = None,
         subscription: m.Subscription | None = None, only_product: str | None = None) -> str:
    """Queue an event for every product that should hear about it. Call before db.commit()."""
    products = db.execute(select(m.Product).where(m.Product.webhook_url != "", m.Product.active.is_(True))).scalars().all()
    subscribed: set[str] = set()
    if tenant is not None:
        subscribed = {s.product_key for s in db.execute(select(m.Subscription).where(m.Subscription.tenant_id == tenant.id)).scalars()}
    event_id = m.new_id()
    payload: dict = {"id": event_id, "type": type, "at": m.now().isoformat()}
    if tenant is not None:
        payload["tenant"] = tenant_dict(tenant)
    if user is not None:
        payload["user"] = user_dict(user)
    if member is not None:
        payload["membership"] = {"status": member.status, "is_admin": member.is_admin}
    if subscription is not None:
        payload["subscription"] = {"product": subscription.product_key, "status": subscription.status,
                                   "valid_until": subscription.valid_until.isoformat() if subscription.valid_until else None}
        if subscription.plan_key:  # only when a plan is assigned, so events for plan-less subscriptions are exactly what they always were
            plan = db.get(m.Plan, subscription.plan_key)
            if plan:
                from . import service
                v = service.plan_view(plan)
                payload["subscription"].update(plan=v["key"], features=v["features"], limits=v["limits"])
    for p in products:
        if only_product and p.key != only_product:
            continue
        # tenant events go to the products the company has (or had); user-wide events go to every product
        if tenant is not None and p.key not in subscribed:
            continue
        db.add(m.Outbox(event_id=event_id, product_key=p.key, type=type, payload=payload))
    return event_id


def http_send(url: str, secret: str, body: str) -> None:
    ts = int(time.time())
    req = urllib.request.Request(url, data=body.encode(), headers={"Content-Type": "application/json",
                                 "X-Aadi-Signature": f"t={ts},v1={sign(secret, ts, body)}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        if r.status >= 300:
            raise urllib.error.HTTPError(url, r.status, "bad status", None, None)


def deliver_pending(db: Session, send=http_send, limit: int = 50) -> int:
    """Deliver due events once. Returns how many were delivered."""
    now = m.now()
    rows = db.execute(select(m.Outbox).where(m.Outbox.delivered_at.is_(None), m.Outbox.failed.is_(False), m.Outbox.next_at <= now)
                      .order_by(m.Outbox.created_at).limit(limit)).scalars().all()
    done = 0
    for o in rows:
        p = db.get(m.Product, o.product_key)
        if not p or not p.webhook_url:
            o.failed, o.last_error = True, "product has no webhook"
            continue
        try:
            send(p.webhook_url, p.webhook_secret, json.dumps(o.payload, separators=(",", ":")))
            o.delivered_at = m.now()
            done += 1
        except Exception as e:  # network error, non-2xx, timeout
            o.attempts += 1
            o.last_error = str(e)[:300]
            if o.attempts >= MAX_ATTEMPTS:
                o.failed = True
            else:
                o.next_at = m.now() + timedelta(seconds=BACKOFF_SECONDS * 2 ** (o.attempts - 1))
    db.commit()
    return done


_stop = threading.Event()


def start_worker(session_factory, interval: float = 5.0) -> threading.Thread:
    def loop():
        last_prune = 0.0
        while not _stop.wait(interval):
            try:
                with session_factory() as db:
                    deliver_pending(db)
                    if time.time() - last_prune > 86400:  # housekeeping once a day
                        from .maintenance import prune
                        prune(db)
                        last_prune = time.time()
            except Exception:  # never let the worker die
                pass
    t = threading.Thread(target=loop, name="event-worker", daemon=True)
    t.start()
    return t


def stop_worker():
    _stop.set()
