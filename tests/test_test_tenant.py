"""The Aadi-Test company: seed, wipe, one-time maintenance and the sign-in page list."""
import os

from sqlalchemy import select

from app import models as m, test_tenant as tt
from app.config import get_settings
from app.security import hash_password
from conftest import PW, login


def products(db):
    for key, name in (("tracker", "Project Tracker"), ("academics", "Academic Video Creation")):
        db.add(m.Product(key=key, name=name, initiate_url=f"https://{key}.example.com/login"))
    db.add(m.Product(key="video-engine", name="Video Engine", kind="service"))
    db.commit()


def test_seed_creates_the_company_and_is_repeatable(db):
    products(db)
    out = tt.seed(db)
    assert (out["people"], out["new_people"], out["subscriptions"]) == (15, 15, ["tracker", "academics", "video-engine"])
    again = tt.seed(db)
    assert again["new_people"] == 0
    assert db.query(m.User).count() == 15 and db.query(m.TenantMember).count() == 15
    admins = [x for x in db.execute(select(m.TenantMember).where(m.TenantMember.is_admin.is_(True))).scalars()]
    assert len(admins) == 1 and db.get(m.User, admins[0].user_id).email == "prajakta@aadi-test.example.com"
    assert not any(u.is_platform_admin for u in db.query(m.User))  # nobody here administers the platform
    assert db.query(m.MemberProduct).count() == 30  # 15 people x tracker + academics


def test_wipe_keeps_platform_administrators_products_and_keys(db):
    products(db)
    db.add(m.User(email="root@example.com", name="Root", password_hash=hash_password(PW), is_platform_admin=True))
    db.commit()
    tt.seed(db)
    counts = tt.wipe(db)
    assert counts["tenants"] == 1 and counts["users"] == 15
    assert [u.email for u in db.query(m.User)] == ["root@example.com"]
    assert db.query(m.Tenant).count() == 0 and db.query(m.Product).count() == 3 and db.query(m.SigningKey).count() >= 1


def test_maintenance_runs_once_per_id(db):
    products(db)
    tt.seed(db)
    first = tt.maintenance_once(db, "run-1")
    assert first and first["wiped"]["users"] == 15 and first["seeded"]["new_people"] == 15
    assert tt.maintenance_once(db, "run-1") is None  # the same id after a restart: nothing happens
    db.query(m.User).filter(m.User.email == "aman@aadi-test.example.com").delete()
    db.commit()
    assert tt.maintenance_once(db, "run-1") is None and db.query(m.User).count() == 14  # and it did not re-seed
    assert tt.maintenance_once(db, "run-2")["seeded"]["new_people"] == 15


def test_sign_in_page_lists_the_people_only_when_switched_on(client, db, monkeypatch):
    products(db)
    tt.seed(db)
    assert "Aadi-Test" not in client.get("/login").text
    monkeypatch.setenv("TEST_LOGINS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        page = client.get("/login").text
        assert "Test company: Aadi-Test" in page and tt.PASSWORD in page and "prajakta@aadi-test.example.com" in page
        assert page.count('class="testrow"') == 15
        r = client.post("/login", data={"email": "prajakta@aadi-test.example.com", "password": tt.PASSWORD, "next": "/", "csrf": client.cookies.get("id_csrf")})
        assert r.status_code == 303  # the one-click form signs in
    finally:
        monkeypatch.delenv("TEST_LOGINS_ENABLED")
        get_settings.cache_clear()


def test_the_list_needs_the_company_to_exist(client, monkeypatch):
    monkeypatch.setenv("TEST_LOGINS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        assert "Test company" not in client.get("/login").text
    finally:
        monkeypatch.delenv("TEST_LOGINS_ENABLED")
        get_settings.cache_clear()


def test_every_test_person_has_a_valid_email_and_the_password_meets_the_policy():
    from app.security import check_password_strength
    assert check_password_strength(tt.PASSWORD) is None
    assert len({f for f, *_ in tt.PEOPLE}) == 15 and all(e.endswith(tt.DOMAIN) for e in map(tt.email_of, (f for f, *_ in tt.PEOPLE)))
