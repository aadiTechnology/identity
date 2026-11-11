"""service clients and service-type products (OAuth client_credentials for shared services)

Revision ID: 0004
Revises: 0003

Adds products.kind (existing rows are products) and the service_clients table. Safe to run on a database
that revision 0001 already built from the current models.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    insp = sa.inspect(op.get_bind())
    if "kind" not in {c["name"] for c in insp.get_columns("products")}:
        op.add_column("products", sa.Column("kind", sa.String(10), nullable=False, server_default="product"))
    if "service_clients" not in insp.get_table_names():
        op.create_table(
            "service_clients",
            sa.Column("client_id", sa.String(80), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("product_key", sa.String(30), sa.ForeignKey("products.key"), nullable=False),
            sa.Column("secret_hash", sa.String(100), nullable=False),
            sa.Column("audiences", sa.JSON, nullable=False),
            sa.Column("scopes", sa.JSON, nullable=False),
            sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("secret_rotated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


    # The first shared service. A company is entitled to it with an ordinary subscription (admin page chips).
    bind = op.get_bind()
    if not bind.execute(sa.text("SELECT 1 FROM products WHERE key = 'video-engine'")).first():
        bind.execute(sa.text("INSERT INTO products (key, name, description, initiate_url, active, webhook_url, webhook_secret, kind) "
                             "VALUES ('video-engine', 'Video Engine', 'Shared service: turns approved scripts into narrated videos', '', :a, '', '', 'service')"),
                     {"a": True})


def downgrade():
    op.get_bind().execute(sa.text("DELETE FROM products WHERE key = 'video-engine'"))
    op.drop_table("service_clients")
    op.drop_column("products", "kind")
