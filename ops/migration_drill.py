"""Proves migrations on a throw-away PostgreSQL database:  python ops/migration_drill.py postgresql+psycopg://user:pw@host:port/postgres
1. a fresh database upgrades to head, 2. a database at the previous revision (live instances before Sprint 4) upgrades to head
without losing rows, 3. downgrading one step and upgrading again works."""
import os
import subprocess
import sys

import sqlalchemy as sa

admin = sys.argv[1]
base = admin.rsplit("/", 1)[0]
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(url, *args):
    env = {**os.environ, "DATABASE_URL": url}
    r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root, env=env, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout, r.stderr)
        raise SystemExit(f"alembic {' '.join(args)} failed")


def fresh(name):
    eng = sa.create_engine(admin, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(sa.text(f"drop database if exists {name}"))
        c.execute(sa.text(f"create database {name}"))
    return f"{base}/{name}"


# 1. fresh install
url = fresh("drill_fresh")
run(url, "upgrade", "head")
print("1. fresh install reaches head: ok")

# 2. upgrade an existing database that is at revision 0001 without the Sprint 4 additions
url = fresh("drill_old")
run(url, "upgrade", "0001")
eng = sa.create_engine(url)
with eng.begin() as c:
    c.execute(sa.text("drop table outbox"))
    c.execute(sa.text("alter table products drop column webhook_url, drop column webhook_secret"))
    c.execute(sa.text("insert into products (key, name, description, initiate_url, active) values ('tracker','Project Tracker','','https://t/x',true)"))
    c.execute(sa.text("insert into tenants (id, name, slug, status, created_at) values ('t1','Acme','acme','active', now())"))
run(url, "upgrade", "head")
with eng.begin() as c:
    assert c.execute(sa.text("select count(*) from products")).scalar() == 1
    assert c.execute(sa.text("select count(*) from tenants")).scalar() == 1
    cols = {r[0] for r in c.execute(sa.text("select column_name from information_schema.columns where table_name='products'"))}
    assert {"webhook_url", "webhook_secret"} <= cols
    assert c.execute(sa.text("select count(*) from outbox")).scalar() == 0
print("2. upgrade from the previous revision keeps data and adds the new parts: ok")

# 3. one step down and up again
run(url, "downgrade", "0001")
run(url, "upgrade", "head")
print("3. downgrade then upgrade: ok")
