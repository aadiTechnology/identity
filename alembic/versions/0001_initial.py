"""initial identity schema

Revision ID: 0001
Revises:
"""
from alembic import op

from app import models  # noqa: F401
from app.db import Base

revision = "0001"
down_revision = None


def upgrade():
    # The first release creates the whole schema from the models. Later revisions are explicit ALTERs.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
