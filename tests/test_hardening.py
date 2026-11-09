"""Sprint 5: rate limits, production guard, housekeeping, operations status, key rotation."""
from datetime import timedelta

import jwt
from sqlalchemy import select

from app import models as m
from app.config import Settings
from app.maintenance import prune
from app.security import jwks, rotate_signing_key, sign, verify
from conftest import PW, authorize_params, get_code, login, make_world, pkce
from test_events import csrf, prepare


def forgot(client, email):
    client.get("/forgot")
    return client.post("/forgot", data={"email": email, "csrf": client.cookies.get("id_csrf")})


def test_forgot_password_is_limited_per_address_and_per_email(client, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_world(db)
    answers = [forgot(client, "solo@example.com") for _ in range(5)]
    assert all("If that email has an account" in a.text for a in answers)  # same answer every time
    sent = list((tmp_path / "var" / "outbox").glob("*solo_at_example.com.txt"))
    assert len(sent) == 3  # the fourth and fifth send nothing
    for i in range(12):
        r = forgot(client, f"person{i}@example.com")
    assert r.status_code == 429  # one address cannot spray many accounts


def test_reset_attempts_are_limited(client, db):
    make_world(db)
    client.get("/forgot")
    codes = [client.post("/reset", data={"token": "x", "password": "Aaaaaaaa1x", "csrf": client.cookies.get("id_csrf")}).status_code for _ in range(12)]
    assert codes[0] == 400 and codes[-1] == 429


def test_guessing_client_secrets_is_slowed_down(client, db):
    make_world(db)
    codes = [client.post("/token", data={"grant_type": "authorization_code", "client_id": "dms-web", "client_secret": f"guess{i}"}).status_code for i in range(25)]
    assert codes[0] == 401 and codes[-1] == 429


def test_production_refuses_unsafe_settings():
    good = dict(app_env="production", issuer="https://id.example.com", cookie_secure=True, email_backend="smtp",
                database_url="postgresql+psycopg://identity:a-real-secret@postgres:5432/identity")
    assert Settings(**good).problems() == []
    assert any("https" in p for p in Settings(**{**good, "issuer": "http://id.example.com"}).problems())
    assert any("COOKIE_SECURE" in p for p in Settings(**{**good, "cookie_secure": False}).problems())
    assert any("DATABASE_URL" in p for p in Settings(**{**good, "database_url": "postgresql+psycopg://identity:change-me@localhost/identity"}).problems())
    assert any("EMAIL_BACKEND" in p for p in Settings(**{**good, "email_backend": "file"}).problems())
    assert Settings(app_env="demo", issuer="http://x", cookie_secure=False).problems() == []  # only production is strict


def test_prune_removes_old_records_and_keeps_recent_ones(db):
    w = make_world(db)
    u = w["people"]["solo"].id
    old, now = m.now() - timedelta(days=60), m.now()
    db.add_all([
        m.LoginSession(user_id=u, token_hash="old", expires_at=old, created_at=old, epoch=0),
        m.LoginSession(user_id=u, token_hash="new", expires_at=now + timedelta(hours=1), epoch=0),
        m.RefreshToken(token_hash="r-old", family="f", client_id="c", user_id=u, tenant_id="t", session_id="s", expires_at=old),
        m.RefreshToken(token_hash="r-new", family="f", client_id="c", user_id=u, tenant_id="t", session_id="s", expires_at=now + timedelta(days=5)),
        m.Outbox(event_id="e1", product_key="tracker", type="x", payload={}, delivered_at=old),
        m.Outbox(event_id="e2", product_key="tracker", type="x", payload={}),  # still waiting: never pruned
    ])
    db.commit()
    out = prune(db)
    assert out["sessions"] == 1 and out["refresh_tokens"] == 1 and out["delivered_events"] == 1
    assert {s.token_hash for s in db.execute(select(m.LoginSession)).scalars()} == {"new"}
    assert db.execute(select(m.Outbox)).scalars().all()[0].event_id == "e2"


def test_ops_status_is_for_platform_admins_and_reports_delivery(client, db):
    w = prepare(db)
    login(client, "solo@example.com")
    assert client.get("/admin/api/ops").status_code == 403
    client.cookies.clear()
    login(client, "root@example.com")
    client.post(f"/admin/api/tenants/{w['alpha'].id}/status", json={"status": "suspended"}, headers=csrf(client))
    ops = client.get("/admin/api/ops").json()
    assert ops["database"] == "ok" and ops["events"]["pending"] == 1 and ops["events"]["failed"] == 0
    assert ops["signing_key"]["published"] == 1 and {p["key"]: p["webhook"] for p in ops["products"]}["tracker"] is True
    assert client.post("/admin/api/maintenance/prune", headers=csrf(client)).status_code == 200


def test_readiness_needs_database_and_a_signing_key(client, db):
    assert client.get("/health/ready").json() == {"status": "ready"}
    db.query(m.SigningKey).delete()
    db.commit()
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "no signing key"


def test_key_rotation_keeps_old_tokens_valid_and_signs_new_ones_with_the_new_key(client, db):
    make_world(db)
    before = sign(db, {"sub": "u", "aud": "tracker"}, 15)
    old_kid = jwt.get_unverified_header(before)["kid"]
    new = rotate_signing_key(db)
    after = sign(db, {"sub": "u", "aud": "tracker"}, 15)
    assert jwt.get_unverified_header(after)["kid"] == new.kid != old_kid
    assert {k["kid"] for k in jwks(db)["keys"]} == {old_kid, new.kid}  # both published
    assert verify(db, before, "tracker")["sub"] == "u" and verify(db, after, "tracker")["sub"] == "u"
    # a full sign-in after rotation still works end to end
    login(client, "solo@example.com")
    v, ch = pkce()
    code = get_code(client.get("/authorize", params=authorize_params(challenge=ch)))
    t = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": "https://tracker.example.com/cb", "code_verifier": v, "client_id": "tracker-web"})
    assert t.status_code == 200 and jwt.get_unverified_header(t.json()["access_token"])["kid"] == new.kid


def test_platform_administrator_without_a_company_lands_on_the_admin_page(client, db):
    prepare(db)
    login(client, "root@example.com")  # belongs to no company
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    client.cookies.clear()
    login(client, "none@example.com")  # an ordinary person without a subscription still gets the explanation
    assert "No active subscription" in client.get("/").text
