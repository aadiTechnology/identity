"""End-to-end check of P2-S5 against a RUNNING local Identity: the real Project Tracker client reads what Identity says the company may use.
Run with Tracker's venv, after `e2e_service_token.py prepare` (service client) and a plan assigned in the admin console.

  python e2e_entitlements.py <expected plan key> <expected features, comma separated> [expected limits as k=v,k=v]
Exit code 1 if the answer differs. Prints a transcript."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\lenovo\AadiTech\Product\Aadi-Product\ProjectTracking\backend")))
import httpx  # noqa: E402
from app.identity_client import IdentityClient  # noqa: E402

ID = "http://localhost:8010"
TENANT = "5ca1ab1e-aad1-4e57-8000-000000000001"
want_plan = sys.argv[1]
want_features = sorted(x for x in sys.argv[2].split(",") if x)
want_limits = {k: (None if v == "" else int(v)) for k, v in (p.split("=") for p in sys.argv[3].split(",") if len(sys.argv) > 3 and p)} if len(sys.argv) > 3 else None

failed = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        failed.append(name)


client = IdentityClient(ID, "", service_id="tracker-identity", service_secret="e2e-service-client-secret")
a = client.entitlements(TENANT)
check("Tracker reads its entitlement with a service token", a is not None and a["entitled"] is True, f"entitled={a and a['entitled']}")
check("plan", a["plan"] and a["plan"]["key"] == want_plan, f"{a['plan']}")
check("features", a["features"] == want_features, ", ".join(a["features"]) or "none")
if want_limits is not None:
    check("limits", a["limits"] == want_limits, str(a["limits"]))
check("it only learns about its own product", a["product"] == "tracker", a["product"])
again = client.entitlements(TENANT)
check("a second read comes from the cache (same answer, no new request)", again is a)
old = httpx.get(f"{ID}/api/v1/entitlements", params={"tenant_id": TENANT}, headers={"Authorization": "Bearer e2e-legacy-product-secret"}).json()
check("the old shared secret gets the same answer", old == a)
other = httpx.get(f"{ID}/api/v1/entitlements", params={"tenant_id": "00000000-0000-0000-0000-000000000000"},
                  headers={"Authorization": "Bearer " + client._token("identity:entitlements", TENANT)[0]})
check("a token for one company cannot read another", other.status_code == 403, f"HTTP {other.status_code}")
print("\nALL ENTITLEMENT CHECKS PASSED" if not failed else f"\nFAILED: {failed}")
sys.exit(1 if failed else 0)
