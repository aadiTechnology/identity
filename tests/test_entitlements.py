"""P2-S5: Entitlements v1. Plans, features and limits live in Identity; products read them. A subscription with no plan behaves exactly as before."""
from datetime import datetime, timedelta, timezone

import pytest

from app import mail, security
from app import models as m
from app.security import hash_password, hash_token
from tests.conftest import login, make_world

CLIENT_SECRET = "svc-secret"
OLD_SECRET = "tracker-product-secret"


@pytest.fixture()
def world(db):
    w = make_world(db)
    db.get(m.Product, "tracker").webhook_secret = OLD_SECRET
    db.add(m.ServiceClient(client_id="tracker-identity", name="Tracker", product_key="tracker", secret_hash=hash_token(CLIENT_SECRET), audiences=["identity"],
                           scopes=["identity:entitlements", "identity:seats", "identity:enrol"]))
    db.add(m.User(email="root@example.com", name="Root", password_hash=hash_password("Correct-Horse-9"), is_platform_admin=True))
    db.commit()
    return w


@pytest.fixture(autouse=True)
def _quiet_mail(monkeypatch):
    monkeypatch.setattr(mail, "send", lambda *a, **k: None)


def admin(client):
    login(client, "root@example.com")
    return {"X-CSRF-Token": client.cookies.get("id_csrf") or ""}


def token(client, tenant, scope="identity:entitlements"):
    r = client.post("/token", data={"grant_type": "client_credentials", "client_id": "tracker-identity", "client_secret": CLIENT_SECRET, "audience": "identity",
                                    "scope": scope, "tenant_id": tenant.id})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def ent(client, tenant, headers):
    return client.get(f"/api/v1/entitlements?tenant_id={tenant.id}", headers=headers)


def make_plan(client, h, key="tracker-pro", **over):
    body = {"key": key, "product_key": "tracker", "name": "Tracker Pro", "features": ["costing", "finance.invoices"], "limits": {"projects": 50, "storage_gb": None}, **over}
    return client.post("/admin/api/plans", json=body, headers=h)


def outbox(db, type_="subscription.changed"):
    db.expire_all()
    return db.query(m.Outbox).filter_by(type=type_).order_by(m.Outbox.created_at).all()


# ---- the answer products read --------------------------------------------------------------------------------------
def test_a_subscription_without_a_plan_is_entitled_with_no_packaging(client, world):
    r = ent(client, world["alpha"], token(client, world["alpha"]))
    assert r.status_code == 200
    assert r.json() == {"tenant_id": world["alpha"].id, "product": "tracker", "entitled": True, "status": "active", "valid_until": None, "seats": None,
                        "seats_used": 0, "plan": None, "features": [], "limits": {}}


def test_a_plan_shows_its_features_and_limits(client, db, world):
    h = admin(client)
    assert make_plan(client, h).status_code == 200
    assert client.post(f"/admin/api/tenants/{world['alpha'].id}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h).status_code == 200
    out = ent(client, world["alpha"], token(client, world["alpha"])).json()
    assert out["plan"] == {"key": "tracker-pro", "name": "Tracker Pro"} and out["features"] == ["costing", "finance.invoices"]
    assert out["limits"] == {"projects": 50, "storage_gb": None}  # null = unlimited


def test_a_product_only_learns_about_its_own_entitlement(client, db, world):
    # alpha also has dms; the tracker client asks and gets tracker, never dms
    out = ent(client, world["alpha"], token(client, world["alpha"])).json()
    assert out["product"] == "tracker"


def test_not_live_means_not_entitled_with_nothing_attached(client, db, world):
    h = admin(client)
    make_plan(client, h)
    client.post(f"/admin/api/tenants/{world['alpha'].id}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h)
    tok = token(client, world["alpha"])
    sub = db.query(m.Subscription).filter_by(tenant_id=world["alpha"].id, product_key="tracker").one()
    sub.status = "cancelled"
    db.commit()
    out = ent(client, world["alpha"], tok).json()
    assert out["entitled"] is False and out["plan"] is None and out["features"] == [] and out["limits"] == {} and out["status"] == "cancelled"
    sub.status, sub.valid_until = "active", datetime.now(timezone.utc) - timedelta(days=1)  # expired
    db.commit()
    assert ent(client, world["alpha"], tok).json()["entitled"] is False
    sub.valid_until = datetime.now(timezone.utc) + timedelta(days=30)
    db.commit()
    assert ent(client, world["alpha"], tok).json()["entitled"] is True


def test_a_suspended_company_is_not_entitled(client, db, world):
    tok = token(client, world["alpha"])
    t = db.get(m.Tenant, world["alpha"].id)
    t.status = "suspended"
    db.commit()
    assert ent(client, world["alpha"], tok).json()["entitled"] is False


def test_an_inactive_plan_is_ignored(client, db, world):
    h = admin(client)
    make_plan(client, h)
    client.post(f"/admin/api/tenants/{world['alpha'].id}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h)
    make_plan(client, h, active=False)
    assert ent(client, world["alpha"], token(client, world["alpha"])).json()["plan"] is None


def test_seats_are_reported(client, db, world):
    db.query(m.Subscription).filter_by(tenant_id=world["alpha"].id, product_key="tracker").one().seats = 5
    db.commit()
    out = ent(client, world["alpha"], token(client, world["alpha"])).json()
    assert out["seats"] == 5 and out["seats_used"] == 0


# ---- who may ask ---------------------------------------------------------------------------------------------------
def test_company_a_token_cannot_read_company_b_and_the_scope_is_required(client, world):
    tok = token(client, world["alpha"])
    assert ent(client, world["beta"], tok).status_code == 403
    assert ent(client, world["alpha"], token(client, world["alpha"], "identity:seats")).status_code == 403
    assert ent(client, world["alpha"], {}).status_code == 401
    assert ent(client, world["alpha"], {"Authorization": "Bearer nope"}).status_code == 401


def test_the_shared_secret_also_works_for_now_and_unknown_company_is_404(client, world):
    assert ent(client, world["alpha"], {"Authorization": f"Bearer {OLD_SECRET}"}).status_code == 200
    r = client.get("/api/v1/entitlements?tenant_id=nope", headers={"Authorization": f"Bearer {OLD_SECRET}"})
    assert r.status_code == 404


# ---- administration ------------------------------------------------------------------------------------------------
def test_plan_validation(client, world):
    h = admin(client)
    for bad in ({"key": "X"}, {"key": "ab"}, {"key": "Has Space"}, {"product_key": "nope"}, {"name": " "}, {"features": ["Bad Feature"]},
                {"limits": {"Bad Limit": 1}}, {"limits": {"projects": -1}}):
        r = make_plan(client, h, **{"key": "new-plan", **bad})
        assert r.status_code == 422, bad
    assert client.get("/admin/api/plans").json() == {"plans": []}
    # a plan key cannot be moved to another product
    make_plan(client, h)
    assert make_plan(client, h, product_key="dms").status_code == 409


def test_only_a_platform_admin_with_csrf_may_manage_plans(client, world):
    assert client.get("/admin/api/plans").status_code == 403
    login(client, "solo@example.com")
    assert client.get("/admin/api/plans").status_code == 403
    login(client, "root@example.com")
    assert client.post("/admin/api/plans", json={"key": "abc", "product_key": "tracker", "name": "x"}).status_code == 403  # no CSRF header


def test_assigning_a_plan_has_rules(client, db, world):
    h = admin(client)
    make_plan(client, h)
    a, g = world["alpha"].id, db.query(m.Tenant).filter_by(slug="gamma").one().id
    assert client.post(f"/admin/api/tenants/{a}/plan", json={"product": "dms", "plan": "tracker-pro"}, headers=h).status_code == 422  # another product's plan
    assert client.post(f"/admin/api/tenants/{a}/plan", json={"product": "tracker", "plan": "no-such"}, headers=h).status_code == 422
    assert client.post(f"/admin/api/tenants/{g}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h).status_code == 404  # gamma has no subscription
    assert client.post(f"/admin/api/tenants/{a}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h).status_code == 200
    assert client.post(f"/admin/api/tenants/{a}/plan", json={"product": "tracker", "plan": None}, headers=h).status_code == 200  # back to no plan
    assert ent(client, world["alpha"], token(client, world["alpha"])).json()["plan"] is None
    assert db.query(m.AuditLog).filter_by(action="subscription.plan_changed").count() == 2


def test_the_overview_shows_the_plan_of_each_subscription(client, world):
    h = admin(client)
    make_plan(client, h)
    client.post(f"/admin/api/tenants/{world['alpha'].id}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h)
    subs = {t["name"]: {s["product"]: s["plan"] for s in t["subscriptions"]} for t in client.get("/admin/api/overview").json()["tenants"]}
    assert subs["Alpha Works"] == {"tracker": "tracker-pro", "dms": None} and subs["Beta Builders"] == {"tracker": None}


# ---- events: products hear about changes -----------------------------------------------------------------------------
def test_events_carry_the_plan_only_when_one_is_assigned(client, db, world):
    for p in ("tracker", "dms"):
        db.get(m.Product, p).webhook_url = f"https://{p}.example.com/hook"
    db.commit()
    h = admin(client)
    client.post(f"/admin/api/tenants/{world['beta'].id}/subscriptions", json={"product": "tracker", "status": "active"}, headers=h)
    plain = outbox(db)[-1].payload["subscription"]
    assert plain == {"product": "tracker", "status": "active", "valid_until": None}  # exactly what it always was
    make_plan(client, h)
    client.post(f"/admin/api/tenants/{world['beta'].id}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h)
    withplan = outbox(db)[-1].payload["subscription"]
    assert withplan["plan"] == "tracker-pro" and withplan["features"] == ["costing", "finance.invoices"] and withplan["limits"] == {"projects": 50, "storage_gb": None}
    assert outbox(db)[-1].product_key == "tracker"


def test_changing_a_plan_notifies_every_company_on_it(client, db, world):
    db.get(m.Product, "tracker").webhook_url = "https://tracker.example.com/hook"
    db.commit()
    h = admin(client)
    make_plan(client, h)
    for t in ("alpha", "beta"):
        client.post(f"/admin/api/tenants/{world[t].id}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h)
    before = len(outbox(db))
    make_plan(client, h, features=["costing", "finance.invoices", "insights"])
    new = outbox(db)[before:]
    assert len(new) == 2 and all("insights" in o.payload["subscription"]["features"] for o in new)
    assert {o.payload["tenant"]["id"] for o in new} == {world["alpha"].id, world["beta"].id}


def test_the_admin_page_offers_plans(client, world):
    admin(client)
    html = client.get("/admin").text
    assert 'id="planform"' in html and "admin-plans.js" in html
    assert client.get("/static/admin-plans.js").status_code == 200


def test_editing_a_plan_changes_features_and_limits_in_place(client, db, world):
    h = admin(client)
    make_plan(client, h)
    client.post(f"/admin/api/tenants/{world['alpha'].id}/plan", json={"product": "tracker", "plan": "tracker-pro"}, headers=h)
    tok = token(client, world["alpha"])
    # keep one feature, drop one, add one; change one limit, drop one, add one
    r = make_plan(client, h, features=["costing", "insights"], limits={"projects": 80, "users": 10})
    assert r.status_code == 200
    out = ent(client, world["alpha"], tok).json()
    assert out["features"] == ["costing", "insights"] and out["limits"] == {"projects": 80, "users": 10}
    assert db.query(m.PlanFeature).filter_by(plan_key="tracker-pro").count() == 2 and db.query(m.PlanLimit).filter_by(plan_key="tracker-pro").count() == 2
    # saving the same plan again changes nothing and does not fail
    assert make_plan(client, h, features=["costing", "insights"], limits={"projects": 80, "users": 10}).status_code == 200
    # emptying it is allowed
    assert make_plan(client, h, features=[], limits={}).status_code == 200
    assert ent(client, world["alpha"], tok).json()["features"] == []
