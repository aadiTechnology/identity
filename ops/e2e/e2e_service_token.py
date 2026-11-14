"""End-to-end proof of P2-S3 against a RUNNING local Identity (the staging gate): a real Project Tracker client enrols and removes a person with a
service token, reads seats and the snapshot, and the old shared secret still works. Prints a transcript; exit code 1 on any failed step.

  step A (Identity venv):  python e2e_service_token.py prepare     creates the service client in Identity's database and prints its secret to a file
  step B (Tracker venv):   python e2e_service_token.py run         runs the transcript through app.identity_client.IdentityClient
"""
import json
import sys
from pathlib import Path

ID = "http://localhost:8010"
TENANT = "5ca1ab1e-aad1-4e57-8000-000000000001"  # Aadi-Test
STATE = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("e2e_state.json")
LEGACY_SECRET = "e2e-legacy-product-secret"
SVC_SECRET = "e2e-service-client-secret"


def prepare():
    sys.path.insert(0, str(Path(r"C:\Users\lenovo\AadiTech\Product\Aadi-Product\identity")))
    from app import models as m
    from app.db import SessionLocal
    from app.security import hash_token
    with SessionLocal() as db:
        db.merge(m.ServiceClient(client_id="tracker-identity", name="Tracker to Identity (e2e)", product_key="tracker", secret_hash=hash_token(SVC_SECRET),
                                 audiences=["identity"], scopes=["identity:enrol", "identity:remove", "identity:seats", "identity:snapshot", "identity:entitlements"], active=True))
        db.get(m.Product, "tracker").webhook_secret = LEGACY_SECRET
        db.commit()
    print("prepared: service client tracker-identity created; legacy secret set on the tracker product")


def run():
    sys.path.insert(0, str(Path(r"C:\Users\lenovo\AadiTech\Product\Aadi-Product\ProjectTracking\backend")))
    import httpx
    from app.identity_client import IdentityClient
    failed = []

    def check(name, ok, detail=""):
        print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail else ""))
        if not ok:
            failed.append(name)

    via_token = IdentityClient(ID, "", service_id="tracker-identity", service_secret=SVC_SECRET)
    via_secret = IdentityClient(ID, LEGACY_SECRET)
    check("token mode is on, no product secret configured", via_token.token_mode and not via_token.secret)
    r = via_token.enrol(TENANT, "e2e.token@aadi-test.example.com", "E2E Token", "e2e")
    check("enrol with a service token", r["ok"] and r.get("created") is True, str(r)[:120])
    check("registered only in Tracker", r.get("registered_products") == ["tracker"], str(r.get("registered_products")))
    seats = httpx.get(f"{ID}/api/v1/products/seats", params={"tenant_id": TENANT}, headers={"Authorization": "Bearer " + via_token._token("identity:seats", TENANT)[0]}).json()
    check("seats with a service token (v1)", seats.get("product") == "tracker" and seats.get("used", 0) >= 1, str(seats))
    snap = httpx.get(f"{ID}/api/v1/sync/snapshot", params={"product": "tracker"}, headers={"Authorization": "Bearer " + httpx.post(f"{ID}/token", data={
        "grant_type": "client_credentials", "client_id": "tracker-identity", "client_secret": SVC_SECRET, "audience": "identity", "scope": "identity:snapshot"}).json()["access_token"]}).json()
    check("snapshot with a company-free token", snap.get("product") == "tracker" and any(t["id"] == TENANT for t in snap["tenants"]), f"{len(snap.get('tenants', []))} companies")
    rm = via_token.remove(TENANT, "e2e.token@aadi-test.example.com", "e2e")
    check("remove with a service token", rm["ok"] and rm.get("registered_products") == [], str(rm)[:120])
    r2 = via_secret.enrol(TENANT, "e2e.secret@aadi-test.example.com", "E2E Secret", "e2e")
    check("the old shared secret still works", r2["ok"], str(r2)[:120])
    bad = IdentityClient(ID, "", service_id="tracker-identity", service_secret="wrong").enrol(TENANT, "x@aadi-test.example.com", "X", "e2e")
    check("a wrong service secret is refused", bad["ok"] is False, bad["message"][:80])
    other = httpx.post(f"{ID}/api/v1/products/enrol", json={"tenant_id": "00000000-0000-0000-0000-000000000000", "email": "y@aadi-test.example.com"},
                       headers={"Authorization": "Bearer " + via_token._token("identity:enrol", TENANT)[0]})
    check("a token for one company cannot act on another", other.status_code == 403, f"HTTP {other.status_code}")
    print("\nALL E2E STEPS PASSED" if not failed else f"\nFAILED: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    {"prepare": prepare, "run": run}[sys.argv[1]]()
