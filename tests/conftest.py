import os

os.environ.update({"DATABASE_URL": "sqlite://", "COOKIE_SECURE": "false", "APP_ENV": "test", "ISSUER": "http://testserver",
                   "EMAIL_BACKEND": "file", "LOGIN_RATE_PER_MINUTE": "1000", "WEBHOOKS_ENABLED": "false"})

import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import models as m
from app.db import Base, SessionLocal, engine
from app.main import app
from app.security import ensure_signing_key, hash_password, hash_token

PW = "Correct-Horse-9"


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        ensure_signing_key(s)
        yield s


@pytest.fixture()
def client(db):
    with TestClient(app, follow_redirects=False) as c:
        yield c


def make_world(db):
    """Two products, three people: solo (one product), multi (two companies, two products), none (no subscription)."""
    for key, name in [("tracker", "Project Tracker"), ("dms", "Documents")]:
        db.add(m.Product(key=key, name=name, description=name + " description", initiate_url=f"https://{key}.example.com/login"))
    db.add(m.OAuthClient(client_id="tracker-web", product_key="tracker", name="Tracker", redirect_uris=["https://tracker.example.com/cb"],
                         post_logout_redirect_uris=["https://tracker.example.com/"]))
    db.add(m.OAuthClient(client_id="dms-web", product_key="dms", name="DMS", redirect_uris=["https://dms.example.com/cb"],
                         secret_hash=hash_token("s3cret")))
    a = m.Tenant(name="Alpha Works", slug="alpha")
    b = m.Tenant(name="Beta Builders", slug="beta")
    db.add_all([a, b])
    db.flush()
    people = {}
    for key, name in [("solo", "Solo User"), ("multi", "Multi User"), ("none", "No Sub")]:
        u = m.User(email=f"{key}@example.com", name=name, password_hash=hash_password(PW))
        db.add(u)
        db.flush()
        people[key] = u
    db.add_all([m.Subscription(tenant_id=a.id, product_key="tracker"), m.Subscription(tenant_id=a.id, product_key="dms"),
                m.Subscription(tenant_id=b.id, product_key="tracker")])
    db.add_all([m.TenantMember(tenant_id=b.id, user_id=people["solo"].id),
                m.TenantMember(tenant_id=a.id, user_id=people["multi"].id), m.TenantMember(tenant_id=b.id, user_id=people["multi"].id)])
    nosub = m.Tenant(name="Gamma", slug="gamma")
    db.add(nosub)
    db.flush()
    db.add(m.TenantMember(tenant_id=nosub.id, user_id=people["none"].id))
    db.commit()
    return {"alpha": a, "beta": b, "people": people}


def login(client, email, password=PW, next="/"):
    client.get("/login")
    return client.post("/login", data={"email": email, "password": password, "next": next, "csrf": client.cookies.get("id_csrf")})


def pkce():
    v = secrets.token_urlsafe(40)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def authorize_params(client_id="tracker-web", redirect="https://tracker.example.com/cb", challenge="x", **extra):
    return {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st1", "nonce": "n1", **extra}


def get_code(resp):
    loc = resp.headers["location"]
    return parse_qs(urlparse(loc).query)["code"][0]
