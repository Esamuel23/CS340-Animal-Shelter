# Threat Model — Grazioso Salvare Dashboard

Author: Elijah Samuel · 2026-10-11 · Scope: the enhanced app (`app.py`, `shelter/`) and its MongoDB.

## The one thing to know first

**The employer portal is attribution, not authentication.** A visitor types a name and a company.
Nothing is verified. Anyone can type "Google" and get in. The portal tells me *who says* they
visited. It does not prove who they are, and it does not keep anyone out who wants in.

## What is protected

| Asset | Why it matters |
|---|---|
| Database credentials (`MONGO_URI`, `MONGO_SETUP_URI`) | Whoever has them can read or change the data directly |
| The `animals` collection | Shelter records; must not be wiped or polluted |
| The `visits` collection | Who looked at the portfolio; other visitors should not see it |
| Session cookies | A stolen cookie lets someone appear as another visitor for up to 2 hours |

## Who we defend against

1. **A curious or hostile web visitor** typing into the page or sending crafted requests.
2. **Someone who reads the public GitHub repo** looking for secrets.
3. **A bug in my own code** that sends a too-broad query (for example `delete({})`).

Out of scope: a compromised MongoDB Atlas account, a compromised laptop, nation-state attackers.

## Threats and what stops them

| Threat | Control | Where |
|---|---|---|
| Credentials leaked in source or git history | Env vars only; `.env` gitignored; `.env.example` has names only; history scanned | `shelter/config.py`, `.gitignore` |
| Leaked app credentials used to wreck the DB | App user has a custom role: find/insert/update/remove on three collections only. No drop, no index or validator changes, no other databases | `scripts/create_local_users.py`, `docs/ATLAS_SETUP.md` |
| Network access to the cluster from anywhere | Atlas IP access list with my IP only (no `0.0.0.0/0`) | `docs/ATLAS_SETUP.md` |
| NoSQL operator injection (`$where`, `$ne`, `$regex`) | Keys starting with `$` rejected at any depth; field whitelist; values must be plain scalars; the UI only sends a profile key | `shelter/animal_shelter.py`, `shelter/profiles.py` |
| Mass update/delete by accident | `update`/`delete` refuse an empty filter | `shelter/animal_shelter.py` |
| Bad data written to the collection | `$jsonSchema` validator, `validationAction: "error"`, `additionalProperties: false` | `shelter/schema.py` |
| Error messages revealing internals | Driver exceptions mapped to typed errors with fixed text; Dash debug mode off by default | `shelter/animal_shelter.py`, `app.py` |
| Slow or hung database tying up the app | 5 s server-selection and connect timeouts | `shelter/config.py` |
| Unauthenticated access to dashboard data (portal on) | Flask `before_request` gate in front of every route, including Dash's JSON endpoints; no session → redirect or 401 with no data | `shelter/portal.py` |
| Session theft via script (XSS) | Cookie is `HttpOnly`; Dash escapes text; the sign-in page escapes what it echoes | `shelter/portal.py` |
| Cookie sent over plain HTTP | `Secure` flag on by default (`PORTAL_SECURE_COOKIE=1`) | `shelter/portal.py` |
| Cross-site request forgery | `SameSite=Lax` cookie, so cross-site POSTs (Dash callbacks, sign-out) arrive without the cookie | `shelter/portal.py` |
| Database leak exposing live sessions | Only the SHA-256 of the session token is stored, not the token | `shelter/portal.py` |
| Sessions living forever | 2-hour absolute expiry checked on every request, plus a TTL index that deletes expired sessions | `shelter/portal.py`, `scripts/setup_db.py` |
| Junk or script in name/company | NFKC normalize, collapse whitespace, 2–60 / 2–80 chars, small character set, refused (not silently changed) | `shelter/portal.py` |
| Sign-in flooding | 5 sign-in attempts per IP per minute (HTTP 429 after that) | `shelter/portal.py` |
| One visitor seeing another company's visits | The dashboard never shows visits; the report is a terminal script for the owner | `scripts/visits_report.py` |
| Clickjacking | `X-Frame-Options: DENY` | `shelter/portal.py` |

## What is NOT protected (plainly)

- **Identity.** Company and name are self-asserted. Do not treat the visits report as proof that a
  company looked. Treat it as a hint.
- **Determined access.** Anyone willing to type a fake name gets the dashboard. That is fine here
  because the data is public course data. Do not put private data behind this portal.
- **The rate limit is per process and in memory.** It resets on restart, does not share state
  across multiple workers, and trusts `request.remote_addr`. Behind a proxy or load balancer every
  visitor may share one IP; that would need a trusted `X-Forwarded-For` setup and a shared store.
- **No TLS in the app itself.** HTTPS must come from the host (for example a platform that
  terminates TLS). With `PORTAL_SECURE_COOKIE=1`, browsers will only send the cookie over HTTPS
  (localhost is the exception browsers make for development).
- **The app user can still write.** It has insert/update/remove on `animals` because the CRUD
  module supports it. If the dashboard is deployed read-only, give the deployed user `find` only.
- **The validator does not check meaning.** It catches a negative age but not a wrong breed name.
