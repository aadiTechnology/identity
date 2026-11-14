"""End-to-end proof of P2-S6: the product template signs people in through a REAL running Identity, refuses unknown people, reads the company's plan, and
stops a suspended person on their next request because Identity's event reached it. Needs the staging gate running (ops/staging_gate.ps1).

  python e2e_template.py prepare   (Identity's venv)   registers the product 'template-demo', its client, a service client, a plan and the webhook
  python e2e_template.py run       (Playwright venv)   drives a real browser and prints a transcript; exit 1 on any failed step
The template itself is started by the caller on port 8400 (see start_template() in record_p2s6.py)."""
import sys
import time
from pathlib import Path

TENANT = "5ca1ab1e-aad1-4e57-8000-000000000001"
APP = "http://localhost:8400"
ID = "http://localhost:8010"
HOOK = "template-webhook-secret"
SVC = "template-service-secret"
SESSION = "template-session-secret-at-least-32-chars"
ENV = {"PRODUCT_KEY": "template-demo", "IDENTITY_ISSUER": ID, "IDENTITY_CLIENT_ID": "template-demo-web", "APP_BASE_URL": APP, "SESSION_SECRET": SESSION,
       "IDENTITY_WEBHOOK_SECRET": HOOK, "IDENTITY_SERVICE_CLIENT_ID": "template-identity", "IDENTITY_SERVICE_CLIENT_SECRET": SVC, "APP_ENV": "development",
       "ALLOWED_EMAILS": "prajakta@aadi-test.example.com,gayatri@aadi-test.example.com"}


def prepare():
    sys.path.insert(0, str(Path(r"C:\Users\lenovo\AadiTech\Product\Aadi-Product\identity")))
    from app import models as m
    from app.db import SessionLocal
    from app.security import hash_token
    with SessionLocal() as db:
        db.merge(m.Product(key="template-demo", name="Template Demo", description="Product built from the Aadi template", initiate_url=f"{APP}/auth/login", active=True,
                           webhook_url=f"{APP}/identity/events", webhook_secret=HOOK, kind="product"))
        db.flush()
        db.merge(m.OAuthClient(client_id="template-demo-web", product_key="template-demo", name="Template Demo", secret_hash="", redirect_uris=[f"{APP}/auth/callback"],
                               post_logout_redirect_uris=[f"{APP}/"], active=True))
        db.merge(m.ServiceClient(client_id="template-identity", name="Template to Identity", product_key="template-demo", secret_hash=hash_token(SVC),
                                 audiences=["identity"], scopes=["identity:entitlements", "identity:seats"], active=True))
        if not db.query(m.Subscription).filter_by(tenant_id=TENANT, product_key="template-demo").first():
            db.add(m.Subscription(tenant_id=TENANT, product_key="template-demo", status="active"))
        db.commit()
    print("prepared: product, OAuth client, service client and subscription for template-demo")


def start():
    """Start the template on port 8400 (replacing any earlier one), detached, logging to the folder given as argv[2]."""
    import os
    import subprocess
    import urllib.request
    root = Path(r"C:\Users\lenovo\AadiTech\Product\Aadi-Product\aadi-product-template")
    subprocess.run(["powershell", "-NoProfile", "-Command", "Get-NetTCPConnection -LocalPort 8400 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"],
                   capture_output=True)
    log = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
    out = open(log / "template.log", "w")
    subprocess.Popen([str(root / ".venv/Scripts/python.exe"), "-m", "uvicorn", "app.main:factory", "--factory", "--port", "8400"], cwd=str(root), env={**os.environ, **ENV},
                     stdout=out, stderr=out, creationflags=0x00000008 | 0x00000200)
    for _ in range(40):
        try:
            if urllib.request.urlopen(f"{APP}/health", timeout=2).status == 200:
                print("template started on", APP)
                return
        except Exception:  # noqa: BLE001
            time.sleep(1)
    sys.exit("the template did not start")


def run():
    from playwright.sync_api import sync_playwright
    failed = []

    def check(name, ok, detail=""):
        print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail else ""), flush=True)
        if not ok:
            failed.append(name)

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        ann, boss = b.new_context(), b.new_context()
        a, s = ann.new_page(), boss.new_page()

        def sign_in(page, first):
            page.goto(f"{APP}/")
            page.click("#signin")
            page.wait_for_selector(".testbtn")
            page.click(f".testbtn:has-text('{first}')")
            page.wait_for_load_state("networkidle")

        sign_in(a, "Gayatri")
        check("sign in through Aadi Identity lands back in the product", a.url.rstrip("/") == APP and a.locator("#who").count() == 1, a.url)
        check("the page names the person and the company", "Gayatri" in a.inner_text("#who") and "aadi-test" in a.inner_text("#who"), a.inner_text("#who")[:80])
        check("the company's plan features are read from Identity", "campaigns.basic" in a.inner_text("#plan") or "none" in a.inner_text("#plan"), a.inner_text("#plan"))
        me = a.request.get(f"{APP}/api/me")
        check("the API knows the person", me.status == 200 and me.json()["company_slug"], f"HTTP {me.status}")

        c = b.new_context()
        d = c.new_page()
        sign_in(d, "Aman")
        check("a person Identity vouches for but the product has no record of is refused", d.locator("#who").count() == 0 and "sign_in_error=no_access" in d.url, d.url)
        check("...and no session was created", d.request.get(f"{APP}/api/me").status == 401)

        # the company administrator suspends Gayatri in Identity; Identity's event must reach the product
        s.goto(f"{ID}/login")
        s.click(".testbtn:has-text('Prajakta')")
        s.wait_for_load_state("networkidle")
        s.goto(f"{ID}/company")
        s.wait_for_selector("#members .row")
        row = s.locator("#members .row", has_text="Gayatri Pawar")
        row.get_by_role("button", name="Suspend").click()
        s.wait_for_timeout(1500)
        t0 = time.time()
        status = 200
        while time.time() - t0 < 45 and status == 200:  # the event worker runs every few seconds
            status = a.request.get(f"{APP}/api/me").status
            a.wait_for_timeout(1500)
        check("a suspension in Identity stops the person's next request in the product", status == 403, f"HTTP {status} after {time.time() - t0:.0f}s")
        a.reload()
        check("...the same valid session now sees the sign-in page", a.locator("#who").count() == 0)
        b.close()
    print("\nALL TEMPLATE E2E STEPS PASSED" if not failed else f"\nFAILED: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    {"prepare": prepare, "start": start, "run": run}[sys.argv[1]]()
