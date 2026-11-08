"""Settings, read from environment variables (see .env.example)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "production"  # production | demo | test
    issuer: str = "https://id.aadiprojecttracker.aaditechnology.com"
    database_url: str = "postgresql+psycopg://identity:identity@localhost:15440/identity"
    # Optional PEM private key. When empty, a key is generated once and stored in the database (rotatable).
    signing_key_pem: str = ""
    cookie_secure: bool = True
    session_hours: int = 12
    access_token_minutes: int = 15
    id_token_minutes: int = 15
    refresh_token_days: int = 14
    auth_code_seconds: int = 60
    lock_after_failures: int = 5
    lock_minutes: int = 15
    login_rate_per_minute: int = 30
    # email: file (writes to var/outbox) or smtp
    email_backend: str = "file"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@aaditechnology.com"
    smtp_use_tls: bool = True

    @property
    def is_prod(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
