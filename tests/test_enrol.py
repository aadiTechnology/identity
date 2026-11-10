"""K2: a product enrols its own people in Identity; a registry decides what the switcher shows; seats are counted per product."""
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app import mail, service
from app import models as m
from tests.conftest import login, make_world

SECRET = "tracker-product-secret"
AUTH = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture()
def world(db):
    w = make_world(db)
    db.get(m.Product, "tracker").webhook_secret = SECRET
    db.commit()
    return w


@pytest.fixture()
def sent(monkeypatch):
    box = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body: box.append((to, subject, body)))
    return box


def enrol(client, tenant, email="new@example.com", name="New Person", actor="boss@alpha.test", headers=AUTH):
    return client.post("/api/products/enrol", json={"tenant_id": tenant.id, "email": email, "name": name, "actor": actor}, headers=headers)


def rows(db, user_id=None):
    q = select(m.MemberProduct)
    if user_id:
        q = q.where(m.MemberProduct.user_id == user_id)
    return list(db.scalars(q))


# ---- who may call --------------------------------------------------------------------------------------------------
def test_only_a_product_with_the_right_secret_may_call(client, world):
    t = world["alpha"]
    assert enrol(client, t, headers={}).status_code == 401
    assert enrol(client, t, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert enrol(client, t, headers={"Authorization": "Bearer "}).status_code == 401
    assert client.get(f"/api/products/seats?tenant_id={t.id}", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_a_product_without_a_secret_cannot_call(client, world):
    # dms has no secret: an empty Bearer must not match it
    assert enrol(client, world["alpha"], headers={"Authorization": "Bearer "}).status_code == 401


# ---- enrolling ----------------------------------------------------------------------------------------------------
def test_enrol_creates_the_account_membership_and_registers_only_that_product(client, world, db, sent):
    t = world["alpha"]  # has tracker and dms
    r = enrol(client, t)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] and body["invited"] and body["user_status"] == "invited" and body["registered_products"] == ["tracker"]
    u = db.scalar(select(m.User).where(m.User.email == "new@example.com"))
    mem = db.scalar(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id, m.TenantMember.user_id == u.id))
    assert mem.status == "active" and mem.is_admin is False  # never a company administrator
    assert [r.product_key for r in rows(db, u.id)] == ["tracker"] and rows(db, u.id)[0].source == "product"
    assert any(to == "new@example.com" and "invited" in s.lower() for to, s, _ in sent)  # the invitation email (from Identity)
    assert db.scalar(select(m.AuditLog).where(m.AuditLog.action == "member.enrolled")) is not None


def test_enrolling_again_is_harmless_and_an_existing_person_is_not_recreated(client, world, db, sent):
    t = world["alpha"]
    enrol(client, t)
    r2 = enrol(client, t)
    assert r2.status_code == 200 and len(rows(db)) == 1
    solo = world["people"]["multi"]  # already a member of alpha, with a password
    r3 = enrol(client, t, email=solo.email)
    assert r3.status_code == 200 and not r3.json()["created"] and not r3.json()["invited"]
    assert db.scalar(select(m.User).where(m.User.email == solo.email)).password_hash  # untouched
    assert [r.product_key for r in rows(db, solo.id)] == ["tracker"]


def test_the_company_must_have_the_product_live(client, world, db):
    beta = world["beta"]  # tracker only
    gamma = db.scalar(select(m.Tenant).where(m.Tenant.slug == "gamma"))  # no subscription
    assert enrol(client, beta, email="a@example.com").status_code == 200
    assert enrol(client, gamma, email="b@example.com").status_code == 403
    sub = db.scalar(select(m.Subscription).where(m.Subscription.tenant_id == beta.id))
    sub.status = "suspended"
    db.commit()
    assert enrol(client, beta, email="c@example.com").status_code == 403
    sub.status, sub.valid_until = "active", datetime(2020, 1, 1, tzinfo=timezone.utc)
    db.commit()
    assert enrol(client, beta, email="d@example.com").status_code == 403  # ended
    assert client.post("/api/products/enrol", json={"tenant_id": str(uuid.uuid4()), "email": "e@example.com"}, headers=AUTH).status_code == 404
    assert db.scalar(select(m.User).where(m.User.email.in_(["b@example.com", "c@example.com", "d@example.com"]))) is None  # refused means nothing created


def test_a_suspended_company_cannot_enrol(client, world, db):
    t = world["alpha"]
    t.status = "suspended"
    db.commit()
    assert enrol(client, t).status_code == 403


def test_a_product_cannot_override_a_company_administrators_suspension(client, world, db):
    t = world["alpha"]
    enrol(client, t)
    u = db.scalar(select(m.User).where(m.User.email == "new@example.com"))
    mem = db.scalar(select(m.TenantMember).where(m.TenantMember.user_id == u.id))
    mem.status = "suspended"
    db.commit()
    assert enrol(client, t).status_code == 409
    u.status = "suspended"
    mem.status = "active"
    db.commit()
    assert enrol(client, t).status_code == 409


def test_a_removed_person_can_be_enrolled_again(client, world, db):
    t = world["alpha"]
    enrol(client, t)
    u = db.scalar(select(m.User).where(m.User.email == "new@example.com"))
    client.post("/api/products/remove", json={"tenant_id": t.id, "identity_id": u.id}, headers=AUTH)
    db.expire_all()
    assert db.scalar(select(m.TenantMember).where(m.TenantMember.user_id == u.id)).status == "removed"
    r = enrol(client, t)
    assert r.status_code == 200 and r.json()["membership_status"] == "active" and r.json()["registered_products"] == ["tracker"]


def test_company_administrators_are_told(client, world, db, sent):
    t = world["alpha"]
    boss = m.User(email="boss@alpha.test", name="Boss", password_hash="x", status="active")
    db.add(boss)
    db.flush()
    db.add(m.TenantMember(tenant_id=t.id, user_id=boss.id, is_admin=True))
    db.commit()
    enrol(client, t, email="fresh@example.com", actor="registrar@alpha.test")
    notice = [x for x in sent if x[0] == "boss@alpha.test"]
    assert len(notice) == 1 and "fresh@example.com" in notice[0][2] and "registrar@alpha.test" in notice[0][2]
    enrol(client, t, email="fresh@example.com")  # the same person again: no second notice
    assert len([x for x in sent if x[0] == "boss@alpha.test"]) == 1


# ---- seats ---------------------------------------------------------------------------------------------------------
def test_seats_are_counted_per_product(client, world, db):
    t = world["alpha"]
    db.scalar(select(m.Subscription).where(m.Subscription.tenant_id == t.id, m.Subscription.product_key == "tracker")).seats = 2
    db.commit()
    assert enrol(client, t, email="one@example.com").status_code == 200
    assert enrol(client, t, email="two@example.com").status_code == 200
    r = enrol(client, t, email="three@example.com")
    assert r.status_code == 409 and "2 seats" in r.json()["detail"]
    assert enrol(client, t, email="one@example.com").status_code == 200  # already has a seat
    assert client.get(f"/api/products/seats?tenant_id={t.id}", headers=AUTH).json() == {"product": "tracker", "seats": 2, "used": 2}
    one = db.scalar(select(m.User).where(m.User.email == "one@example.com"))
    db.scalar(select(m.TenantMember).where(m.TenantMember.user_id == one.id)).status = "suspended"
    db.commit()
    assert enrol(client, t, email="three@example.com").status_code == 200  # a suspended person frees the seat
    assert service.seats_used(db, t.id, "dms") == 0  # another product's seats are separate


def test_unlimited_seats_when_none_is_set(client, world, db):
    for i in range(5):
        assert enrol(client, world["alpha"], email=f"p{i}@example.com").status_code == 200
    assert client.get(f"/api/products/seats?tenant_id={world['alpha'].id}", headers=AUTH).json()["seats"] is None


# ---- removing ------------------------------------------------------------------------------------------------------
def test_removal_ends_the_membership_only_when_no_product_is_left(client, world, db):
    t = world["alpha"]
    enrol(client, t)
    u = db.scalar(select(m.User).where(m.User.email == "new@example.com"))
    service.register(db, t.id, u.id, "dms", "admin")  # also registered in a second product
    db.commit()
    r = client.post("/api/products/remove", json={"tenant_id": t.id, "email": "new@example.com"}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"registered_products": ["dms"], "membership_ended": False}
    assert db.scalar(select(m.TenantMember).where(m.TenantMember.user_id == u.id)).status == "active"
    assert client.post("/api/products/remove", json={"tenant_id": t.id, "email": "new@example.com"}, headers=AUTH).status_code == 404  # already gone
    service.register(db, t.id, u.id, "tracker", "product")
    db.commit()
    for key in ("tracker",):
        pass
    db.scalar(select(m.MemberProduct).where(m.MemberProduct.user_id == u.id, m.MemberProduct.product_key == "dms")).status = "removed"
    db.commit()
    r = client.post("/api/products/remove", json={"tenant_id": t.id, "identity_id": u.id}, headers=AUTH)
    assert r.json() == {"registered_products": [], "membership_ended": True}
    db.expire_all()
    assert db.scalar(select(m.TenantMember).where(m.TenantMember.user_id == u.id)).status == "removed"


def test_a_company_administrator_is_never_removed_by_a_product(client, world, db):
    t = world["alpha"]
    boss = m.User(email="boss@alpha.test", name="Boss", password_hash="x", status="active")
    db.add(boss)
    db.flush()
    db.add(m.TenantMember(tenant_id=t.id, user_id=boss.id, is_admin=True))
    service.register(db, t.id, boss.id, "tracker", "admin")
    db.commit()
    r = client.post("/api/products/remove", json={"tenant_id": t.id, "identity_id": boss.id}, headers=AUTH)
    assert r.json()["membership_ended"] is False
    db.expire_all()
    assert db.scalar(select(m.TenantMember).where(m.TenantMember.user_id == boss.id)).status == "active"


# ---- the switcher --------------------------------------------------------------------------------------------------
def test_the_switcher_shows_only_registered_products_but_never_nothing(client, world, db):
    multi = world["people"]["multi"]  # member of alpha (tracker, dms) and beta (tracker); registered nowhere (an older account)
    login(client, multi.email)
    assert {(i["tenant"], i["product"]) for i in client.get("/api/me/products").json()["items"]} == {("Alpha Works", "tracker"), ("Alpha Works", "dms"), ("Beta Builders", "tracker")}
    service.register(db, world["alpha"].id, multi.id, "dms", "product")
    db.commit()
    items = client.get("/api/me/products").json()["items"]
    assert {(i["tenant"], i["product"]) for i in items} == {("Alpha Works", "dms")}  # registered in one product: that is what is shown


def test_registration_does_not_change_who_may_sign_in(db, world):
    multi = world["people"]["multi"]
    service.register(db, world["alpha"].id, multi.id, "dms", "product")
    db.commit()
    assert {(p.tenant_name, p.product_key) for p in service.eligible_pairs(db, multi)} == {("Alpha Works", "tracker"), ("Alpha Works", "dms"), ("Beta Builders", "tracker")}
    assert {(p.tenant_name, p.product_key) for p in service.visible_pairs(db, multi)} == {("Alpha Works", "dms")}


def test_an_enrolled_person_lands_in_the_product_that_enrolled_them(client, world, db, sent):
    t = world["alpha"]
    enrol(client, t, email="staff@example.com")
    u = db.scalar(select(m.User).where(m.User.email == "staff@example.com"))
    u.status, u.password_hash = "active", __import__("app.security", fromlist=["hash_password"]).hash_password("Correct-Horse-9")
    db.commit()
    login(client, "staff@example.com")
    items = client.get("/api/me/products").json()
    assert [i["product"] for i in items["items"]] == ["tracker"] and items["next"] == "direct"  # one product: straight in


# ---- the older ways of adding people keep their behaviour ---------------------------------------------------------
def test_a_new_company_registers_its_owner_in_every_product(db):
    from app.routes import admin as adm  # noqa: F401
    make_world(db)
    t = db.scalar(select(m.Tenant).where(m.Tenant.slug == "alpha"))
    owner = db.scalar(select(m.User).where(m.User.email == "multi@example.com"))
    assert sorted(service.register_all_subscribed(db, t, owner.id, "admin")) == ["dms", "tracker"]
    assert sorted(service.registered_products(db, t.id, owner.id)) == ["dms", "tracker"]


def test_a_new_subscription_registers_the_companys_administrators(client, db):
    w = make_world(db)
    root = m.User(email="root@example.com", name="Root", password_hash=__import__("app.security", fromlist=["hash_password"]).hash_password("Correct-Horse-9"), is_platform_admin=True)
    db.add(root)
    boss = m.User(email="boss@example.com", name="Boss", password_hash="x")
    db.add(boss)
    db.flush()
    db.add(m.TenantMember(tenant_id=w["beta"].id, user_id=boss.id, is_admin=True))
    db.add(m.TenantMember(tenant_id=w["beta"].id, user_id=w["people"]["none"].id))
    db.commit()
    login(client, "root@example.com")
    r = client.post(f"/admin/api/tenants/{w['beta'].id}/subscriptions", json={"product": "dms", "status": "active"},
                    headers={"x-csrf-token": client.cookies.get("id_csrf")})
    assert r.status_code == 200, r.text
    assert [x.product_key for x in rows(db, boss.id)] == ["dms"]  # the administrator can reach it to become its first administrator
    assert rows(db, w["people"]["none"].id) == []  # an ordinary member is registered by the product, not automatically


# ---- the migration -------------------------------------------------------------------------------------------------
def test_the_migration_registers_every_active_member_in_every_product_their_company_has():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.db import Base
    eng = sa.create_engine("sqlite://")
    old = [t for name, t in Base.metadata.tables.items() if name != "member_products"]
    Base.metadata.create_all(eng, tables=old)
    with eng.begin() as c:
        for tid in ("T1", "T2"):
            c.execute(sa.text("INSERT INTO tenants (id, name, slug, status, created_at) VALUES (:i, :n, :s, 'active', :c)"), {"i": tid, "n": tid, "s": tid.lower(), "c": datetime.now(timezone.utc)})
        for key in ("tracker", "dms"):
            c.execute(sa.text("INSERT INTO products (key, name, description, initiate_url, active, webhook_url, webhook_secret) VALUES (:k, :k, '', '', 1, '', '')"), {"k": key})
        for uid in ("U1", "U2", "U3"):
            c.execute(sa.text("INSERT INTO users (id, email, name, password_hash, is_platform_admin, status, failed_logins, token_epoch, created_at) VALUES (:i, :e, :i, '', 0, 'active', 0, 0, :c)"),
                      {"i": uid, "e": uid.lower() + "@x.test", "c": datetime.now(timezone.utc)})
        for sid, t, p in (("S1", "T1", "tracker"), ("S2", "T1", "dms"), ("S3", "T2", "tracker")):
            c.execute(sa.text("INSERT INTO subscriptions (id, tenant_id, product_key, status) VALUES (:i, :t, :p, 'active')"), {"i": sid, "t": t, "p": p})
        for mid, t, u, st in (("M1", "T1", "U1", "active"), ("M2", "T2", "U1", "active"), ("M3", "T1", "U2", "suspended"), ("M4", "T2", "U3", "removed")):
            c.execute(sa.text("INSERT INTO tenant_members (id, tenant_id, user_id, is_admin, status, created_at) VALUES (:i, :t, :u, 0, :s, :c)"),
                      {"i": mid, "t": t, "u": u, "s": st, "c": datetime.now(timezone.utc)})
    spec = importlib.util.spec_from_file_location("mig0003", Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0003_member_products.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with eng.begin() as c:
        with Operations.context(MigrationContext.configure(c)):
            mod.upgrade()
    with eng.connect() as c:
        got = sorted(c.execute(sa.text("SELECT tenant_id, user_id, product_key, status, source FROM member_products")).all())
    assert got == [("T1", "U1", "dms", "active", "backfill"), ("T1", "U1", "tracker", "active", "backfill"), ("T2", "U1", "tracker", "active", "backfill")]  # not the suspended or removed
