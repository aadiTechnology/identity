"""The Aadi-Test company: one fictional company with 15 people, the same people in Identity, Project Tracker and Academic Video Creation.

Everyone shares one password (below) so a developer never has to remember logins. The company has the Project Tracker, Academic and Video Engine
subscriptions. NO ONE here is a platform administrator, so the company can be shown on a sign-in page without exposing the platform.
ALL NAMES ARE FICTIONAL (first names come from the Aadi Technology Matrix workbook). Keep this list identical in the three products' seeds
(identity/app/test_tenant.py, ProjectTracking/backend/app/seed_aadi_test.py, Media/Academics/backend/app/seed_aadi_test.py).

Commands:  python -m app.cli seed-test                  add or refresh the company (safe to repeat)
           python -m app.cli reset-and-seed-test        ERASES every company and person (platform administrators stay), then seeds. Asks you to type YES."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import models as m
from .security import hash_password

TENANT_ID = "5ca1ab1e-aad1-4e57-8000-000000000001"  # fixed so every product agrees on the company id
TENANT_NAME = "Aadi-Test"
TENANT_SLUG = "aadi-test"
DOMAIN = "aadi-test.example.com"
PASSWORD = "AadiTest@2026"  # shared, public, test only

# first name, full name, title, Tracker role, Academic role, what to try
PEOPLE = [
    ("prajakta", "Prajakta Kulkarni", "CEO", "owner", "admin", "Everything: whole company, money, people, roles, audit. Academic administrator and company administrator."),
    ("akanksha", "Akanksha Joshi", "Project Manager", "pm", "reviewer", "Her projects, team and delivery. Academic: approves lessons."),
    ("aarti", "Aarti Deshmukh", "Product Owner", "po", "content_operator", "Own products by effort and staffing (hours only). Academic: creates and edits lessons."),
    ("kalyani", "Kalyani Patil", "Team Lead", "tl", "content_operator", "Her team's work, hours only. Academic: creates lessons."),
    ("namrata", "Namrata More", "Software Engineer", "employee", "production_operator", "Employee: own tasks and timesheet. Academic: makes the videos."),
    ("aman", "Aman Shah", "Software Engineer", "employee", "viewer", "Employee. Academic: look only."),
    ("anjali", "Anjali Rao", "Software Engineer", "employee", "production_operator", "Employee. Academic: makes the videos."),
    ("prital", "Prital Naik", "Software Engineer", "employee", "viewer", "Employee. Academic: look only."),
    ("anushka", "Anushka Iyer", "QA Engineer", "employee", "viewer", "Employee on the bench. Academic: look only."),
    ("ankita", "Ankita Sawant", "Software Engineer", "employee", "viewer", "Employee on the bench. Academic: look only."),
    ("manasi", "Manasi Gokhale", "Marketing Lead", "tl", "viewer", "Team lead of marketing and sales, hours only."),
    ("gayatri", "Gayatri Pawar", "Marketing Executive", "employee", "viewer", "Employee in marketing."),
    ("jyoti", "Jyoti Menon", "Finance Manager", "finance", "viewer", "Budgets, contract values, invoices; no sprint or performance data."),
    ("dhande", "Dhande Kamat", "Delivery Manager", "ops", "viewer", "All projects and delivery; budgets but no contract value."),
    ("pravin", "Pravin Dixit", "Sales Lead", "sales", "viewer", "Assigned customers and contract values only."),
]


def email_of(first: str) -> str:
    return f"{first}@{DOMAIN}"


def seed(db: Session) -> dict:
    """Adds or refreshes the Aadi-Test company, its 15 people (all with the shared password) and its subscriptions. Repeating it changes nothing else."""
    t = db.get(m.Tenant, TENANT_ID)
    if t is None:
        t = m.Tenant(id=TENANT_ID, name=TENANT_NAME, slug=TENANT_SLUG, status="active")
        db.add(t)
        db.flush()
    pw = hash_password(PASSWORD)
    created = 0
    for first, name, _title, _tr, _ar, _hint in PEOPLE:
        u = db.execute(select(m.User).where(m.User.email == email_of(first))).scalar_one_or_none()
        if u is None:
            u = m.User(email=email_of(first), name=name, password_hash=pw, status="active")
            db.add(u)
            db.flush()
            created += 1
        else:
            u.name, u.password_hash, u.status, u.failed_logins, u.locked_until = name, pw, "active", 0, None
        mem = db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id, m.TenantMember.user_id == u.id)).scalar_one_or_none()
        if mem is None:
            db.add(m.TenantMember(tenant_id=t.id, user_id=u.id, is_admin=first == "prajakta"))
        else:
            mem.status, mem.is_admin = "active", first == "prajakta"
    subs = []
    for key in ("tracker", "academics", "video-engine"):
        if db.get(m.Product, key) is None:
            continue  # a product that is not registered here yet (for example on a fresh laptop before its first start)
        s = db.execute(select(m.Subscription).where(m.Subscription.tenant_id == t.id, m.Subscription.product_key == key)).scalar_one_or_none()
        if s is None:
            db.add(m.Subscription(tenant_id=t.id, product_key=key, status="active"))
        else:
            s.status, s.valid_until = "active", None
        subs.append(key)
    db.flush()
    for first, *_ in PEOPLE:  # everyone is registered in every product the company has (what the switcher shows)
        u = db.execute(select(m.User).where(m.User.email == email_of(first))).scalar_one()
        for key in subs:
            if key == "video-engine":
                continue
            exists = db.execute(select(m.MemberProduct).where(m.MemberProduct.tenant_id == t.id, m.MemberProduct.user_id == u.id, m.MemberProduct.product_key == key)).scalar_one_or_none()
            if exists is None:
                db.add(m.MemberProduct(tenant_id=t.id, user_id=u.id, product_key=key, status="active", source="admin", created_by="seed"))
            else:
                exists.status = "active"
    db.commit()
    return {"tenant": TENANT_ID, "people": len(PEOPLE), "new_people": created, "subscriptions": subs}


def wipe(db: Session) -> dict:
    """Deletes every company, person, session, token, event and audit entry. Kept: products, sign-in clients, service clients, signing keys and the
    platform administrators (so the platform can still be administered). Nothing is sent to the products: this is a direct clean-up."""
    counts = {}
    for model in (m.Outbox, m.ImportMap, m.MemberProduct, m.TenantMember, m.Subscription, m.Tenant, m.AuthCode, m.RefreshToken, m.LoginSession, m.AuthToken, m.AuditLog):
        counts[model.__tablename__] = db.execute(delete(model)).rowcount
    counts["users"] = db.execute(delete(m.User).where(m.User.is_platform_admin.is_(False))).rowcount
    for admin in db.execute(select(m.User).where(m.User.is_platform_admin.is_(True))).scalars():
        admin.token_epoch += 1  # old tokens of the kept administrators stop working
    db.commit()
    return counts


def maintenance_once(db: Session, maintenance_id: str) -> dict | None:
    """Wipe and seed, once per ``maintenance_id``: restarting with the same id does nothing. Returns what was done, or None when already done."""
    from .service import audit

    done = db.execute(select(m.AuditLog).where(m.AuditLog.action == "maintenance.reset_and_seed")).scalars().all()
    if any((a.detail or {}).get("id") == maintenance_id for a in done):
        return None
    counts = wipe(db)
    seeded = seed(db)
    audit(db, "maintenance.reset_and_seed", None, TENANT_ID, id=maintenance_id, wiped=counts, seeded=seeded)
    db.commit()
    return {"wiped": counts, "seeded": seeded}
