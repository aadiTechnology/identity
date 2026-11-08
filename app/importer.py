"""Import users, companies and memberships from a Project Tracker database into Identity.

Idempotent: re-running updates the same records (matched by e-mail and company slug) and never deletes anything in either system.
Password hashes (bcrypt) are carried over unchanged and upgraded to Argon2id on each person's first sign-in.
Run with --dry-run first; the report lists what would happen.
"""
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from . import models as m


@dataclass
class Report:
    users_created: int = 0
    users_updated: int = 0
    tenants_created: int = 0
    tenants_updated: int = 0
    members_created: int = 0
    subscriptions_created: int = 0
    skipped: list = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"users: {self.users_created} new, {self.users_updated} existing",
               f"companies: {self.tenants_created} new, {self.tenants_updated} existing",
               f"memberships created: {self.members_created}",
               f"tracker subscriptions created: {self.subscriptions_created}"]
        out += [f"skipped: {s}" for s in self.skipped]
        return out


def _link(db: Session, source: str, kind: str, source_id: str, identity_id: str):
    row = db.query(m.ImportMap).filter_by(source=source, kind=kind, source_id=source_id).first()
    if row:
        row.identity_id = identity_id
    else:
        db.add(m.ImportMap(source=source, kind=kind, source_id=source_id, identity_id=identity_id))


def import_tracker(db: Session, source_url: str, product_key: str = "tracker", source: str = "tracker") -> Report:
    r = Report()
    src = create_engine(source_url)
    with src.connect() as c:
        tenants = c.execute(text("select id, name, slug, status from tenants")).mappings().all()
        users = c.execute(text("select id, email, name, password_hash, is_platform_admin, status from users")).mappings().all()
        members = c.execute(text("select mb.tenant_id, mb.user_id, mb.status, ro.key as role_key "
                                 "from memberships mb join roles ro on ro.id = mb.role_id")).mappings().all()
    tmap: dict[int, m.Tenant] = {}
    for t in tenants:
        obj = db.query(m.Tenant).filter_by(slug=t["slug"]).one_or_none()
        if obj:
            r.tenants_updated += 1
            obj.name = t["name"]
        else:
            status = t["status"] if t["status"] in ("active", "trial", "suspended") else "active"
            obj = m.Tenant(name=t["name"], slug=t["slug"], status=status)
            db.add(obj)
            db.flush()
            r.tenants_created += 1
        _link(db, source, "tenant", str(t["id"]), obj.id)
        tmap[t["id"]] = obj
        if not db.query(m.Subscription).filter_by(tenant_id=obj.id, product_key=product_key).first():
            db.add(m.Subscription(tenant_id=obj.id, product_key=product_key,
                                  status="suspended" if obj.status == "suspended" else "active"))
            r.subscriptions_created += 1
    umap: dict[int, m.User] = {}
    for u in users:
        email = (u["email"] or "").strip().lower()
        if not email:
            r.skipped.append(f"user {u['id']} has no e-mail")
            continue
        obj = db.query(m.User).filter_by(email=email).one_or_none()
        if obj:
            r.users_updated += 1
            if not obj.password_hash and u["password_hash"]:
                obj.password_hash = u["password_hash"]
        else:
            status = u["status"] if u["status"] in ("active", "invited", "suspended") else "active"
            obj = m.User(email=email, name=u["name"], password_hash=u["password_hash"] or "",
                         is_platform_admin=bool(u["is_platform_admin"]), status=status)
            db.add(obj)
            db.flush()
            r.users_created += 1
        _link(db, source, "user", str(u["id"]), obj.id)
        umap[u["id"]] = obj
    for mb in members:
        t, u = tmap.get(mb["tenant_id"]), umap.get(mb["user_id"])
        if not t or not u:
            r.skipped.append(f"membership tenant={mb['tenant_id']} user={mb['user_id']} has no match")
            continue
        if not db.query(m.TenantMember).filter_by(tenant_id=t.id, user_id=u.id).first():
            status = mb["status"] if mb["status"] in ("active", "suspended") else "active"
            db.add(m.TenantMember(tenant_id=t.id, user_id=u.id, is_admin=mb["role_key"] == "owner", status=status))
            r.members_created += 1
    db.flush()
    return r
