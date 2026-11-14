"""P2-S3: Identity's own API accepts service tokens (audience identity) next to the old shared secret, under /api/v1 and the old paths."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from app import mail, security
from app import models as m
from app.security import hash_token
from tests.conftest import make_world

CLIENT_SECRET = "tracker-svc-secret"
OLD_SECRET = "tracker-product-secret"
ALL = ["identity:enrol", "identity:remove", "identity:seats", "identity:snapshot"]


@pytest.fixture()
def world(db):
    w = make_world(db)
    db.get(m.Product, "tracker").webhook_secret = OLD_SECRET
    db.add(m.ServiceClient(client_id="tracker-identity", name="Tracker to Identity", product_key="tracker", secret_hash=hash_token(CLIENT_SECRET),
                           audiences=["identity"], scopes=ALL))
    db.add(m.ServiceClient(client_id="narrow", name="Seats only", product_key="tracker", secret_hash=hash_token("n"), audiences=["identity"], scopes=["identity:seats"]))
    db.add(m.ServiceClient(client_id="no-identity", name="Video only", product_key="tracker", secret_hash=hash_token("v"), audiences=["video-engine"], scopes=["video:read"]))
    db.commit()
    return w


@pytest.fixture(autouse=True)
def _quiet_mail(monkeypatch):
    monkeypatch.setattr(mail, "send", lambda *a, **k: None)


def token(client, tenant=None, scope="identity:enrol", cid="tracker-identity", secret=CLIENT_SECRET, audience="identity"):
    data = {"grant_type": "client_credentials", "client_id": cid, "client_secret": secret, "audience": audience, "scope": scope}
    if tenant is not None:
        data["tenant_id"] = tenant.id
    return client.post("/token", data=data)


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def enrol(client, tenant, headers, path="/api/v1/products/enrol", email="new@example.com"):
    return client.post(path, json={"tenant_id": tenant.id, "email": email, "name": "New", "actor": "boss"}, headers=headers)


# ---- issuing tokens for audience identity ---------------------------------------------------------------------------
def test_issues_an_identity_token_for_one_company(client, db, world):
    r = token(client, world["alpha"], "identity:enrol identity:seats")
    assert r.status_code == 200 and r.json()["expires_in"] == 600
    t = r.json()["access_token"]
    assert jwt.get_unverified_header(t)["typ"] == "at+jwt"
    c = security.verify(db, t, audience="identity")
    assert (c["client_id"], c["caller_product"], c["tenant_id"], c["scope"]) == ("tracker-identity", "tracker", world["alpha"].id, "identity:enrol identity:seats")
    assert db.query(m.AuditLog).filter_by(action="service_token.issued").count() == 1


def test_snapshot_token_needs_no_company_but_no_other_scope_may_go_without(client, db, world):
    ok = token(client, None, "identity:snapshot")
    assert ok.status_code == 200
    assert "tenant_id" not in security.verify(db, ok.json()["access_token"], audience="identity")
    for scope in ("identity:enrol", "identity:snapshot identity:enrol", "identity:seats"):
        r = token(client, None, scope)
        assert r.status_code == 400 and r.json()["error"] == "invalid_request", scope


def test_scope_audience_and_company_rules(client, db, world):
    a = world["alpha"]
    assert token(client, a, "identity:remove", cid="narrow", secret="n").json()["error"] == "invalid_scope"  # client lacks that scope
    assert token(client, a, "identity:everything").json()["error"] == "invalid_scope"  # not an Identity scope
    assert token(client, a, "identity:enrol", cid="no-identity", secret="v").json()["error"] == "invalid_target"
    assert token(client, a, "identity:enrol", secret="wrong").status_code == 401
    assert token(client, a, "identity:enrol", audience="video-engine").json()["error"] == "invalid_target"
    db.add(m.Tenant(id="11111111-1111-1111-1111-111111111111", name="Delta", slug="delta"))
    db.commit()  # exists, but has no tracker subscription: a product cannot get a token for a company that does not use it
    r = client.post("/token", data={"grant_type": "client_credentials", "client_id": "tracker-identity", "client_secret": CLIENT_SECRET,
                                    "audience": "identity", "scope": "identity:enrol", "tenant_id": "11111111-1111-1111-1111-111111111111"})
    assert r.status_code == 400 and r.json()["error"] == "access_denied"
    assert db.query(m.AuditLog).filter_by(action="service_token.denied").count() == 1


# ---- using them ----------------------------------------------------------------------------------------------------
def test_enrol_with_a_service_token_works_on_both_paths(client, db, world):
    a = world["alpha"]
    t = token(client, a, "identity:enrol").json()["access_token"]
    r1 = enrol(client, a, bearer(t), "/api/v1/products/enrol", "one@example.com")
    r2 = enrol(client, a, bearer(t), "/api/products/enrol", "two@example.com")  # the old path keeps working
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["registered_products"] == ["tracker"] and r1.json()["created"] is True
    assert {u.email for u in db.query(m.User)} >= {"one@example.com", "two@example.com"}


def test_a_token_for_company_a_cannot_act_on_company_b(client, world):
    t = token(client, world["alpha"], "identity:enrol").json()["access_token"]
    r = enrol(client, world["beta"], bearer(t))
    assert r.status_code == 403 and "different company" in r.json()["detail"]


def test_scope_is_checked_per_endpoint(client, world):
    a = world["alpha"]
    t = token(client, a, "identity:seats").json()["access_token"]
    assert client.get(f"/api/v1/products/seats?tenant_id={a.id}", headers=bearer(t)).status_code == 200
    assert enrol(client, a, bearer(t)).status_code == 403
    assert client.post("/api/v1/products/remove", json={"tenant_id": a.id, "email": "x@example.com"}, headers=bearer(t)).status_code == 403
    assert client.get(f"/api/v1/products/seats?tenant_id={world['beta'].id}", headers=bearer(t)).status_code == 403


def test_remove_with_a_service_token(client, world):
    a = world["alpha"]
    t = token(client, a, "identity:enrol identity:remove").json()["access_token"]
    assert enrol(client, a, bearer(t), email="gone@example.com").status_code == 200
    r = client.post("/api/v1/products/remove", json={"tenant_id": a.id, "email": "gone@example.com"}, headers=bearer(t))
    assert r.status_code == 200 and r.json()["registered_products"] == []


def test_bad_tokens_are_refused(client, db, world):
    a = world["alpha"]
    good = token(client, a, "identity:enrol").json()["access_token"]
    assert enrol(client, a, bearer(good + "x")).status_code == 401  # tampered
    assert enrol(client, a, bearer("a.b.c")).status_code == 401  # garbage
    # a token for another audience (the video engine) is not accepted here
    other = security.sign(db, {"sub": "x", "client_id": "x", "aud": "video-engine", "scope": "identity:enrol", "caller_product": "tracker", "tenant_id": a.id}, 5, typ="at+jwt")
    assert enrol(client, a, bearer(other)).status_code == 401
    # an expired token
    expired = security.sign(db, {"sub": "x", "client_id": "x", "aud": "identity", "scope": "identity:enrol", "caller_product": "tracker", "tenant_id": a.id}, -5, typ="at+jwt")
    assert enrol(client, a, bearer(expired)).status_code == 401
    # a user token (no at+jwt type, audience is a product key) is never a service token
    user = security.sign(db, {"sub": "u", "aud": "identity", "scope": "identity:enrol", "caller_product": "tracker", "tenant_id": a.id}, 5)
    assert enrol(client, a, bearer(user)).status_code == 401
    # a token signed by some other key
    from cryptography.hazmat.primitives.asymmetric import rsa
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    forged = jwt.encode({"iss": "http://testserver", "aud": "identity", "scope": "identity:enrol", "caller_product": "tracker", "tenant_id": a.id, "exp": int(time.time()) + 60},
                        pem, algorithm="RS256", headers={"kid": db.query(m.SigningKey).first().kid, "typ": "at+jwt"})
    assert enrol(client, a, bearer(forged)).status_code == 401
    # alg none
    none = jwt.encode({"aud": "identity"}, key=None, algorithm="none")
    assert enrol(client, a, bearer(none)).status_code == 401


def test_the_token_stops_working_when_the_product_loses_the_company(client, db, world):
    a = world["alpha"]
    t = token(client, a, "identity:enrol").json()["access_token"]
    sub = db.query(m.Subscription).filter_by(tenant_id=a.id, product_key="tracker").one()
    sub.status = "cancelled"
    db.commit()
    assert enrol(client, a, bearer(t)).status_code == 403  # the handler re-checks the live subscription


def test_the_old_shared_secret_still_works_on_both_paths(client, world):
    a = world["alpha"]
    h = bearer(OLD_SECRET)
    assert enrol(client, a, h, "/api/products/enrol", "o1@example.com").status_code == 200
    assert enrol(client, a, h, "/api/v1/products/enrol", "o2@example.com").status_code == 200
    assert client.get(f"/api/v1/products/seats?tenant_id={a.id}", headers=h).status_code == 200
    assert client.get(f"/api/products/seats?tenant_id={a.id}", headers=h).status_code == 200
    assert client.post("/api/v1/products/enrol", json={"tenant_id": a.id, "email": "x@example.com"}, headers=bearer("wrong")).status_code == 401


# ---- snapshot ------------------------------------------------------------------------------------------------------
def test_snapshot_with_a_token_matches_the_secret_version(client, world):
    t = token(client, None, "identity:snapshot").json()["access_token"]
    via_token = client.get("/api/v1/sync/snapshot?product=tracker", headers=bearer(t))
    via_secret = client.get("/api/sync/snapshot?product=tracker", headers=bearer(OLD_SECRET))
    assert via_token.status_code == 200 and via_secret.status_code == 200
    a, b = via_token.json(), via_secret.json()
    a.pop("at"), b.pop("at")
    assert a == b and a["tenants"]


def test_snapshot_token_cannot_read_another_product(client, world):
    t = token(client, None, "identity:snapshot").json()["access_token"]
    assert client.get("/api/v1/sync/snapshot?product=dms", headers=bearer(t)).status_code == 403
    enrol_only = token(client, world["alpha"], "identity:enrol").json()["access_token"]
    assert client.get("/api/v1/sync/snapshot?product=tracker", headers=bearer(enrol_only)).status_code == 403


# ---- me/products alias, correlation id, admin ----------------------------------------------------------------------
def test_me_products_v1_equals_old_path(client, db, world):
    from tests.conftest import login
    login(client, "multi@example.com")
    assert client.get("/api/v1/me/products").json() == client.get("/api/me/products").json()


def test_correlation_id_is_returned_or_made(client):
    assert client.get("/health", headers={"X-Correlation-Id": "abc-123"}).headers["x-correlation-id"] == "abc-123"
    made = client.get("/health").headers["x-correlation-id"]
    assert len(made) == 32 and made != client.get("/health").headers["x-correlation-id"]
    assert len(client.get("/health", headers={"X-Correlation-Id": "x" * 500}).headers["x-correlation-id"]) == 64


def test_admin_can_create_a_client_for_the_identity_audience(client, db, world):
    from tests.conftest import login
    db.add(m.User(email="root@example.com", name="Root", password_hash=security.hash_password("Correct-Horse-9"), is_platform_admin=True))
    db.commit()
    login(client, "root@example.com")
    h = {"X-CSRF-Token": client.cookies.get("id_csrf") or ""}
    assert any(s["key"] == "identity" for s in client.get("/admin/api/service-clients").json()["services"])
    body = {"client_id": "crm-identity", "name": "CRM to Identity", "product_key": "tracker", "audiences": ["identity"], "scopes": ["identity:enrol"]}
    r = client.post("/admin/api/service-clients", json=body, headers=h)
    assert r.status_code == 200 and r.json()["secret"]
    bad = client.post("/admin/api/service-clients", json={**body, "client_id": "crm-identity-2", "scopes": ["identity:launch-missiles"]}, headers=h)
    assert bad.status_code == 422
    unknown = client.post("/admin/api/service-clients", json={**body, "client_id": "crm-identity-3", "audiences": ["nope"]}, headers=h)
    assert unknown.status_code == 422
