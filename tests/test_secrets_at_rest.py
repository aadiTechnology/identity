"""P2-S3: webhook secrets and signing keys are encrypted in the database when SECRETS_KEY is set, and everything keeps working."""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app import cli, events, security
from app import models as m
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.secrets_box import PREFIX, decrypt, encrypt
from tests.conftest import make_world

SECRET = "tracker-product-secret"
KEY = Fernet.generate_key().decode()


@pytest.fixture()
def world(db):
    w = make_world(db)
    db.get(m.Product, "tracker").webhook_secret = SECRET
    db.commit()
    return w


@pytest.fixture()
def with_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "secrets_key", KEY)


def raw(db, sql):
    db.expire_all()
    with SessionLocal() as s:
        return s.execute(text(sql)).scalar()


RAW_SECRET = "SELECT webhook_secret FROM products WHERE key = 'tracker'"
RAW_PEM = "SELECT private_pem FROM signing_keys ORDER BY created_at DESC LIMIT 1"


def test_nothing_changes_when_no_key_is_set(db, world):
    assert raw(db, RAW_SECRET) == SECRET
    assert raw(db, RAW_PEM).startswith("-----BEGIN")
    assert encrypt("x") == "x" and decrypt("x") == "x"


def test_with_a_key_new_values_are_stored_encrypted_and_read_back_plain(db, world, with_key):
    db.get(m.Product, "tracker").webhook_secret = "another-secret"
    db.commit()
    stored = raw(db, RAW_SECRET)
    assert stored.startswith(PREFIX) and "another-secret" not in stored
    db.expire_all()
    assert db.get(m.Product, "tracker").webhook_secret == "another-secret"
    nk = security.rotate_signing_key(db)  # a new signing key is stored encrypted too
    stored_pem = raw(db, f"SELECT private_pem FROM signing_keys WHERE kid = '{nk.kid}'")
    assert stored_pem.startswith(PREFIX) and "BEGIN" not in stored_pem


def test_tokens_still_sign_and_verify_with_an_encrypted_key(db, world, with_key):
    security.rotate_signing_key(db)
    tok = security.sign(db, {"sub": "u", "aud": "x"}, 5)
    assert security.verify(db, tok, audience="x")["sub"] == "u"
    assert any(k["kid"] for k in security.jwks(db)["keys"])


def test_events_are_signed_with_the_plain_secret(db, world, with_key):
    p = db.get(m.Product, "tracker")
    p.webhook_url, p.webhook_secret = "https://tracker.example.com/hook", "sign-me"
    db.add(m.Outbox(event_id="e1", product_key="tracker", type="membership.added", payload={"id": "e1"}))
    db.commit()
    assert raw(db, RAW_SECRET).startswith(PREFIX)
    seen = []
    events.deliver_pending(db, send=lambda url, secret, body: seen.append(secret))
    assert seen == ["sign-me"]  # the receiver verifies with the plain secret, so signing must use it


def test_the_shared_secret_login_still_works_when_encrypted(client, db, world, with_key):
    new_secret = SECRET + "-rotated"  # a changed value is rewritten, and therefore encrypted
    db.get(m.Product, "tracker").webhook_secret = new_secret
    db.commit()
    assert raw(db, RAW_SECRET).startswith(PREFIX)
    ok = client.get(f"/api/v1/products/seats?tenant_id={world['alpha'].id}", headers={"Authorization": f"Bearer {new_secret}"})
    assert ok.status_code == 200
    bad = client.get(f"/api/v1/products/seats?tenant_id={world['alpha'].id}", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_plaintext_rows_keep_working_then_encrypt_secrets_converts_them_once(db, world, monkeypatch, capsys):
    assert raw(db, RAW_SECRET) == SECRET  # written before the key existed
    monkeypatch.setattr(get_settings(), "secrets_key", KEY)
    db.expire_all()
    assert db.get(m.Product, "tracker").webhook_secret == SECRET  # still readable
    cli.encrypt_secrets(None)
    first = capsys.readouterr().out
    assert raw(db, RAW_SECRET).startswith(PREFIX) and raw(db, RAW_PEM).startswith(PREFIX)
    db.expire_all()
    assert db.get(m.Product, "tracker").webhook_secret == SECRET
    assert "Encrypted" in first and "Encrypted 0 stored" not in first
    cli.encrypt_secrets(None)
    assert "Encrypted 0 stored" in capsys.readouterr().out  # safe to repeat
    tok = security.sign(db, {"sub": "u"}, 5)
    assert security.verify(db, tok)["sub"] == "u"


def test_encrypt_secrets_without_a_key_does_nothing(db, world):
    with pytest.raises(SystemExit) as e:
        cli.encrypt_secrets(None)
    assert "SECRETS_KEY is not set" in str(e.value)
    assert raw(db, RAW_SECRET) == SECRET


def test_wrong_or_missing_key_fails_loudly_instead_of_returning_garbage(db, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "secrets_key", KEY)
    token = encrypt("value")
    assert token.startswith(PREFIX)
    monkeypatch.setattr(get_settings(), "secrets_key", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="does not match"):
        decrypt(token)
    monkeypatch.setattr(get_settings(), "secrets_key", "")
    with pytest.raises(RuntimeError, match="SECRETS_KEY is not set"):
        decrypt(token)


def test_a_malformed_key_is_reported_as_an_unsafe_production_setting():
    common = dict(app_env="production", database_url="postgresql+psycopg://u:p@h/db", email_backend="smtp")
    assert any("SECRETS_KEY" in p for p in Settings(secrets_key="not-a-key", **common).problems())
    assert not any("SECRETS_KEY" in p for p in Settings(secrets_key=KEY, **common).problems())
