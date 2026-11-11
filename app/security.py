"""Passwords, one-time tokens and RS256 signing keys."""
import base64
import hashlib
import secrets
from datetime import datetime, timedelta

import bcrypt
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .config import get_settings

_ph = PasswordHasher()  # Argon2id, library defaults
_DUMMY = _ph.hash("dummy-password-for-constant-time")


def hash_password(p: str) -> str:
    return _ph.hash(p)


def verify_password(p: str, h: str) -> tuple[bool, bool]:
    """Returns (ok, needs_rehash). Imported Project Tracker hashes are bcrypt and are upgraded to Argon2id on first sign-in."""
    if not h:
        return False, False
    if h.startswith("$argon2"):
        try:
            _ph.verify(h, p)
            return True, _ph.check_needs_rehash(h)
        except (VerificationError, InvalidHashError):
            return False, False
    if h.startswith("$2"):
        try:
            return bcrypt.checkpw(p.encode()[:72], h.encode()), True
        except ValueError:
            return False, False
    return False, False


def burn_time():
    """Spend the same time as a real verification so unknown accounts are indistinguishable."""
    try:
        _ph.verify(_DUMMY, "x")
    except VerificationError:
        pass


def check_password_strength(p: str) -> str | None:
    if len(p) < 10 or p.lower() == p or not any(c.isdigit() for c in p):
        return "Use at least 10 characters with upper and lower case letters and a number."
    return None


def new_token() -> tuple[str, str]:
    t = secrets.token_urlsafe(32)
    return t, hash_token(t)


def hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def pkce_ok(verifier: str, challenge: str) -> bool:
    return secrets.compare_digest(b64u(hashlib.sha256(verifier.encode()).digest()), challenge)


# ---- signing keys ----
def _generate_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def ensure_signing_key(db: Session) -> m.SigningKey:
    k = db.execute(select(m.SigningKey).where(m.SigningKey.active.is_(True)).order_by(m.SigningKey.created_at.desc())).scalars().first()
    if k:
        return k
    pem = get_settings().signing_key_pem.replace("\\n", "\n") or _generate_pem()
    kid = hashlib.sha256(pem.encode()).hexdigest()[:16]
    k = m.SigningKey(kid=kid, private_pem=pem)
    db.add(k)
    db.commit()
    return k


def rotate_signing_key(db: Session) -> m.SigningKey:
    """New key signs from now on; old keys stay published until their tokens have expired."""
    for k in db.execute(select(m.SigningKey).where(m.SigningKey.active.is_(True))).scalars():
        k.active = False
    pem = _generate_pem()
    nk = m.SigningKey(kid=hashlib.sha256(pem.encode()).hexdigest()[:16], private_pem=pem)
    db.add(nk)
    db.commit()
    return nk


_parsed: dict[str, tuple] = {}  # kid -> (private key, public key, jwk). Keys never change once created, so this is safe to keep.


def _key_material(k: m.SigningKey) -> tuple:
    got = _parsed.get(k.kid)
    if got is None:
        priv = serialization.load_pem_private_key(k.private_pem.encode(), None)
        pub = priv.public_key()
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(pub, as_dict=True)
        jwk.update({"kid": k.kid, "use": "sig", "alg": "RS256"})
        got = _parsed[k.kid] = (priv, pub, jwk)
    return got


def jwks(db: Session) -> dict:
    ensure_signing_key(db)
    return {"keys": [_key_material(k)[2] for k in db.execute(select(m.SigningKey).where(m.SigningKey.published.is_(True))).scalars()]}


def sign(db: Session, claims: dict, minutes: int, typ: str | None = None) -> str:
    k = ensure_signing_key(db)
    now = datetime.now(tz=__import__("datetime").timezone.utc)
    body = {**claims, "iss": get_settings().issuer, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(body, _key_material(k)[0], algorithm="RS256", headers={"kid": k.kid, **({"typ": typ} if typ else {})})


def verify(db: Session, token: str, audience: str | None = None) -> dict:
    kid = jwt.get_unverified_header(token).get("kid")
    k = db.get(m.SigningKey, kid)
    if not k or not k.published:
        raise jwt.InvalidTokenError("unknown key")
    pub = _key_material(k)[1]
    return jwt.decode(token, pub, algorithms=["RS256"], issuer=get_settings().issuer, audience=audience,
                      options={"verify_aud": audience is not None})
