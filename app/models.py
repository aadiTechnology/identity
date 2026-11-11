"""Identity data model. UUID strings are used for every primary key so products can store them next to their own IDs."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(d: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(300), default="")
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | invited | suspended
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_epoch: Mapped[int] = mapped_column(Integer, default=0)  # bump to revoke every issued token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    memberships: Mapped[list["TenantMember"]] = relationship(back_populates="user")


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended | trial
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    members: Mapped[list["TenantMember"]] = relationship(back_populates="tenant")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="tenant")


class TenantMember(Base):
    """A person's membership of a company. Roles stay inside each product (decision D3)."""
    __tablename__ = "tenant_members"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # can manage the company's people and subscriptions
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    tenant: Mapped[Tenant] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Product(Base):
    __tablename__ = "products"
    key: Mapped[str] = mapped_column(String(30), primary_key=True)  # tracker | dms | crm | social
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(200), default="")
    initiate_url: Mapped[str] = mapped_column(String(400), default="")  # product URL that starts sign-in
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    webhook_url: Mapped[str] = mapped_column(String(400), default="")  # where change events are delivered (empty = none)
    webhook_secret: Mapped[str] = mapped_column(String(100), default="")  # signs events and authorises the product's snapshot reads
    kind: Mapped[str] = mapped_column(String(10), default="product")  # product (people sign in) | service (called by products, e.g. video-engine)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", "product_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    product_key: Mapped[str] = mapped_column(ForeignKey("products.key"))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | trial | suspended | cancelled
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenant: Mapped[Tenant] = relationship(back_populates="subscriptions")


class OAuthClient(Base):
    __tablename__ = "oauth_clients"
    client_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    product_key: Mapped[str] = mapped_column(ForeignKey("products.key"))
    name: Mapped[str] = mapped_column(String(120))
    secret_hash: Mapped[str] = mapped_column(String(100), default="")  # empty = public client (PKCE only)
    redirect_uris: Mapped[list] = mapped_column(JSON, default=list)
    post_logout_redirect_uris: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ServiceClient(Base):
    """A product's server-side credential for calling a shared service (OAuth client_credentials). Never used for people."""
    __tablename__ = "service_clients"
    client_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    product_key: Mapped[str] = mapped_column(ForeignKey("products.key"))  # the calling product; it must be subscribed for the tenant it asks about
    secret_hash: Mapped[str] = mapped_column(String(100))
    audiences: Mapped[list] = mapped_column(JSON, default=list)  # service keys this client may ask tokens for, e.g. ["video-engine"]
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["video:create", "video:read", "video:cancel"]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    secret_rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    csrf: Mapped[str] = mapped_column(String(64), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuthCode(Base):
    __tablename__ = "auth_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str] = mapped_column(String(36))
    redirect_uri: Mapped[str] = mapped_column(String(500))
    code_challenge: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(200), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family: Mapped[str] = mapped_column(String(36), index=True)
    client_id: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthToken(Base):
    """One-time tokens for invitations and password reset (stored hashed)."""
    __tablename__ = "auth_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # invite | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class SigningKey(Base):
    __tablename__ = "signing_keys"
    kid: Mapped[str] = mapped_column(String(40), primary_key=True)
    private_pem: Mapped[str] = mapped_column(String(4000))
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # signs new tokens
    published: Mapped[bool] = mapped_column(Boolean, default=True)  # appears in JWKS (old keys stay until tokens expire)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ImportMap(Base):
    """Links an Identity user/tenant to the ID it had in a source system (for migration and audit)."""
    __tablename__ = "import_map"
    __table_args__ = (UniqueConstraint("source", "kind", "source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(20))  # user | tenant
    source_id: Mapped[str] = mapped_column(String(60))
    identity_id: Mapped[str] = mapped_column(String(36))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Outbox(Base):
    """One row per event per product. A background worker delivers it, signed, and retries with back-off."""
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(36), index=True)  # same for every product that receives the event
    product_key: Mapped[str] = mapped_column(String(30), index=True)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed: Mapped[bool] = mapped_column(Boolean, default=False)  # gave up after the last retry
    last_error: Mapped[str] = mapped_column(String(300), default="")


class MemberProduct(Base):
    """A person's registration in one product of a company. Decides which products the switcher shows them; it is NOT a sign-in gate
    (that is the company's live subscription plus an active membership). Counted against the product's seats."""
    __tablename__ = "member_products"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "product_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    product_key: Mapped[str] = mapped_column(ForeignKey("products.key"))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | removed
    source: Mapped[str] = mapped_column(String(20), default="backfill")  # backfill | company | admin | product
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
