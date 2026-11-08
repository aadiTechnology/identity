"""change events: product webhook settings and the outbox

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    # A fresh database already has these (revision 0001 builds from the current models); existing ones need them added.
    cols = {c["name"] for c in insp.get_columns("products")}
    if "webhook_url" not in cols:
        op.add_column("products", sa.Column("webhook_url", sa.String(400), nullable=False, server_default=""))
    if "webhook_secret" not in cols:
        op.add_column("products", sa.Column("webhook_secret", sa.String(100), nullable=False, server_default=""))
    if not insp.has_table("outbox"):
        from app.models import Outbox
        Outbox.__table__.create(bind)


def downgrade():
    op.drop_table("outbox")
    op.drop_column("products", "webhook_secret")
    op.drop_column("products", "webhook_url")
