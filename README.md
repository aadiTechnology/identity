# Aadi Identity

Common sign-in for all Aadi products. One account, many companies, many products. Hosted at **https://id.aadiprojecttracker.aaditechnology.com**.

Extracted from Project Tracker (sprint plan: `ProjectTracking/docs/Identity_Platform_Extraction_Sprint_Plan.md`). Project Tracker keeps its own database, roles and permissions; this service answers *who you are*, *which companies you belong to* and *which products they bought*.

## What it does

- **Sign in once** (Argon2id passwords, lockout after 5 failures, no account enumeration, CSRF protection, rate limit).
- **Eligible products:** a company's active membership + live subscription decides what a person can open.
- **The redirect rule:** no product → explanation page. **Exactly one product in one company → straight into that product, the chooser is never shown.** Several → chooser, and a switcher API for the product header.
- **OpenID Connect:** authorization code with mandatory PKCE (S256), RS256 tokens, JWKS with key rotation, discovery, userinfo, refresh-token rotation with theft detection, registered redirect and logout addresses only.
- **Imported passwords keep working:** Project Tracker's bcrypt hashes are accepted and upgraded to Argon2id on first sign-in.
- **Platform admin** page (`/admin`): companies, subscriptions per product, people, recent activity.
- **Revocation:** disabling a person or a subscription stops token refresh; "sign out everywhere" bumps the user's epoch.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /` | Product hub (applies the redirect rule) |
| `GET /login`, `POST /login`, `POST /logout` | Sign in / out |
| `GET/POST /forgot`, `/reset` | Password reset and first-time password (invitations) |
| `GET /authorize`, `POST /token`, `GET /userinfo` | OIDC |
| `GET /.well-known/openid-configuration`, `/.well-known/jwks.json` | Discovery and signing keys |
| `GET /api/me/products` | Feeds the product-header switcher (Bearer access token or session) |
| `GET /logout?client_id=&post_logout_redirect_uri=` | RP-initiated sign-out |
| `GET /admin`, `/admin/api/*` | Platform administration |
| `GET /health` | Health check |

## Token claims

Access token (`aud` = product key) and ID token (`aud` = client id): `iss, sub` (Identity user UUID), `email, name, tid` (Identity company UUID), `tenant_slug, product, sid, ep` (revocation epoch), `exp`. Products map `sub`/`tid` to their own local user and company (a mapping column next to their own IDs) and keep applying their own roles and permissions.

## Run locally

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q                       # 24 tests, no database needed
docker compose up -d postgres                           # PostgreSQL on localhost:15440
cp .env.example .env
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.cli create-admin --email you@company.com
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Demo data (fictional, refuses to run in production): `APP_ENV=demo python -m app.cli seed-demo`.

## Register a product and its client

```bash
python -m app.cli register-product --key tracker --name "Project Tracker" --initiate-url https://tracker.aaditechnology.com/auth/login
python -m app.cli register-client --client-id tracker-web --product tracker \
    --redirect-uri https://tracker.aaditechnology.com/auth/callback --logout-uri https://tracker.aaditechnology.com/ --confidential
```

`initiate-url` is where the hub sends a person who picked this product; that URL starts the standard OIDC flow (and receives `?tenant=<Identity company UUID>` as a hint).

## Import from Project Tracker

```bash
python -m app.cli import-tracker --source-url postgresql+psycopg://readonly@host/tracker --dry-run
python -m app.cli import-tracker --source-url postgresql+psycopg://readonly@host/tracker
```

Idempotent, never deletes, carries password hashes, and creates a Tracker subscription for each company. The mapping of old IDs to Identity IDs is kept in `import_map`.

## Deploy (Coolify)

Create a Docker Compose resource from this repository using `docker-compose.coolify.yml`, set the app service domain to `https://id.aadiprojecttracker.aaditechnology.com` (port 8000) and fill the variables from `.env.coolify.example`. Migrations run when the container starts. Create the first administrator once: `docker exec -it <container> python -m app.cli create-admin --email ...` (password read from a prompt, or `IDENTITY_ADMIN_PASSWORD`).

## Decisions in force

D1 build thin on standard OIDC · D2 `id.` subdomain · D3 roles stay inside each product · D4 Identity UUIDs plus a mapping column in each product. Open: D5 token lifetimes (access 15 min, refresh 14 days rotated), D6 users in several companies (supported: one identity, many memberships).
