"""Housekeeping: removes records that can no longer matter, so tables stay small and nothing sensitive lingers."""
from datetime import timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from . import models as m


def prune(db: Session, now=None) -> dict:
    now = now or m.now()
    week, month = now - timedelta(days=7), now - timedelta(days=30)
    out = {}
    out["sessions"] = db.execute(delete(m.LoginSession).where(or_(m.LoginSession.expires_at < week, (m.LoginSession.revoked.is_(True)) & (m.LoginSession.created_at < week)))).rowcount
    out["auth_codes"] = db.execute(delete(m.AuthCode).where(m.AuthCode.expires_at < now - timedelta(hours=1))).rowcount
    out["refresh_tokens"] = db.execute(delete(m.RefreshToken).where(m.RefreshToken.expires_at < month)).rowcount
    out["reset_tokens"] = db.execute(delete(m.AuthToken).where(m.AuthToken.expires_at < week)).rowcount
    out["delivered_events"] = db.execute(delete(m.Outbox).where(m.Outbox.delivered_at.is_not(None), m.Outbox.delivered_at < month)).rowcount
    db.commit()
    return out
