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
    c.execute(sa.text("insert into products (key, name, description, initiate_url, active, kind) values ('tracker','Project Tracker','','https://t/x',true,'product')"))
    c.execute(sa.text("insert into tenants (id, name, slug, status, created_at) values ('t1','Acme','acme','active', now())"))
run(url, "upgrade", "head")
with eng.begin() as c:
    assert c.execute(sa.text("select count(*) from products where key = 'tracker'")).scalar() == 1  # (0004 also seeds the video-engine service)
    assert c.execute(sa.text("select count(*) from tenants")).scalar() == 1
    cols = {r[0] for r in c.execute(sa.text("select column_name from information_schema.columns where table_name='products'"))}
    assert {"webhook_url", "webhook_secret"} <= cols
    assert c.execute(sa.text("select count(*) from outbox")).scalar() == 0
print("2. upgrade from the previous revision keeps data and adds the new parts: ok")

# 3. one step down and up again
run(url, "downgrade", "0001")
run(url, "upgrade", "head")
print("3. downgrade then upgrade: ok")

# 4. a production-like database: revision 0004 with the old VARCHAR secret columns and real secret data (revision 0005, secrets at rest)
url = fresh("drill_prod")
run(url, "upgrade", "0004")
eng = sa.create_engine(url)
with eng.begin() as c:
    c.execute(sa.text("alter table products alter column webhook_secret type varchar(100)"))
    c.execute(sa.text("alter table signing_keys alter column private_pem type varchar(4000)"))
    c.execute(sa.text("insert into products (key, name, description, initiate_url, active, webhook_url, webhook_secret, kind) values "
                      "('tracker','Project Tracker','','https://t/x',true,'https://t/hook','plain-secret-123','product')"))
    c.execute(sa.text("insert into signing_keys (kid, private_pem, active, published, created_at) values ('k1','-----BEGIN PRIVATE KEY-----x',true,true,now())"))
run(url, "upgrade", "head")
with eng.begin() as c:
    types = {r[0]: r[1] for r in c.execute(sa.text("select column_name, data_type from information_schema.columns where (table_name='products' and column_name='webhook_secret') or (table_name='signing_keys' and column_name='private_pem')"))}
    assert types == {"webhook_secret": "text", "private_pem": "text"}, types
    assert c.execute(sa.text("select webhook_secret from products where key='tracker'")).scalar() == "plain-secret-123"
    assert c.execute(sa.text("select private_pem from signing_keys where kid='k1'")).scalar().startswith("-----BEGIN")
print("4. production-like database (0004, varchar secrets, real data) upgrades to text columns without changing any value: ok")
run(url, "downgrade", "0004")
run(url, "upgrade", "head")
print("5. 0005 down and up again on that database: ok")
