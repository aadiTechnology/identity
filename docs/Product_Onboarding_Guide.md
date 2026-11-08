# Putting a product on Aadi Identity

Use this for Documents (DMS), CRM, Aadi Social and anything later. Project Tracker is the worked example; each step names the file there to copy from (`ProjectTracking/backend/app/routers/sso.py`, `identity_sync.py`, `tests/test_sso.py`, `tests/test_identity_sync.py`, `frontend/src/pages/Login.tsx`).

**The rule:** the product keeps its own users, companies, roles and permissions. Identity only says *who this is* and *which company they are signing in to*. After the change every feature behaves as before.

## What Identity gives you

| | |
|---|---|
| Sign in | OpenID Connect, authorization code with PKCE (S256), RS256 tokens. Discovery at `/.well-known/openid-configuration`, keys at `/.well-known/jwks.json` |
| Claims (in both tokens) | `sub` (Identity user id), `email`, `name`, `tid` (Identity company id), `tenant_slug`, `product`, `sid`, `ep`. Access token `aud` is the product key; ID token `aud` is your client id |
| Where people start | your **initiate URL**: Identity's hub sends people there with `?tenant=<company id>`; it must start the sign-in and pass the hint on as `tenant` |
| Switcher | `GET /api/me/products` with the person's access token, or just link to the hub (`https://<identity>/`) |
| Events | signed `POST` to your webhook (below) |
| Snapshot | `GET /api/sync/snapshot?product=<key>` with `Authorization: Bearer <webhook secret>` |

## Steps

1. **Pick the keys.** Product key (`dms`, `crm`, `social`), client id (`<key>-web`), initiate URL, redirect URI (`<app>/api/auth/sso/callback`), post-logout URI (`<app>/`).
2. **Register it.** Add an env-driven registration like `_register_tracker` in `app/cli.py` (product, client, webhook URL and secret), or run `register-product` / `register-client`. Confidential servers use `--confidential`; the secret is shown once.
3. **Add link columns, nothing else.** `identity_id` on your user table and `identity_tenant_id` on your company table: nullable, unique, indexed (Project Tracker migration `0006`). Do not change your existing ids. DMS uses UUID ids: store Identity's UUID in the same column type.
4. **Add the sign-in routes** (copy `sso.py`): `/start` (PKCE verifier, state and nonce in a signed short-lived cookie, redirect to `/authorize`), `/callback` (exchange the code, verify ID token with the right audience, nonce and issuer; verify the access token with your product key as audience; refresh the key list once on an unknown key id), then map to your records:
   - person: by `identity_id`; else by email, link on first sign-in (only if the record has no link yet)
   - company: by `identity_tenant_id`; else by slug, link on first use
   - **refuse** if either has no local match or the person has no active membership in that company. Never create a user or a role from a token.
5. **Hand over a normal session.** Trade a one-time code for your usual session token, so everything after sign-in is your existing code. Use a shorter lifetime for these sessions (60 minutes in Project Tracker).
6. **Check membership on every request.** This is what makes suspension immediate. If your tokens only carry an id and are trusted until expiry, add a live check of the person's membership and company status.
7. **Switch by setting** `AUTH_MODE=local|both|platform` (default `local`). `both` shows the Aadi button next to the old form; `platform` also switches the old password routes off. Keep `local` working in tests.
8. **Frontend:** the Aadi button, the return handling, sign-out through Identity's `/logout?client_id=&post_logout_redirect_uri=`, and a "Switch product" link (`Login.tsx`, `App.tsx`).
9. **Receive events** (copy `identity_sync.py`): verify the `X-Aadi-Signature` header (`t=<unix>,v1=<hex>`, HMAC-SHA256 of `t + "." + raw body`, within 5 minutes), ignore an event id you have seen, then apply:

   | Event | Do |
   |---|---|
   | `membership.suspended`, `membership.removed` | suspend that person in that company (never delete) |
   | `membership.reactivated` | restore only if currently suspended and the person is linked |
   | `user.suspended` / `user.revoked` | suspend or sign out the person everywhere (bump your session epoch) |
   | `user.reactivated` | restore only if linked |
   | `subscription.changed` (your product) | company closed when status is not active/trial; reopened when it is |
   | `tenant.status_changed` | same for the company |
   | `membership.added` | note it; an administrator in your product grants the role |

10. **Reconcile**: pull the snapshot daily and apply restrictions only (suspend memberships, people, companies). Never restore from a snapshot.
11. **Tests to copy:** link on first sign-in, one-time code used once, unknown person refused, person without membership refused, wrong or missing state, token from another key rejected, rotated key picked up, platform-only mode disables old passwords, unsigned and replayed events refused, duplicate event harmless, suspension and reactivation round trip, ended subscription closes and reopens the company, reconcile never reactivates. Run your **whole existing suite** in `local` mode too.
12. **Roll out:** one pilot company in `both`, check, then the rest. Roll back by setting `local`.

## Per-product notes

| Product | Today | Specific work |
|---|---|---|
| **Documents (DMS)** | JWT with tenant id, row-level security by tenant, sessions table, Argon2 | smallest change: ids are UUIDs, so store Identity's; keep RLS by your own tenant id; map Identity company to your tenant; your session check already revokes at once |
| **CRM** | HttpOnly cookie sessions, roles `SUPER_ADMIN`, `BUSINESS_OWNER`, `MARKETING`, `SALESPERSON` and a role-permission table, single-tenant | add tenant ownership to leads, deals and tasks first (the larger job), then the same steps; keep its roles |
| **Aadi Social** | `client_id` per client, Super Admin and Client Admin, encrypted Meta tokens per client | treat `client_id` as the company link; keep Meta tokens in place; Aadi-side staff (Super Admin, Marketing team) are not company people and stay on their own sign-in or become platform accounts |

## Definition of done

Existing tests green in `local`; new tests green in `both` and `platform`; one pilot company signed in, suspended and restored; rollback tried; an onboarding video recorded on the demo pair; runbook section updated.
