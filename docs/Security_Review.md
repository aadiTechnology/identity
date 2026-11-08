# Aadi Identity: Security Review (Sprint 5)

Scope: the Identity service (`identity` repository) and its connection to Project Tracker (`app/routers/sso.py`, `app/identity_sync.py`). Method: reading the code against a checklist, automated tests for every control below, a dependency scan, and two live drills. This is a self-review, not an independent penetration test; get one before onboarding paying customers.

## Controls checked and how they are proven

| Area | Control | Proof |
|---|---|---|
| Passwords | Argon2id; imported bcrypt accepted and upgraded on first sign-in; strength rule on set/reset | tests |
| Sign-in | same message and same time for unknown account, wrong password, locked account; lock after 5 failures for 15 minutes | tests |
| Forms | CSRF (double-submit cookie) on every form and every admin/company call; same-site relative `next` only | tests |
| Sessions | random token, stored hashed, HttpOnly, Secure, SameSite=Lax; revoked on password reset and on "sign out everywhere" | tests |
| OIDC | exact redirect-address match; PKCE (S256) mandatory; one-time codes, replay kills what they produced; refresh tokens rotate and reuse revokes the whole family; unregistered return addresses never redirect | tests |
| Tokens | RS256, key id header, public keys at JWKS, old keys stay published after rotation | tests, rotation drill |
| Authorization | company administrators act only on their own company; a company keeps at least one active administrator; platform admin pages need platform admin | tests |
| Events | signed with HMAC-SHA256 over timestamp and body, 5 minute window, unique event id (repeats ignored), Project Tracker restricts but never creates roles or restores from a reconcile | tests on both sides |
| Output | templates auto-escape; admin and company scripts escape every value; CSP `script-src 'self'`; frames denied; HSTS; no referrer | code review |
| Secrets | no secrets in the repository; deployment values live in git-ignored files and Coolify; passwords are only typed into masked fields | review |

## Findings fixed in this sprint

| # | Finding | Fix |
|---|---|---|
| 1 | Password reset and the one-time-link page had no rate limit: an attacker could mail-bomb a person or enumerate by volume | per-address limit on both pages, and a per-email limit of 3 requests in 10 minutes (further requests answer the same but send nothing) |
| 2 | The token endpoint could be used to guess client secrets without limit | 20 failed client logins a minute per address, then 429; overall cap per address |
| 3 | `python-multipart` 0.0.20 and PyJWT 2.10.1 had known vulnerabilities (12 and 22 advisories) | upgraded to 0.0.33 and 2.15.1; scan now reports none |
| 4 | Project Tracker used `python-jose` 3.3.0 and `python-multipart` 0.0.6 with known vulnerabilities | upgraded (jose 3.5.0 with the cryptography backend, multipart 0.0.33); 124 tests pass; on branch `identity-hardening`, **not yet deployed** |
| 5 | A production start with a development database address, plain http, insecure cookies or file-based mail would run silently | the service now refuses to start and lists what to fix |
| 6 | Old sessions, codes, refresh tokens and delivered events would accumulate forever | daily housekeeping job |
| 7 | Signing key parsing on every request made token issue slow and easy to overload (89 ms to 20 ms after the fix) | parsed keys are cached |
| 8 | Test fixture ordering hid foreign-key assumptions | suite now also runs on PostgreSQL (41 pass); migrations drilled on PostgreSQL |

## Residual risks (not fixed; owner decisions)

| Risk | Impact | Recommendation |
|---|---|---|
| No multi-factor sign-in, especially for platform and company administrators | a stolen admin password exposes a company | build optional authenticator-app codes for administrators first |
| Rate limits and the event worker live in one process | with more than one process, limits are per process and two workers could deliver an event twice (receivers ignore repeats) | keep one process, or move both to Redis/PostgreSQL locks before scaling out |
| Webhook secrets and the signing key are stored in the database in clear | whoever can read the database can forge events and tokens | encrypt backups; consider supplying the key through `SIGNING_KEY_PEM` from a secret store; encrypt at rest on the server |
| Access tokens (15 min) cannot be revoked before they expire | a suspended person's access token may verify for up to 15 minutes at products that check only the signature | Project Tracker checks the membership on every request, so it is not affected; require the same of every product (see the onboarding guide) |
| Project Tracker sessions from Aadi sign-in last 60 minutes | removal by event is immediate, but removal without an event (missed delivery) waits for the next reconcile (24 h) or the session end | keep the reconcile on; shorten if needed |
| The first sign-in links a Project Tracker user to an Aadi account by email | a company administrator who can create an Aadi account with a victim's email could be linked to that victim's Project Tracker user | only accept invitations for addresses the company controls; consider requiring verified email before linking (the invitation flow already verifies by mail) |
| Platform administrators of Project Tracker still use the old form | outside Identity's protections | decide whether to give them Identity accounts |
| Demo instance has a shared known password | acceptable for fictional data only | keep it on its own address; never load real data there |
| No independent penetration test | unknown unknowns | commission one before the first paying customer |

## Live drills run

- **Backup and restore:** dump of a database with 2,813 audit rows restored into a scratch database; all 8 tables matched.
- **Migrations:** fresh install, upgrade from the previous revision with data, and one step down and up again, on PostgreSQL.
- **Key rotation:** a new key signs new tokens, the old key stays published, both verify, a full sign-in works after; Project Tracker fetches a rotated key by itself and refuses a key nobody published.
- **Load:** 30 simultaneous people, 98 requests a second, 0 errors (see the runbook).
