"""Sprint 4: change events, company self-service, snapshot."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import events, models as m
from app.security import hash_password
from conftest import PW, login, make_world

SECRET = "webhook-secret-for-tests"


def prepare(db):
    """make_world plus: a webhook on Tracker, a company administrator for Alpha, and a platform administrator."""
    w = make_world(db)
    db.get(m.Product, "tracker").webhook_url = "https://tracker.example.com/api/auth/identity-events"
    db.get(m.Product, "tracker").webhook_secret = SECRET
    boss = m.User(email="boss@example.com", name="Boss", password_hash=hash_password(PW))
    root = m.User(email="root@example.com", name="Root", password_hash=hash_password(PW), is_platform_admin=True)
    db.add_all([boss, root])
    db.flush()
    db.add(m.TenantMember(tenant_id=w["alpha"].id, user_id=boss.id, is_admin=True))
    db.commit()
    return {**w, "boss": boss, "root": root}


def csrf(client):
    return {"x-csrf-token": client.cookies.get("id_csrf")}


def outbox(db, type=None):
    q = select(m.Outbox).order_by(m.Outbox.created_at)
    rows = db.execute(q).scalars().all()
    return [o for o in rows if type is None or o.type == type]


def test_company_admin_can_invite_and_event_is_queued(client, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    w = prepare(db)
    login(client, "boss@example.com")
    r = client.post(f"/company/api/{w['alpha'].id}/members", json={"email": "new@example.com", "name": "New Joiner"}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert any((tmp_path / "var" / "outbox").glob("*new_at_example.com.txt"))  # invitation e-mail
    ev = outbox(db, "membership.added")
    assert len(ev) == 1 and ev[0].product_key == "tracker" and ev[0].payload["user"]["email"] == "new@example.com"
    assert ev[0].payload["tenant"]["slug"] == "alpha"
    assert client.post(f"/company/api/{w['alpha'].id}/members", json={"email": "new@example.com", "name": "x"}, headers=csrf(client)).status_code == 409


def test_only_company_administrators_of_that_company(client, db):
    w = prepare(db)
    login(client, "solo@example.com")  # a normal employee
    assert client.get(f"/company/api/{w['beta'].id}/members").status_code == 403
    assert client.get("/company").status_code == 403
    client.cookies.clear()
    login(client, "boss@example.com")  # administers Alpha only
    assert client.get(f"/company/api/{w['alpha'].id}/members").status_code == 200
    assert client.get(f"/company/api/{w['beta'].id}/members").status_code == 403
    assert client.post(f"/company/api/{w['alpha'].id}/members", json={"email": "a@b.co", "name": "x"}).status_code == 403  # no CSRF header


def test_suspend_reactivate_remove_emit_events_and_end_tokens(client, db):
    w = prepare(db)
    multi = w["people"]["multi"]
    db.add(m.RefreshToken(token_hash="h1", family="f", client_id="tracker-web", user_id=multi.id, tenant_id=w["alpha"].id, session_id="s",
                          expires_at=m.now() + timedelta(days=1)))
    db.add(m.RefreshToken(token_hash="h2", family="g", client_id="tracker-web", user_id=multi.id, tenant_id=w["beta"].id, session_id="s",
                          expires_at=m.now() + timedelta(days=1)))
    db.commit()
    login(client, "boss@example.com")
    base = f"/company/api/{w['alpha'].id}/members/{multi.id}"
    assert client.post(base + "/status", json={"status": "suspended"}, headers=csrf(client)).json()["status"] == "suspended"
    assert [o.type for o in outbox(db)] == ["membership.suspended"]
    toks = {t.token_hash: t.revoked for t in db.execute(select(m.RefreshToken)).scalars()}
    assert toks == {"h1": True, "h2": False}  # only this company's tokens
    client.post(base + "/status", json={"status": "active"}, headers=csrf(client))
    client.post(base + "/remove", headers=csrf(client))
    assert [o.type for o in outbox(db)] == ["membership.suspended", "membership.reactivated", "membership.removed"]
    listed = client.get(f"/company/api/{w['alpha'].id}/members").json()["members"]
    assert multi.id not in [x["user_id"] for x in listed]


def test_last_administrator_cannot_be_removed(client, db):
    w = prepare(db)
    me = w["boss"]
    login(client, "boss@example.com")
    base = f"/company/api/{w['alpha'].id}/members/{me.id}"
    assert client.post(base + "/status", json={"status": "suspended"}, headers=csrf(client)).status_code == 409
    assert client.post(base + "/admin", json={"is_admin": False}, headers=csrf(client)).status_code == 409
    assert client.post(base + "/remove", headers=csrf(client)).status_code == 409


def test_platform_changes_emit_the_right_events(client, db):
    w = prepare(db)
    login(client, "root@example.com")
    h = csrf(client)
    client.post(f"/admin/api/tenants/{w['alpha'].id}/subscriptions", json={"product": "tracker", "status": "cancelled"}, headers=h)
    client.post(f"/admin/api/tenants/{w['alpha'].id}/status", json={"status": "suspended"}, headers=h)
    uid = w["people"]["multi"].id
    client.post(f"/admin/api/users/{uid}/status", json={"status": "suspended"}, headers=h)
    types = [o.type for o in outbox(db)]
    assert types == ["subscription.changed", "tenant.status_changed", "user.suspended"]
    assert outbox(db, "subscription.changed")[0].payload["subscription"] == {"product": "tracker", "status": "cancelled", "valid_until": None}
    # suspending a person signs them out everywhere
    u = db.get(m.User, uid)
    db.refresh(u)
    assert u.token_epoch == 1 and u.status == "suspended"


def test_tenant_events_only_go_to_products_the_company_has(client, db):
    w = prepare(db)
    db.get(m.Product, "dms").webhook_url = "https://dms.example.com/hook"
    db.get(m.Product, "dms").webhook_secret = "s2"
    db.commit()
    login(client, "root@example.com")
    # Beta subscribes to Tracker only
    client.post(f"/admin/api/tenants/{w['beta'].id}/status", json={"status": "suspended"}, headers=csrf(client))
    assert {o.product_key for o in outbox(db, "tenant.status_changed")} == {"tracker"}
    # Alpha has both
    client.post(f"/admin/api/tenants/{w['alpha'].id}/status", json={"status": "suspended"}, headers=csrf(client))
    assert {o.product_key for o in outbox(db, "tenant.status_changed")} == {"tracker", "dms"}
    assert len({o.event_id for o in outbox(db, "tenant.status_changed")}) == 2  # one event id per event, shared by products


def test_delivery_signs_marks_done_and_retries_with_backoff(db):
    prepare(db)
    sent = []
    events.emit(db, "user.revoked", user=db.query(m.User).first())
    db.commit()
    assert events.deliver_pending(db, send=lambda url, secret, body: sent.append((url, secret, body))) == 1
    assert sent[0][0].endswith("/identity-events") and '"user.revoked"' in sent[0][2]
    assert outbox(db)[0].delivered_at is not None
    # signature a receiver can check
    sig = events.sign(SECRET, 1700000000, "{}")
    import hashlib, hmac
    assert sig == hmac.new(SECRET.encode(), b"1700000000.{}", hashlib.sha256).hexdigest()

    events.emit(db, "user.revoked", user=db.query(m.User).first())
    db.commit()

    def down(*a):
        raise OSError("connection refused")

    assert events.deliver_pending(db, send=down) == 0
    o = outbox(db)[-1]
    assert o.attempts == 1 and "refused" in o.last_error and o.delivered_at is None and not o.failed
    assert m.aware(o.next_at) > m.now()  # backed off
    assert events.deliver_pending(db, send=down) == 0 and outbox(db)[-1].attempts == 1  # not due yet
    for _ in range(events.MAX_ATTEMPTS):
        o.next_at = m.now() - timedelta(seconds=1)
        db.commit()
        events.deliver_pending(db, send=down)
    assert outbox(db)[-1].failed is True


def test_snapshot_needs_the_products_secret(client, db):
    w = prepare(db)
    assert client.get("/api/sync/snapshot", params={"product": "tracker"}).status_code == 401
    assert client.get("/api/sync/snapshot", params={"product": "tracker"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/sync/snapshot", params={"product": "dms"}, headers={"Authorization": f"Bearer {SECRET}"}).status_code == 401  # no secret set for dms
    r = client.get("/api/sync/snapshot", params={"product": "tracker"}, headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code == 200
    by_slug = {t["slug"]: t for t in r.json()["tenants"]}
    assert set(by_slug) == {"alpha", "beta"} and by_slug["alpha"]["subscription_status"] == "active"
    emails = {x["email"] for x in by_slug["alpha"]["members"]}
    assert {"multi@example.com", "boss@example.com"} <= emails


def test_company_page_renders_for_administrator(client, db):
    prepare(db)
    login(client, "boss@example.com")
    r = client.get("/company")
    assert r.status_code == 200 and "Invite someone" in r.text and "Alpha Works" in r.text
