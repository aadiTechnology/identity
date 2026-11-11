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
    service_token_minutes: int = 10  # service tokens (client_credentials) are short-lived; callers cache and renew
    auth_code_seconds: int = 60
    lock_after_failures: int = 5
    lock_minutes: int = 15
    login_rate_per_minute: int = 30
    token_rate_per_minute: int = 600  # per calling address; products call from their servers, so this is generous
    webhooks_enabled: bool = True  # background delivery of change events to products
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

    def problems(self) -> list[str]:
        """Unsafe settings for a production start. An empty list means the configuration is acceptable."""
        out: list[str] = []
        if not self.is_prod:
            return out
        if not self.issuer.startswith("https://"):
            out.append("ISSUER must be an https address")
        if not self.cookie_secure:
            out.append("COOKIE_SECURE must be true")
        if "change-me" in self.database_url or "identity:identity@" in self.database_url or self.database_url.startswith("sqlite"):
            out.append("DATABASE_URL still has the development default")
        if self.email_backend == "file":
            out.append("EMAIL_BACKEND is file: invitations and reset links would not be sent")
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
