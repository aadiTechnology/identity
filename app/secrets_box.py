"""Encryption of secrets stored in the database (webhook signing secrets, token signing keys).

Off unless SECRETS_KEY is set (a Fernet key: `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`).
With the key set, new and changed values are stored as `enc:v1:<token>`; reading decrypts, and a value without the prefix is returned as it is,
so a database that still holds plaintext keeps working while `python -m app.cli encrypt-secrets` converts it. Losing the key makes the encrypted
values unreadable: keep it in Coolify and in the owner's password manager, never in git. Webhook secrets must stay recoverable (events are signed
with them), which is why they are encrypted and not hashed."""
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from .config import get_settings

PREFIX = "enc:v1:"


def _fernet() -> Fernet | None:
    key = get_settings().secrets_key
    return Fernet(key.encode()) if key else None


def encrypt(value: str | None) -> str | None:
    f = _fernet()
    if f is None or not value or value.startswith(PREFIX):
        return value
    return PREFIX + f.encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value or not value.startswith(PREFIX):
        return value
    f = _fernet()
    if f is None:
        raise RuntimeError("The database holds encrypted secrets but SECRETS_KEY is not set")
    try:
        return f.decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("SECRETS_KEY does not match the key used to encrypt the stored secrets") from e


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(PREFIX)


class EncryptedText(TypeDecorator):
    """A text column that is encrypted at rest when SECRETS_KEY is set."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)
