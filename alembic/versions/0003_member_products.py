"""which products a person is registered in (drives the product switcher and per-product seats)

Revision ID: 0003
Revises: 0002

Creates member_products and registers every active member in every product their company already has, so the switcher
shows exactly what it showed before this release. From now on a product that enrols a person registers only itself.
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "member_products" in insp.get_table_names():  # a fresh database already has it (revision 0001 builds from the current models)
        return
    op.create_table(
        "member_products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("product_key", sa.String(30), sa.ForeignKey("products.key"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("source", sa.String(20), nullable=False, server_default="backfill"),
        sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "product_key"),
    )
    rows = bind.execute(sa.text(
        "SELECT m.tenant_id, m.user_id, s.product_key FROM tenant_members m JOIN subscriptions s ON s.tenant_id = m.tenant_id WHERE m.status = 'active'")).all()
    now = datetime.now(timezone.utc)
    for tenant, user, product in rows:
        bind.execute(sa.text("INSERT INTO member_products (id, tenant_id, user_id, product_key, status, source, created_by, created_at) "
                             "VALUES (:i, :t, :u, :p, 'active', 'backfill', 'migration', :c)"),
                     {"i": str(uuid.uuid4()), "t": tenant, "u": user, "p": product, "c": now})


def downgrade():
    op.drop_table("member_products")
