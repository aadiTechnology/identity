from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import jwt

from app import models as m
from app.security import hash_password, jwks, verify_password
from conftest import PW, authorize_params, get_code, login, make_world, pkce


# ---- sign-in and the "one product goes straight in" rule ----
def test_single_product_user_goes_straight_to_product(client, db):
    make_world(db)
    r = login(client, "solo@example.com")
    assert r.status_code == 303
    hub = client.get("/")
    assert hub.status_code == 303
    loc = hub.headers["location"]
    assert loc.startswith("https://tracker.example.com/login?tenant=")  # no chooser shown


def test_multi_product_user_sees_chooser(client, db):
    make_world(db)
    login(client, "multi@example.com")
    r = client.get("/")
    assert r.status_code == 200
    assert "Alpha Works" in r.text and "Beta Builders" in r.text and "Documents" in r.text


def test_user_without_subscription_sees_explanation(client, db):
    make_world(db)
    login(client, "none@example.com")
    r = client.get("/")
    assert r.status_code == 200 and "No active subscription" in r.text


def test_expired_or_suspended_subscription_removes_product(client, db):
    w = make_world(db)
    s = db.query(m.Subscription).filter_by(tenant_id=w["beta"].id).one()
    s.valid_until = m.now() - timedelta(days=1)
    db.commit()
    login(client, "solo@example.com")
    assert "No active subscription" in client.get("/").text


def test_wrong_password_same_message_and_lockout(client, db):
    make_world(db)
    for _ in range(5):
        r = login(client, "solo@example.com", "wrong-Password1")
        assert r.status_code == 401 and "Email or password is incorrect." in r.text
    r = login(client, "solo@example.com", PW)  # correct password but locked
    assert r.status_code == 401
    unknown = login(client, "ghost@example.com")
    assert unknown.status_code == 401 and "Email or password is incorrect." in unknown.text


def test_csrf_required_on_login(client, db):
    make_world(db)
    r = client.post("/login", data={"email": "solo@example.com", "password": PW, "csrf": "forged"})
    assert r.status_code == 400


def test_open_redirect_blocked_after_login(client, db):
    make_world(db)
    r = login(client, "solo@example.com", next="//evil.example.com")
    assert r.headers["location"] == "/"


def test_bcrypt_hash_from_tracker_works_and_is_upgraded(client, db):
    import bcrypt
    w = make_world(db)
    u = w["people"]["solo"]
    u.password_hash = bcrypt.hashpw(PW.encode(), bcrypt.gensalt(rounds=4)).decode()
    db.commit()
    assert verify_password(PW, u.password_hash) == (True, True)
    assert login(client, "solo@example.com").status_code == 303
    db.refresh(u)
    assert u.password_hash.startswith("$argon2")


# ---- OIDC authorization code + PKCE ----
def full_flow(client, client_id="tracker-web", redirect="https://tracker.example.com/cb", **extra):
    verifier, challenge = pkce()
    r = client.get("/authorize", params=authorize_params(client_id, redirect, challenge, **extra))
    return verifier, r


def test_authorize_requires_login_then_issues_code_and_tokens(client, db):
    make_world(db)
    verifier, r = full_flow(client)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?")
    login(client, "solo@example.com")
    verifier, r = full_flow(client)
    assert r.status_code == 303 and "state=st1" in r.headers["location"]
    code = get_code(r)
    t = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": "https://tracker.example.com/cb",
                                    "code_verifier": verifier, "client_id": "tracker-web"})
    assert t.status_code == 200, t.text
    body = t.json()
    keys = jwks(db)["keys"]
    pub = jwt.algorithms.RSAAlgorithm.from_jwk(keys[0])
    claims = jwt.decode(body["access_token"], pub, algorithms=["RS256"], audience="tracker", issuer="http://testserver")
    assert claims["email"] == "solo@example.com" and claims["tenant_slug"] == "beta" and claims["product"] == "tracker"
    idt = jwt.decode(body["id_token"], pub, algorithms=["RS256"], audience="tracker-web", issuer="http://testserver")
    assert idt["nonce"] == "n1"
    assert client.get("/userinfo", headers={"Authorization": "Bearer " + body["access_token"]}).json()["email"] == "solo@example.com"


def test_code_cannot_be_reused_and_needs_correct_verifier(client, db):
    make_world(db)
    login(client, "solo@example.com")
    verifier, r = full_flow(client)
    code = get_code(r)
    base = {"grant_type": "authorization_code", "code": code, "redirect_uri": "https://tracker.example.com/cb", "client_id": "tracker-web"}
    assert client.post("/token", data={**base, "code_verifier": "wrong" * 10}).status_code == 400
    # the failed attempt burned the code
    assert client.post("/token", data={**base, "code_verifier": verifier}).status_code == 400


def test_pkce_is_mandatory_and_redirect_uri_must_match(client, db):
    make_world(db)
    login(client, "solo@example.com")
    r = client.get("/authorize", params={"response_type": "code", "client_id": "tracker-web", "redirect_uri": "https://tracker.example.com/cb"})
    assert "error=invalid_request" in r.headers["location"]
    r = client.get("/authorize", params=authorize_params(redirect="https://evil.example.com/cb"))
    assert r.status_code == 400 and "location" not in r.headers  # never redirect to an unregistered address


def test_product_in_several_companies_asks_which_company(client, db):
    w = make_world(db)
    login(client, "multi@example.com")
    r = client.get("/authorize", params=authorize_params())
    assert r.status_code == 200 and "Alpha Works" in r.text and "Beta Builders" in r.text
    r = client.get("/authorize", params=authorize_params(tenant=w["alpha"].id))
    assert r.status_code == 303 and "code=" in r.headers["location"]


def test_cannot_pick_company_without_subscription(client, db):
    w = make_world(db)
    login(client, "multi@example.com")
    r = client.get("/authorize", params=authorize_params("dms-web", "https://dms.example.com/cb", tenant=w["beta"].id))
    assert "error=access_denied" in r.headers["location"]  # Beta has no Documents


def test_user_with_other_product_but_not_this_one(client, db):
    make_world(db)
    login(client, "solo@example.com")  # only Beta/Tracker
    r = client.get("/authorize", params=authorize_params("dms-web", "https://dms.example.com/cb"))
    assert r.status_code == 403 and "Product not available" in r.text


def test_confidential_client_needs_secret(client, db):
    make_world(db)
    login(client, "multi@example.com")
    verifier, r = full_flow(client, "dms-web", "https://dms.example.com/cb")
    code = get_code(r)
    base = {"grant_type": "authorization_code", "code": code, "redirect_uri": "https://dms.example.com/cb", "code_verifier": verifier, "client_id": "dms-web"}
    assert client.post("/token", data=base).status_code == 401
    assert client.post("/token", data={**base, "client_secret": "s3cret"}).status_code == 200


def test_refresh_rotation_and_reuse_detection(client, db):
    make_world(db)
    login(client, "solo@example.com")
    verifier, r = full_flow(client)
    t = client.post("/token", data={"grant_type": "authorization_code", "code": get_code(r), "redirect_uri": "https://tracker.example.com/cb",
                                    "code_verifier": verifier, "client_id": "tracker-web"}).json()
    r1 = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": "tracker-web"})
    assert r1.status_code == 200
    new_rt = r1.json()["refresh_token"]
    # replaying the old token is treated as theft: the whole family dies
    assert client.post("/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": "tracker-web"}).status_code == 400
    assert client.post("/token", data={"grant_type": "refresh_token", "refresh_token": new_rt, "client_id": "tracker-web"}).status_code == 400


def test_subscription_ending_stops_refresh(client, db):
    w = make_world(db)
    login(client, "solo@example.com")
    verifier, r = full_flow(client)
    t = client.post("/token", data={"grant_type": "authorization_code", "code": get_code(r), "redirect_uri": "https://tracker.example.com/cb",
                                    "code_verifier": verifier, "client_id": "tracker-web"}).json()
    db.query(m.Subscription).filter_by(tenant_id=w["beta"].id).one().status = "cancelled"
    db.commit()
    assert client.post("/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": "tracker-web"}).status_code == 400


def test_revoking_user_invalidates_access_token_and_session(client, db):
    w = make_world(db)
    login(client, "solo@example.com")
    verifier, r = full_flow(client)
    t = client.post("/token", data={"grant_type": "authorization_code", "code": get_code(r), "redirect_uri": "https://tracker.example.com/cb",
                                    "code_verifier": verifier, "client_id": "tracker-web"}).json()
    from app.service import revoke_user_everywhere
    revoke_user_everywhere(db, w["people"]["solo"])
    db.commit()
    assert client.get("/userinfo", headers={"Authorization": "Bearer " + t["access_token"]}).status_code == 401
    assert client.get("/").headers["location"] == "/login"


def test_products_api_for_switcher(client, db):
    make_world(db)
    login(client, "multi@example.com")
    body = client.get("/api/me/products").json()
    assert body["next"] == "choose" and len(body["items"]) == 3
    client.cookies.clear()
    assert client.get("/api/me/products").status_code == 401


def test_logout_only_honours_registered_return_address(client, db):
    make_world(db)
    login(client, "solo@example.com")
    r = client.get("/logout", params={"client_id": "tracker-web", "post_logout_redirect_uri": "https://evil.example.com/"})
    assert r.headers["location"] == "/login"
    login(client, "solo@example.com")
    r = client.get("/logout", params={"client_id": "tracker-web", "post_logout_redirect_uri": "https://tracker.example.com/"})
    assert r.headers["location"].startswith("https://tracker.example.com/")


def test_discovery_and_jwks_published(client, db):
    d = client.get("/.well-known/openid-configuration").json()
    assert d["jwks_uri"].endswith("/.well-known/jwks.json") and d["code_challenge_methods_supported"] == ["S256"]
    assert client.get(d["jwks_uri"].replace("http://testserver", "")).json()["keys"][0]["alg"] == "RS256"


# ---- password reset ----
def test_password_reset_flow_single_use(client, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_world(db)
    client.get("/forgot")
    r = client.post("/forgot", data={"email": "solo@example.com", "csrf": client.cookies.get("id_csrf")})
    assert "If that email has an account" in r.text
    ghost = client.post("/forgot", data={"email": "ghost@example.com", "csrf": client.cookies.get("id_csrf")})
    assert "If that email has an account" in ghost.text  # no account enumeration
    mail = next((tmp_path / "var" / "outbox").glob("*solo_at_example.com.txt")).read_text()
    token = mail.split("token=")[1].split()[0]
    assert client.get("/reset", params={"token": token}).status_code == 200
    client.get("/reset", params={"token": token})
    ok = client.post("/reset", data={"token": token, "password": "Brand-New-Pass1", "csrf": client.cookies.get("id_csrf")})
    assert "Password saved" in ok.text
    assert client.get("/reset", params={"token": token}).status_code == 400  # used
    assert login(client, "solo@example.com", "Brand-New-Pass1").status_code == 303


# ---- admin ----
def test_admin_requires_platform_admin_and_can_create_company(client, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_world(db)
    login(client, "solo@example.com")
    assert client.get("/admin/api/overview").status_code == 403
    admin = m.User(email="root@example.com", name="Root", password_hash=hash_password(PW), is_platform_admin=True)
    db.add(admin)
    db.commit()
    client.cookies.clear()
    login(client, "root@example.com")
    h = {"x-csrf-token": client.cookies.get("id_csrf")}
    assert client.get("/admin").status_code == 200
    assert client.post("/admin/api/tenants", json={"name": "Delta Co", "owner_email": "o@delta.com", "owner_name": "O", "products": ["dms"]}).status_code == 403  # no CSRF header
    r = client.post("/admin/api/tenants", json={"name": "Delta Co", "owner_email": "o@delta.com", "owner_name": "Owner", "products": ["dms"]}, headers=h)
    assert r.status_code == 200 and r.json()["subscriptions"][0]["product"] == "dms"
    assert any((tmp_path / "var" / "outbox").glob("*o_at_delta.com.txt"))  # invitation mail
    r = client.post(f"/admin/api/tenants/{r.json()['id']}/subscriptions", json={"product": "tracker"}, headers=h)
    assert len(r.json()["subscriptions"]) == 2


# ---- importer ----
def test_tracker_import_is_idempotent_and_keeps_hashes(db, tmp_path):
    import bcrypt
    from sqlalchemy import create_engine, text
    from app.importer import import_tracker
    db.add(m.Product(key="tracker", name="Project Tracker", initiate_url="https://tracker.example.com/login"))
    db.commit()
    url = f"sqlite:///{tmp_path / 'tracker.db'}"
    e = create_engine(url)
    h = bcrypt.hashpw(b"Old-Password-1", bcrypt.gensalt(rounds=4)).decode()
    with e.begin() as c:
        c.execute(text("create table tenants (id integer, name text, slug text, status text)"))
        c.execute(text("create table users (id integer, email text, name text, password_hash text, is_platform_admin boolean, status text)"))
        c.execute(text("create table roles (id integer, key text)"))
        c.execute(text("create table memberships (tenant_id integer, user_id integer, role_id integer, status text)"))
        c.execute(text("insert into tenants values (1,'Acme','acme','active')"))
        c.execute(text("insert into users values (1,'Boss@Acme.com','Boss',:h,0,'active'),(2,'dev@acme.com','Dev',:h,0,'active')"), {"h": h})
        c.execute(text("insert into roles values (1,'owner'),(2,'employee')"))
        c.execute(text("insert into memberships values (1,1,1,'active'),(1,2,2,'active')"))
    r1 = import_tracker(db, url)
    db.commit()
    assert (r1.users_created, r1.tenants_created, r1.members_created, r1.subscriptions_created) == (2, 1, 2, 1)
    r2 = import_tracker(db, url)
    db.commit()
    assert (r2.users_created, r2.tenants_created, r2.members_created, r2.subscriptions_created) == (0, 0, 0, 0)
    boss = db.query(m.User).filter_by(email="boss@acme.com").one()
    assert verify_password("Old-Password-1", boss.password_hash)[0]
    assert db.query(m.TenantMember).filter_by(user_id=boss.id).one().is_admin
