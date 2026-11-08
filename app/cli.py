"""Operator commands:  python -m app.cli <command> --help"""
import argparse
import getpass
import os
import sys

from sqlalchemy import select

from . import models as m
from .db import Base, SessionLocal, engine
from .security import check_password_strength, ensure_signing_key, hash_password, hash_token, new_token


def create_admin(a):
    pw = os.environ.get("IDENTITY_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    weak = check_password_strength(pw)
    if weak:
        sys.exit(weak)
    with SessionLocal() as db:
        u = db.execute(select(m.User).where(m.User.email == a.email.lower())).scalar_one_or_none()
        if u:
            u.is_platform_admin, u.password_hash, u.status = True, hash_password(pw), "active"
        else:
            db.add(m.User(email=a.email.lower(), name=a.name, password_hash=hash_password(pw), is_platform_admin=True))
        db.commit()
    print("Platform admin ready:", a.email)


def register_product(a):
    with SessionLocal() as db:
        p = db.get(m.Product, a.key) or m.Product(key=a.key, name=a.name)
        p.name, p.description, p.initiate_url, p.active = a.name, a.description, a.initiate_url, True
        db.merge(p)
        db.commit()
    print("Product registered:", a.key)


def register_client(a):
    secret = None
    with SessionLocal() as db:
        c = db.get(m.OAuthClient, a.client_id) or m.OAuthClient(client_id=a.client_id, product_key=a.product, name=a.name or a.client_id)
        c.product_key, c.redirect_uris, c.post_logout_redirect_uris, c.active = a.product, a.redirect_uri, a.logout_uri, True
        if a.confidential and not c.secret_hash:
            secret = new_token()[0]
            c.secret_hash = hash_token(secret)
        db.merge(c)
        db.commit()
    print("Client registered:", a.client_id)
    if secret:
        print("Client secret (shown once, store it in the product's environment):", secret)


def import_tracker_cmd(a):
    from .importer import import_tracker

    with SessionLocal() as db:
        rep = import_tracker(db, a.source_url, a.product)
        print("\n".join(rep.lines()))
        if a.dry_run:
            db.rollback()
            print("Dry run: nothing was written.")
        else:
            db.commit()
            print("Imported.")


DEMO_PASSWORD = "Demo-Password-1"


def seed_demo(_):
    """Fictional companies and people so every screen can be shown. Refuses to run in production."""
    from .config import get_settings

    if get_settings().is_prod:
        sys.exit("seed-demo is for demo and test environments only (APP_ENV=demo).")
    with SessionLocal() as db:
        for key, name, desc in [("tracker", "Project Tracker", "Projects, timesheets and profitability"), ("dms", "Documents", "Versioned files and approvals"),
                                ("crm", "CRM", "Leads, deals and follow-ups"), ("social", "Aadi Social", "Brand kit and social publishing")]:
            iss = get_settings().issuer
            db.merge(m.Product(key=key, name=name, description=desc, initiate_url=f"{iss}/demo/{key}/login", active=True))
            cid = f"demo-{key}"
            db.merge(m.OAuthClient(client_id=cid, product_key=key, name=name + " (demo)", redirect_uris=[f"{iss}/demo/{key}/callback"],
                                   post_logout_redirect_uris=[f"{iss}/demo/{key}/signed-out"], active=True))
        db.commit()
        if not db.execute(select(m.User).where(m.User.email == "admin@demo.example.com")).scalar_one_or_none():
            db.add(m.User(email="admin@demo.example.com", name="Platform Admin", password_hash=hash_password(DEMO_PASSWORD), is_platform_admin=True))
            db.commit()
        tenants = {}
        for name, subs in [("Sunrise Logistics", ["tracker", "dms", "crm"]), ("Greenline Foods", ["tracker"]), ("Frostway Dairy", ["dms"])]:
            slug = name.lower().replace(" ", "-")
            t = db.execute(select(m.Tenant).where(m.Tenant.slug == slug)).scalar_one_or_none() or m.Tenant(name=name, slug=slug)
            db.add(t)
            db.flush()
            for k in subs:
                if not db.execute(select(m.Subscription).where(m.Subscription.tenant_id == t.id, m.Subscription.product_key == k)).scalar_one_or_none():
                    db.add(m.Subscription(tenant_id=t.id, product_key=k))
            tenants[slug] = t
        people = [("asha@demo.example.com", "Asha Rao", ["sunrise-logistics", "greenline-foods"]), ("rahul@demo.example.com", "Rahul Deshmukh", ["greenline-foods"]),
                  ("meera@demo.example.com", "Meera Iyer", ["frostway-dairy"]), ("priya@demo.example.com", "Priya Nair", ["sunrise-logistics"]),
                  ("nobody@demo.example.com", "Neha Kapoor", [])]
        for email, name, slugs in people:
            u = db.execute(select(m.User).where(m.User.email == email)).scalar_one_or_none()
            if not u:
                u = m.User(email=email, name=name, password_hash=hash_password(DEMO_PASSWORD))
                db.add(u)
                db.flush()
            for s_ in slugs:
                if not db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == tenants[s_].id, m.TenantMember.user_id == u.id)).scalar_one_or_none():
                    db.add(m.TenantMember(tenant_id=tenants[s_].id, user_id=u.id))
        db.commit()
    _seed_tracker_demo()
    print("Demo data ready. Sign in as asha@demo.example.com (two companies), rahul@... (one product), meera@... (one product), nobody@... (none). Password:", DEMO_PASSWORD)


def _register_tracker(url: str):
    """Registers Project Tracker as a product and its sign-in client. No people, companies or subscriptions are created."""
    with SessionLocal() as db:
        existing = db.get(m.Product, "tracker")
        secret = os.environ.get("TRACKER_WEBHOOK_SECRET") or (existing.webhook_secret if existing else "")
        db.merge(m.Product(key="tracker", name="Project Tracker", description="Projects, timesheets and profitability",
                           initiate_url=f"{url}/api/auth/sso/start", active=True,
                           webhook_url=f"{url}/api/auth/identity-events" if secret else "", webhook_secret=secret))
        db.merge(m.OAuthClient(client_id="tracker-web", product_key="tracker", name="Project Tracker", redirect_uris=[f"{url}/api/auth/sso/callback"],
                               post_logout_redirect_uris=[f"{url}/"], active=True))
        db.commit()


def bootstrap(_):
    """Runs at every start: registers Project Tracker when TRACKER_URL is set. Idempotent, safe in production."""
    url = os.environ.get("TRACKER_URL", "").rstrip("/")
    if url:
        _register_tracker(url)
        print("Project Tracker registered for", url)


def _seed_tracker_demo():
    """When TRACKER_URL is set, point the Project Tracker product at the real demo Project Tracker and add its demo people."""
    url = os.environ.get("TRACKER_URL", "").rstrip("/")
    if not url:
        return
    from .demo_people import DOMAIN, PEOPLE, TENANT_NAME, TENANT_SLUG

    _register_tracker(url)
    with SessionLocal() as db:
        t = db.execute(select(m.Tenant).where(m.Tenant.slug == TENANT_SLUG)).scalar_one_or_none() or m.Tenant(name=TENANT_NAME, slug=TENANT_SLUG)
        db.add(t)
        db.flush()
        if not db.execute(select(m.Subscription).where(m.Subscription.tenant_id == t.id, m.Subscription.product_key == "tracker")).scalar_one_or_none():
            db.add(m.Subscription(tenant_id=t.id, product_key="tracker"))
        for key, name in PEOPLE:
            email = f"{key}@{DOMAIN}"
            u = db.execute(select(m.User).where(m.User.email == email)).scalar_one_or_none()
            if not u:
                u = m.User(email=email, name=name, password_hash=hash_password(DEMO_PASSWORD))
                db.add(u)
                db.flush()
            mem = db.execute(select(m.TenantMember).where(m.TenantMember.tenant_id == t.id, m.TenantMember.user_id == u.id)).scalar_one_or_none()
            if not mem:
                db.add(m.TenantMember(tenant_id=t.id, user_id=u.id, is_admin=key == "priya.rao"))  # the CEO administers the company's people
            elif key == "priya.rao":
                mem.is_admin = True
        # one person who has an Aadi account but no Project Tracker user (shows the refusal)
        v = db.execute(select(m.User).where(m.User.email == f"visitor@{DOMAIN}")).scalar_one_or_none()
        if not v:
            v = m.User(email=f"visitor@{DOMAIN}", name="Vera Visitor", password_hash=hash_password(DEMO_PASSWORD))
            db.add(v)
            db.flush()
            db.add(m.TenantMember(tenant_id=t.id, user_id=v.id))
        db.commit()
    print("Project Tracker demo wired to", url)


def init_db(_):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        ensure_signing_key(db)
    print("Tables ready (use Alembic in production).")


def main():
    p = argparse.ArgumentParser(prog="app.cli")
    sp = p.add_subparsers(required=True)
    x = sp.add_parser("create-admin")
    x.add_argument("--email", required=True)
    x.add_argument("--name", default="Platform Admin")
    x.set_defaults(f=create_admin)
    x = sp.add_parser("register-product")
    x.add_argument("--key", required=True)
    x.add_argument("--name", required=True)
    x.add_argument("--initiate-url", required=True)
    x.add_argument("--description", default="")
    x.set_defaults(f=register_product)
    x = sp.add_parser("register-client")
    x.add_argument("--client-id", required=True)
    x.add_argument("--product", required=True)
    x.add_argument("--name")
    x.add_argument("--redirect-uri", action="append", required=True)
    x.add_argument("--logout-uri", action="append", default=[])
    x.add_argument("--confidential", action="store_true", help="issue a client secret (server-side apps)")
    x.set_defaults(f=register_client)
    x = sp.add_parser("import-tracker")
    x.add_argument("--source-url", required=True, help="Project Tracker database URL (read-only account recommended)")
    x.add_argument("--product", default="tracker")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(f=import_tracker_cmd)
    x = sp.add_parser("bootstrap")
    x.set_defaults(f=bootstrap)
    x = sp.add_parser("seed-demo")
    x.set_defaults(f=seed_demo)
    x = sp.add_parser("init-db")
    x.set_defaults(f=init_db)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
