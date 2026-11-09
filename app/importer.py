"""Import users, companies and memberships from Project Tracker into Identity, so existing credentials work at Identity.

Two ways to get the data, one set of rules:
  * read Project Tracker's database directly (command line: `import-tracker --source-url ...`), or
  * pull it over HTTPS from Project Tracker's guarded export (admin page: "Import people from Project Tracker").

Rules: idempotent (matched by e-mail and company slug, re-running changes nothing), never deletes anything in either system, carries password
hashes (bcrypt) unchanged so people keep their password (upgraded to Argon2id at first sign-in), creates a Project Tracker subscription for each
company, and NEVER copies Project Tracker's platform-administrator flag: nobody becomes an Identity administrator through an import.
Run with a dry run first; the report lists what would happen.
"""
import json
import urllib.request
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

    def as_dict(self) -> dict:
        return {"users_created": self.users_created, "users_updated": self.users_updated, "tenants_created": self.tenants_created,
                "tenants_updated": self.tenants_updated, "members_created": self.members_created,
                "subscriptions_created": self.subscriptions_created, "skipped": self.skipped}


def _link(db: Session, source: str, kind: str, source_id: str, identity_id: str):
    row = db.query(m.ImportMap).filter_by(source=source, kind=kind, source_id=source_id).first()
    if row:
        row.identity_id = identity_id
    else:
        db.add(m.ImportMap(source=source, kind=kind, source_id=source_id, identity_id=identity_id))


def read_tracker_db(source_url: str) -> dict:
    """Reads the three tables straight from a Project Tracker database."""
    src = create_engine(source_url)
    with src.connect() as c:
        return {
            "tenants": [dict(r) for r in c.execute(text("select id, name, slug, status from tenants")).mappings().all()],
            "users": [dict(r) for r in c.execute(text("select id, email, name, password_hash, status from users")).mappings().all()],
            "memberships": [dict(r) for r in c.execute(text("select mb.tenant_id, mb.user_id, mb.status, ro.key as role_key "
                                                            "from memberships mb join roles ro on ro.id = mb.role_id")).mappings().all()],
        }


def fetch_tracker_export(url: str, secret: str) -> dict:
    """Pulls the same data from Project Tracker's guarded export (needs the shared secret and the export switched on there)."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {secret}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def summarize(data: dict) -> list[dict]:
    """Companies in an export with how many people each has, so an administrator can choose what to bring across."""
    count: dict = {}
    for mb in data.get("memberships", []):
        count[mb["tenant_id"]] = count.get(mb["tenant_id"], 0) + 1
    return [{"slug": t["slug"], "name": t["name"], "people": count.get(t["id"], 0)} for t in data.get("tenants", [])]


def only(data: dict, slugs: list[str]) -> dict:
    """Keeps just the chosen companies, their memberships, and the people who belong to them."""
    tenants = [t for t in data.get("tenants", []) if t["slug"] in slugs]
    ids = {t["id"] for t in tenants}
    mems = [mb for mb in data.get("memberships", []) if mb["tenant_id"] in ids]
    uids = {mb["user_id"] for mb in mems}
    return {"tenants": tenants, "memberships": mems, "users": [u for u in data.get("users", []) if u["id"] in uids]}


def import_payload(db: Session, data: dict, product_key: str = "tracker", source: str = "tracker") -> Report:
    r = Report()
    tmap: dict = {}
    for t in data.get("tenants", []):
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
    umap: dict = {}
    for u in data.get("users", []):
        email = (u.get("email") or "").strip().lower()
        if not email:
            r.skipped.append(f"user {u['id']} has no e-mail")
            continue
        obj = db.query(m.User).filter_by(email=email).one_or_none()
        if obj:
            r.users_updated += 1
            if not obj.password_hash and u.get("password_hash"):
                obj.password_hash = u["password_hash"]
        else:
            status = u["status"] if u["status"] in ("active", "invited", "suspended") else "active"
            # is_platform_admin is deliberately left false: an import never creates an Identity administrator
            obj = m.User(email=email, name=u["name"], password_hash=u.get("password_hash") or "", status=status)
            db.add(obj)
            db.flush()
            r.users_created += 1
        _link(db, source, "user", str(u["id"]), obj.id)
        umap[u["id"]] = obj
    for mb in data.get("memberships", []):
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


def import_tracker(db: Session, source_url: str, product_key: str = "tracker", source: str = "tracker") -> Report:
    return import_payload(db, read_tracker_db(source_url), product_key, source)
