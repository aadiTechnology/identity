"""entitlements v1: plans, plan_features, plan_limits and subscriptions.plan_key

Revision ID: 0006
Revises: 0005

Additive only: three new tables and one nullable column. Existing subscriptions keep plan_key NULL and behave exactly as before.
Fresh databases already get these from the models (revision 0001 creates the schema from them), hence the guards.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade():
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    if "plans" not in tables:
        op.create_table("plans", sa.Column("key", sa.String(40), primary_key=True), sa.Column("product_key", sa.String(30), sa.ForeignKey("products.key"), nullable=False),
                        sa.Column("name", sa.String(80), nullable=False), sa.Column("description", sa.String(200), nullable=False, server_default=""),
                        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()))
        op.create_index("ix_plans_product_key", "plans", ["product_key"])
    if "plan_features" not in tables:
        op.create_table("plan_features", sa.Column("id", sa.String(36), primary_key=True), sa.Column("plan_key", sa.String(40), sa.ForeignKey("plans.key"), nullable=False),
                        sa.Column("feature", sa.String(60), nullable=False), sa.UniqueConstraint("plan_key", "feature"))
        op.create_index("ix_plan_features_plan_key", "plan_features", ["plan_key"])
    if "plan_limits" not in tables:
        op.create_table("plan_limits", sa.Column("id", sa.String(36), primary_key=True), sa.Column("plan_key", sa.String(40), sa.ForeignKey("plans.key"), nullable=False),
                        sa.Column("limit_key", sa.String(60), nullable=False), sa.Column("value", sa.Integer, nullable=True), sa.UniqueConstraint("plan_key", "limit_key"))
        op.create_index("ix_plan_limits_plan_key", "plan_limits", ["plan_key"])
    if "plan_key" not in {c["name"] for c in insp.get_columns("subscriptions")}:
        with op.batch_alter_table("subscriptions") as b:
            b.add_column(sa.Column("plan_key", sa.String(40), sa.ForeignKey("plans.key"), nullable=True))


def downgrade():
    with op.batch_alter_table("subscriptions") as b:
        b.drop_column("plan_key")
    op.drop_table("plan_limits")
    op.drop_table("plan_features")
    op.drop_table("plans")
