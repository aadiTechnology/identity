"""Service tokens: client_credentials for shared services (Video Creator work, sprint 2)."""
import base64

import jwt
import pytest

from app import models as m, service
from app.security import hash_password, hash_token, verify
from conftest import PW, login, make_world

SECRET = "svc-secret-1"
FORM = {"grant_type": "client_credentials", "client_id": "academic-video", "client_secret": SECRET, "audience": "video-engine",
        "scope": "video:create video:read"}


@pytest.fixture()
def world(db):
    w = make_world(db)
    db.add(m.Product(key="video-engine", name="Video Engine", kind="service"))
    db.flush()
    db.add(m.ServiceClient(client_id="academic-video", name="Academic video", product_key="tracker", secret_hash=hash_token(SECRET),
                           audiences=["video-engine"], scopes=["video:create", "video:read", "video:cancel"]))
    db.add(m.Subscription(tenant_id=w["alpha"].id, product_key="video-engine"))  # alpha: tracker + dms + video-engine; beta: tracker only
    db.commit()
    return w


def ask(client, w, tenant="alpha", **over):
    return client.post("/token", data={**FORM, "tenant_id": w[tenant].id, **over})


def test_issues_a_signed_token_for_one_company(client, db, world):
    r = ask(client, world)
    assert r.status_code == 200 and r.json()["expires_in"] == 600
    tok = r.json()["access_token"]
    assert jwt.get_unverified_header(tok)["typ"] == "at+jwt"
    c = verify(db, tok, audience="video-engine")
    assert (c["client_id"], c["sub"], c["tenant_id"], c["scope"]) == ("academic-video", "academic-video", world["alpha"].id, "video:create video:read")
    assert c["jti"] and c["iss"] == "http://testserver" and c["exp"] - c["iat"] == 600
    assert db.query(m.AuditLog).filter_by(action="service_token.issued").count() == 1


def test_basic_auth_works(client, world):
    basic = base64.b64encode(f"academic-video:{SECRET}".encode()).decode()
    r = client.post("/token", data={"grant_type": "client_credentials", "audience": "video-engine", "scope": "video:read", "tenant_id": world["alpha"].id},
                    headers={"Authorization": f"Basic {basic}"})
    assert r.status_code == 200


def test_wrong_secret_or_disabled_client_is_refused(client, db, world):
    assert ask(client, world, client_secret="nope").status_code == 401
    db.get(m.ServiceClient, "academic-video").active = False
    db.commit()
    assert ask(client, world).status_code == 401


def test_company_without_the_service_is_refused(client, db, world):
    r = ask(client, world, tenant="beta")
    assert r.status_code == 400 and r.json()["error"] == "access_denied"
    assert db.query(m.AuditLog).filter_by(action="service_token.denied").count() == 1


def test_company_not_subscribed_to_the_calling_product_is_refused(client, db, world):
    """Confused deputy: gamma has the service but not the calling product."""
    g = db.query(m.Tenant).filter_by(slug="gamma").one()
    db.add(m.Subscription(tenant_id=g.id, product_key="video-engine"))
    db.commit()
    r = client.post("/token", data={**FORM, "tenant_id": g.id})
    assert r.status_code == 400 and r.json()["error"] == "access_denied"


def test_suspended_subscription_is_refused(client, db, world):
    s = db.query(m.Subscription).filter_by(tenant_id=world["alpha"].id, product_key="video-engine").one()
    s.status = "suspended"
    db.commit()
    assert ask(client, world).json()["error"] == "access_denied"


@pytest.mark.parametrize("over,error", [({"scope": "video:admin"}, "invalid_scope"), ({"scope": ""}, "invalid_scope"),
                                        ({"audience": "tracker"}, "invalid_target"), ({"audience": "nothing"}, "invalid_target"),
                                        ({"tenant_id": "no-such"}, "invalid_request"), ({"tenant_id": ""}, "invalid_request")])
def test_bad_requests(client, world, over, error):
    r = ask(client, world, **over)
    assert r.status_code == 400 and r.json()["error"] == error


def test_service_token_is_not_a_user_token(client, world):
    tok = ask(client, world).json()["access_token"]
    assert client.get("/userinfo", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_service_products_never_appear_for_people(db, world):
    keys = {p.product_key for p in service.eligible_pairs(db, world["people"]["multi"])}
    assert "video-engine" not in keys and "tracker" in keys


def test_discovery_lists_the_grant(client):
    assert "client_credentials" in client.get("/.well-known/openid-configuration").json()["grant_types_supported"]


# ---- admin ----
def admin_session(client, db):
    db.add(m.User(email="boss@example.com", name="Boss", password_hash=hash_password(PW), is_platform_admin=True))
    db.commit()
    login(client, "boss@example.com")
    return {"x-csrf-token": client.cookies.get("id_csrf")}


def test_admin_creates_rotates_and_disables_a_client(client, db, world):
    h = admin_session(client, db)
    body = {"client_id": "tracker-video", "name": "Tracker video", "product_key": "tracker", "audiences": ["video-engine"], "scopes": ["video:read"]}
    r = client.post("/admin/api/service-clients", json=body, headers=h)
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert db.get(m.ServiceClient, "tracker-video").secret_hash == hash_token(secret)
    assert secret not in str(client.get("/admin/api/service-clients").json())
    form = {"grant_type": "client_credentials", "client_id": "tracker-video", "client_secret": secret, "audience": "video-engine",
            "scope": "video:read", "tenant_id": world["alpha"].id}
    assert client.post("/token", data=form).status_code == 200
    new = client.post("/admin/api/service-clients/tracker-video/rotate", headers=h).json()["secret"]
    assert client.post("/token", data=form).status_code == 401  # old secret is dead
    assert client.post("/token", data={**form, "client_secret": new}).status_code == 200
    client.post("/admin/api/service-clients/tracker-video/status", json={"active": False}, headers=h)
    assert client.post("/token", data={**form, "client_secret": new}).status_code == 401
    actions = {a.action for a in db.query(m.AuditLog)}
    assert {"service_client.created", "service_client.rotated", "service_client.disabled"} <= actions


def test_admin_validation_and_access(client, db, world):
    assert client.get("/admin/api/service-clients").status_code == 403
    h = admin_session(client, db)
    bad = {"client_id": "x1x", "name": "n", "product_key": "tracker", "audiences": ["tracker"], "scopes": ["a"]}
    assert client.post("/admin/api/service-clients", json=bad, headers=h).status_code == 422  # tracker is not a service
    assert client.post("/admin/api/service-clients", json={**bad, "client_id": "ok-one", "audiences": ["video-engine"]}).status_code == 403  # no CSRF


def test_migration_0004_adds_kind_and_the_table_to_an_older_database():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE products (key VARCHAR(30) PRIMARY KEY, name VARCHAR(80), description VARCHAR(200), initiate_url VARCHAR(400), "
                          "active BOOLEAN, webhook_url VARCHAR(400), webhook_secret VARCHAR(100))"))
        c.execute(sa.text("INSERT INTO products VALUES ('tracker', 'Tracker', '', '', 1, '', '')"))
    spec = importlib.util.spec_from_file_location("mig0004", Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0004_service_clients.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for _ in range(2):  # running it twice must be harmless
        with eng.begin() as c:
            with Operations.context(MigrationContext.configure(c)):
                mod.upgrade()
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT kind FROM products WHERE key='tracker'")).scalar() == "product"
        assert c.execute(sa.text("SELECT kind FROM products WHERE key='video-engine'")).scalar() == "service"
        assert "service_clients" in sa.inspect(c).get_table_names()
