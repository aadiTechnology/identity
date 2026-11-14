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
