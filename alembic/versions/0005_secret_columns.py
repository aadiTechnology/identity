"""secret columns become TEXT so they can hold encrypted values

Revision ID: 0005
Revises: 0004

products.webhook_secret (was VARCHAR(100)) and signing_keys.private_pem (was VARCHAR(4000)) become TEXT. No data change: encryption itself is
switched on by SECRETS_KEY and applied to existing rows with `python -m app.cli encrypt-secrets`. Fresh databases already get TEXT from the models.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"

COLUMNS = [("products", "webhook_secret", sa.String(100), "''"), ("signing_keys", "private_pem", sa.String(4000), None)]


def _is_varchar(insp, table, col):
    return any(c["name"] == col and isinstance(c["type"], sa.String) and not isinstance(c["type"], sa.Text) for c in insp.get_columns(table))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":  # SQLite does not enforce lengths
        return
    insp = sa.inspect(bind)
    for table, col, _, _ in COLUMNS:
        if _is_varchar(insp, table, col):
            op.alter_column(table, col, type_=sa.Text(), existing_nullable=False)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, col, old, _ in COLUMNS:  # fails on purpose if an encrypted value no longer fits: decrypt first (see runbook)
        op.alter_column(table, col, type_=old, existing_nullable=False)
