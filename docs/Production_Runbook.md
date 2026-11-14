# Aadi Identity: Production Runbook

For the people who run Identity. Everything here was exercised except where marked *not yet exercised*.

## 1. What runs where

| | Real | Demo |
|---|---|---|
| Address | `https://id.aadiprojecttracker.aaditechnology.com` | `https://id-demo.aadiprojecttracker.aaditechnology.com` |
| Coolify resource | `aadi-identity` | `aadi-identity-demo` |
| Mode | `APP_ENV=production` (refuses unsafe settings) | `APP_ENV=demo` (fictional data, demo pages, no real mail) |
| Database | its own PostgreSQL volume | its own PostgreSQL volume |
| Used by | real customers (none yet) | videos, trials, Project Tracker demo |

Deploy or update: `python deploy/coolify_deploy.py` (real) or `python deploy/coolify_deploy.py --demo`. The scripts read git-ignored `.env.coolify` / `.env.coolify.demo`; never commit those.

## 2. Settings that matter

| Variable | Meaning | Production rule |
|---|---|---|
| `ISSUER` | the public address, in every token | must be `https://` |
| `COOKIE_SECURE` | send cookies only over https | must be `true` |
| `DATABASE_URL` | PostgreSQL | must not be the development default |
| `EMAIL_BACKEND` | `smtp` sends invitations and reset links | must be `smtp` (a `file` value would silently drop mail) |
| `SIGNING_KEY_PEM` | optional fixed RSA key | empty = generated once and kept in the database |
| `TRACKER_URL`, `TRACKER_WEBHOOK_SECRET` | register Project Tracker and its event delivery at start | set together |
| `LOGIN_RATE_PER_MINUTE` (30), `TOKEN_RATE_PER_MINUTE` (600) | per-address limits | leave the defaults |

If a production start has an unsafe value the service stops with a message listing what to fix. That is intended.

## 3. First day on the real instance (one time)

1. Open the `aadi-identity` resource in Coolify, **Terminal**, and run `python -m app.cli create-admin --email you@company.com --name "Your Name"`. Choose the password at the prompt (10+ characters, mixed case, a number).
2. Sign in at `/login`, open `/admin`. Add each customer company with its owner's email and tick the products. The owner receives an invitation to set a password.
3. The owner (a company administrator) manages their own people at `/company`.
4. Confirm the status panel on `/admin` shows database `ok`, 0 failed events, one published signing key.

## 4. Connecting Project Tracker for real (pilot, then everyone)

Do this for **one company first**.

1. **Identity side:** on the real instance, set `TRACKER_URL=https://<tracker address>` and a new random `TRACKER_WEBHOOK_SECRET` in Coolify and redeploy. This registers the Project Tracker product, its sign-in client and event delivery.
2. **Project Tracker side:** set `AUTH_MODE=both`, `IDENTITY_ISSUER=https://id.aadiprojecttracker.aaditechnology.com`, `IDENTITY_CLIENT_ID=tracker-web`, `IDENTITY_WEBHOOK_SECRET=<the same secret>`, `IDENTITY_RECONCILE_HOURS=24`, and redeploy. People can still use the old password form.
3. **People:** take a read-only copy of the Project Tracker database and run `python -m app.cli import-tracker --source-url <read-only url> --dry-run`, read the report (users, companies, memberships, skipped lines), then run it without `--dry-run`. It keeps existing passwords. Or add people by hand at `/admin` and `/company`. Match is by email; a person is linked on their first Aadi sign-in.
4. **Check with the pilot company:** each person signs in with the Aadi button; roles and screens are unchanged; suspend a test person at `/company` and confirm Project Tracker refuses them within seconds; reactivate.
5. **Everyone:** repeat for the other companies. Keep `both` for at least one full release cycle. Move to `platform` (Aadi sign-in only; old password endpoints switch off) only when nobody still needs the old form. Platform administrators of Project Tracker keep using the old form.

**Rollback at any point:** set `AUTH_MODE=local` in Project Tracker and redeploy. Nothing in Project Tracker's data changes; the extra columns are simply unused.

## 5. Daily and weekly checks

- `/health` (is it up) and `/health/ready` (database answers, a signing key exists) for an uptime monitor. Use `/health/ready` for alerts.
- `/admin` Health panel: **events waiting** should be near 0 and **failed** exactly 0. *Waiting and growing* means Project Tracker is unreachable or the secret differs. *Failed* means 8 retries over about an hour were used up: fix the cause, then run Project Tracker's reconcile (`python -m app.identity_sync` inside its container) to catch up.
- Housekeeping (old sessions, codes, tokens, delivered events) runs by itself every 24 hours; the admin API `POST /admin/api/maintenance/prune` runs it now.

## 6. Backups

- **Schedule:** daily. `DB=identity PGUSER=identity PG_EXEC="docker exec -i <postgres container>" ./ops/backup.sh 14` writes a compressed dump and keeps the newest 14. Run it from cron on the Coolify server (or use Coolify's scheduled database backup if your version offers it for compose databases) and copy the dumps **off the server** (object storage). Not yet scheduled on your server: that needs your decision on where copies go.
- **Prove it:** `./ops/restore_drill.sh` restores the newest dump into a scratch database and compares row counts with the source. Exercised on the load-test database: all 8 tables matched. Repeat after any change to backups and at least quarterly.
- **A real restore:** stop the app container, `pg_restore --clean --if-exists -d identity < dump` inside the database container, start the app. Tokens issued before the dump remain valid because the signing keys are in the dump.
- The signing key is in the database. A backup is therefore as sensitive as the database: store dumps encrypted.

## 7. Rotating the signing key

When: every 6 to 12 months, and immediately if the database or a backup might have leaked.

1. `/admin` > **Rotate signing key** (two clicks), or `python -m app.cli rotate-key`.
2. New tokens use the new key at once. The old key stays published, so tokens already issued keep working until they expire (15 minutes); the next refresh issues new tokens under the new key.
3. Products fetch the key list again automatically when they see an unknown key id. Project Tracker's behaviour is covered by a test.
4. *Emergency* (key leaked): rotate, then sign everyone out (admin API `POST /admin/api/users/{id}/revoke` per person, or reset the database's `sessions` and `refresh_tokens`), and treat tokens issued before the rotation as untrusted. *Not yet exercised on the real instance.*

## 8. When something goes wrong

| Situation | What happens | What to do |
|---|---|---|
| Identity is down | Project Tracker sessions already open keep working until they end (60 minutes). New Aadi sign-ins fail. In `both` mode the old form still works. | Restart the resource; if it will not come back, set `AUTH_MODE=local` or keep `both` until it does. |
| Events not arriving | Suspensions take effect on the next daily reconcile instead of at once. | Check Health panel; fix the secret or address; run reconcile. |
| A person cannot sign in | Wrong password five times locks the account for 15 minutes. | Wait, or reset the password. There is no unlock-by-admin yet. |
| Someone must be cut off now | | `/company` > Suspend (company) or the admin API `users/{id}/status` > suspended (everywhere). Project Tracker refuses them on their next request. |
| A client secret or webhook secret leaked | | Register the client again with a new secret / set a new webhook secret on both sides and redeploy. |
| Database lost | | Restore the newest dump (section 6) and redeploy. |

## 9. Capacity (measured)

Local run, one server process, PostgreSQL, 30 people at the same time for 20 seconds, repeating sign-in flows: **2,002 requests, 98 per second, 0 errors**. Typical response times: authorize 190 ms, token 270 ms, userinfo 150 ms (95th percentile 210 to 410 ms); first sign-in 0.8 s because 30 people hash a password at once. Single-person latency: token 20 ms, userinfo 6 ms. One process is enough for many hundreds of daily users. Beyond that, add processes only after moving the rate limits and the event worker to shared storage (see the security review).
Reproduce with `python ops/loadtest.py http://localhost:8802 --users 30 --seconds 20` against a demo or local instance only.

## 10. Open decisions for the business owner

1. Where nightly backups are copied, and who receives the alert when one fails.
2. Whether to monitor `/health/ready` with an external uptime service (recommended).
3. Whether administrators must have a second sign-in step (multi-factor). Not built.
4. Seat limits per subscription: the number is stored but not enforced.
5. Token lifetimes (access 15 minutes, refresh 14 days, Project Tracker session 60 minutes).

## Service tokens (shared services, e.g. the Video Engine)

A product's **server** gets a short-lived token to call a shared service for one company. People never use this.

- **Request:** `POST /token` with `grant_type=client_credentials`, the client id and secret (form fields or HTTP Basic), `audience` (the service key, e.g. `video-engine`), `scope` (space separated, e.g. `video:create video:read`) and `tenant_id` (the company the call is for).
- **Token:** RS256, header `typ: at+jwt`, life 10 minutes (`SERVICE_TOKEN_MINUTES`), claims `iss, aud, sub, client_id, scope, tenant_id, caller_product, jti, iat, exp`. Verify with the JWKS at `/.well-known/jwks.json`. No refresh token: ask again.
- **Refused unless all hold:** client active and secret right; the service is registered (`kind=service`) and allowed for that client; scopes are a subset of the client's; the company exists and is active; **the company has a live subscription to the calling product and to the service** (stops a product asking for a company that does not use it). Refusals are audited (`service_token.denied`); issues too (`service_token.issued`).
- **Set up:** the `video-engine` service exists after the migration. In `/admin`: turn the "Video Engine (service)" chip on for each company, then create a client under "Service clients" (the secret is shown once). Rotate a secret or disable a client there; the old secret stops working at once, tokens already issued live out their 10 minutes.
- **CLI:** `python -m app.cli register-service --key <key> --name <name>` registers another service.

## 9. Backups, monitoring and the staging gate (sprint P2-S1, 2026-11-14)

**What is live on Coolify** (scheduled tasks, created through the Coolify API):

| Application | Task | When (server time) | What it does |
|---|---|---|---|
| aadi-identity | `db-backup` | 03:00 daily | `pg_dumpall` of the whole PostgreSQL server in the Identity stack (databases Identity, Academics, Video Engine), gzip, keeps the newest 14, in `/var/lib/postgresql/data/_b` |
| aadi-project-tracker, aadi-crm, d-m-s (DMS) | `db-backup` | 03:00 daily | same, for each product's own server |
| all four | `db-backup-check` | 03:30 daily | exits with an error (task shows as failed in Coolify) when the newest backup is missing, under 10 KB or older than 25 hours |

Each task was run once and its result checked on 2026-11-14 (a positive check passed and a negative control failed as expected).

**Known limits (deviations D-1, D-2).** The dumps are on the same server and volume as the data. They protect against mistakes, bad migrations and a dropped database, NOT against loss of the server or volume. Copy them off the server (needs shell or S3 access, not available through the Coolify API) before relying on them for disaster recovery. The shared PostgreSQL still lives inside the Identity stack; moving Academics and the engine to the standalone server also needs shell access.

**Restore.** `ops/restore_drill_all.sh <container> <superuser> [dump.gz]` restores a `pg_dumpall` dump into a scratch container and compares row counts of every table in every database. It refuses to pass on an empty dump, a missing container or zero tables. Last run: 67 tables matched (local engine databases).
To restore production: take the newest `a-*.gz` from the server, `gunzip -c a-....gz | psql -U <superuser> postgres` into a NEW container first, check, then switch the application.

**Uptime monitor.** `.github/workflows/uptime.yml` runs `ops/uptime_check.py` every 10 minutes on GitHub (off the Coolify server) for Identity (`/health/ready`), Tracker, Academics, CRM and DMS. A failing run emails the repository's watchers. Owner action: make sure the right people watch this repository and Actions is enabled.

**Standalone PostgreSQL for new products.** Coolify resource `aadi-platform-postgres` (postgres:17-alpine, internal only, Coolify native daily backup, 14 kept). Rule: one database and one login per product; never put a product's tables in the `postgres` database. Create the product's login and database with `psql` from the Coolify terminal.

**Staging gate (deviation D-6).** `powershell -File ops\staging_gate.ps1 -LogDir <folder> > out.txt 2>&1` starts Identity, Tracker, Academics and the Video Engine locally with Aadi-Test data and smoke-checks all of them; exit code 0 means PASS. Run it before every deploy. A Coolify staging environment with `staging.*` addresses needs DNS records; until then this is the pre-production gate.

## 10. Branches, CI and deploys (sprint P2-S2, 2026-11-14)

**A push to a deploy branch IS a production deploy.** Coolify deploys by webhook on every push to the branch it watches, with no approval step. The CI-only pushes of 2026-11-14 redeployed Identity, Tracker, Academics and the engine; Academics returned 503/connection errors for about two minutes while its container swapped. Run `ops/staging_gate.ps1` and the full test suite BEFORE pushing, and push in quiet hours for apps others are using.

| Application | Repository | Deploys from | Public address | CI |
|---|---|---|---|---|
| Identity (+ demo) | aadiTechnology/identity | main | id.aadiprojecttracker.aaditechnology.com | .github/workflows/ci.yml |
| Project Tracker | aadiTechnology/aadiprojecttracker | main (was identity-export; same commit 085bdb6 on 2026-11-14) | aadiprojecttracker.aaditechnology.com | yes |
| Academics | aadiTechnology/acedemicvideocreation | main | academics.aaditechnology.com | yes |
| Video Engine | aadiTechnology/ScriptToVideo | main | none (internal) | yes (service tests) |
| Project CRM | Codewithash27/Project-CRM (PERSONAL account) | main | crm.aaditechnology.com | none yet |
| DMS | aadiTechnology/DMS | dms-mvp-build | dms.aaditechnology.com | yes (existing) |

**Owner actions that cannot be done through the tools available to this assistant**
1. Transfer `Codewithash27/Project-CRM` into the `aadiTechnology` organisation (GitHub: Settings, Danger zone, Transfer). Then update the repository in Coolify (aadi-crm) and add the CI workflow.
2. Turn on branch protection for `main` in each repository (require the `test` job to pass) once the first CI runs are confirmed green.
3. Confirm GitHub Actions is enabled for the organisation and the first runs are green (not verifiable from the assistant: no GitHub CLI or token). Failures to expect and why: Tracker deselects one date-dependent test; the engine runs the service tests only.
4. Merge DMS `dms-mvp-build` into `main` when ready (not done: it was not part of the approved sprint scope and DMS is live from that branch).

**Known test-suite state (2026-11-14, local)**: Identity 89 pass; Tracker 150 pass, 1 pre-existing failure (`test_timesheet_submit_approve_reject`, depends on today's date versus demo data); Academics 228 pass; Video Engine 534 pass, 1 known unrelated failure (`test_project_slides_csv_is_utf8_csv`), and one engine isolation test failed once and passed on two re-runs (possible timing flake, not reproduced); aadi-org-kit 39 pass. One Identity test fails only on a developer machine whose `.env` sets `TEST_LOGINS_ENABLED=true`.

**Dependency scan (pip-audit, 2026-11-14)**: Identity clean. Academics had PyJWT 2.10.1 with 22 advisories: fixed (2.15.1, 228 tests pass). Tracker: python-jose 3.5.0, one advisory (PYSEC-2026-4298) with NO fixed version published: keep watching, consider replacing python-jose with PyJWT (Identity and the engine already use it). Video Engine: Pillow 12.3.0, 42 advisories fixed only in 13.0.0 (a major version): needs its own sprint with the golden-frame render tests before upgrading.

## 11. Service tokens for Identity's own API, secrets at rest (sprint P2-S3, 2026-11-14)

**What changed (all additive; old behaviour is the default).**
- New `/api/v1/...` paths for enrol, remove, seats, snapshot and me/products. The old `/api/...` paths still answer identically (contract in `contracts/identity-v1-paths.json`, test `tests/test_v1_contract.py`; published at `/api/v1/openapi.json`).
- A product's server can use an Identity **service token** instead of the shared secret: audience `identity`, scopes `identity:enrol`, `identity:remove`, `identity:seats`, `identity:snapshot`; 10 minutes; limited to one company (the snapshot scope is company-free and limited to the token's own product). Both ways are accepted until the secrets are retired.
- `SECRETS_KEY` (optional): encrypts webhook secrets and token signing keys in the database. Migration 0005 widens the two columns to TEXT (drilled on PostgreSQL, including a 0004 database with real values).
- Every response carries `X-Correlation-Id` (accepted if sent, else generated).

**To switch a product to service tokens (owner/platform-admin steps; I could not do these, they need an admin sign-in).**
1. `/admin` > Service clients > new: client id `<product>-identity`, calling product `<product>`, service `Aadi Identity`, scopes enrol + remove (+ seats/snapshot if used). Copy the secret shown once.
2. In Coolify, for that product add `IDENTITY_SERVICE_CLIENT_ID` and `IDENTITY_SERVICE_CLIENT_SECRET` (Tracker and Academics already pass them through) and redeploy. If Identity cannot issue a token the product falls back to its old secret and logs a warning, so a typo does not stop people being added.
3. After a release with no fallback warnings, remove `IDENTITY_PRODUCT_SECRET` / the shared secret path (a later sprint). NOTE: Tracker's nightly reconcile still uses the old secret; moving it to the snapshot token is a follow-up.

**To turn on encryption at rest (not done in production: key custody is the owner's decision, deviation D-7).**
1. Generate a key: `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`. Store it in a password manager AND as `SECRETS_KEY` in Coolify. If the key is lost the encrypted values cannot be read: webhook secrets must be set again (`set-product-secret`) and the signing key rotated; people would have to sign in again.
2. Redeploy Identity, then run once in the Identity container (for example as a one-off Coolify scheduled task): `python -m app.cli encrypt-secrets`. It is safe to repeat and reports how many values it converted.
3. Rollback: unset nothing; to go back, decrypt first (not provided on purpose) or restore the database backup from section 9.

**Rollback of this release.** Redeploy the previous commit. Migration 0005 needs no downgrade for that: TEXT columns are compatible with the old code.

**Test state for this release (local):** Identity 117 pass on SQLite and 116 pass on PostgreSQL (one PostgreSQL-only failure, `test_maintenance_runs_once_per_id`, fails identically on the previous commit); Tracker 158 pass + 1 pre-existing failure; Academics 236 pass; migration drill passes (5 scenarios); end-to-end transcript of a real Tracker client against a real Identity passes 9 of 9 steps.

**Found on the way (not changed).** Academics' `docker-compose.coolify.yml` does not pass `IDENTITY_PRODUCT_SECRET` to the container, so enrolling people from Academics into Identity may be switched off in production even if the variable is set in Coolify. Check, and add the line if it was meant to be on.

## 12. Entitlements v1: plans, features, limits (sprint P2-S5, 2026-11-14)

**Idea.** Identity says what a company may use; products read it instead of keeping a plan of their own. A plan belongs to one product and holds feature names (strings the product checks, for example `costing`) and limits (numbers the product enforces, for example `projects=50`; empty = unlimited). A subscription with NO plan behaves exactly as before. Nothing in Tracker or Academics reads plans yet (they only gained the reader), so no existing feature changes.

**Admin.** `/admin` > Plans: create or change a plan; "Give a company a plan" assigns it. Both emit `subscription.changed` to the product (the event carries `plan`, `features`, `limits` only when a plan is assigned; plan-less events are unchanged).

**Product side.** `GET /api/v1/entitlements?tenant_id=<company>` with a service token (scope `identity:entitlements`, add it to the product's service client) or, for now, the shared secret. The Tracker and Academics `IdentityClient.entitlements(tenant_id)` reader caches 5 minutes and serves the last known answer if Identity is down. Rule for products: treat `entitled:false` as no access to the product's paid parts, cache at most 5 minutes, never invent a plan locally.

**Migration 0006** adds `plans`, `plan_features`, `plan_limits` and a nullable `subscriptions.plan_key`; drilled on PostgreSQL on a production-like database with real subscriptions (up, down keeping the subscriptions, up). Rollback of the release: redeploy the previous commit (the extra tables and column are ignored by it).

**Tests (local):** Identity 134 pass on SQLite, 133 on PostgreSQL (+ the one PostgreSQL-only failure that also fails on the commit before this work); Tracker 162 + 1 pre-existing failure; Academics 240; migration drill 7 scenarios; end-to-end check of the real Tracker client reading a plan created in the console (8/8). A bug found by the tests and fixed before release: re-saving a plan inserted its features before deleting the old ones (unique-constraint error); plans are now updated in place.

**Not done in this sprint:** wiring plan features into Tracker or Academics screens (would change product behaviour: later, per product, when a plan is actually sold); defining the real plans and prices (owner/business decision); a plan for the Video Engine service (its limits still live in its `tenant_limits` table).
