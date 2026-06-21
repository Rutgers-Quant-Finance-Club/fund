# RQFC Fund — Architecture

How the trading package, backend, database, Alpaca, and dashboard fit together.

## Core model

| Concept | Maps to | Notes |
|---|---|---|
| **Pod** | one Alpaca account (1:1) | A strategy team. Holds the capital and the positions. |
| **Trader** | one rqfc account = one Supabase Auth user | A person. Assigned to one or more pods. |
| **Admin** | a trader with `is_admin = true` | Can trade any pod, assign/remove traders, allocate capital. |

3–4 traders share a pod's single Alpaca account. Because the account is shared,
**Alpaca only knows pod-level positions and P&L** — performance is reported per
pod. Trades still record *which trader* placed them (for audit and activity feeds).

## The trust boundary (why there's a backend)

Traders must authenticate, must **not** hold the pod's Alpaca keys, and must be
restricted to pods they're assigned to. None of that can be enforced by code
running on a trader's laptop. So three things live behind a trusted backend:

1. **Alpaca credentials** — never sent to traders.
2. **Permission checks** — "can this trader trade this pod?"
3. **All database writes** — via the Supabase service-role key.

```
 Trader's machine            Backend (FastAPI, holds secrets)         External
 ────────────────            ────────────────────────────────        ──────────
 import rqfc
 rqfc.login(email, pw) ─────► POST /auth/login
                              Supabase Auth verifies password ──────► Supabase Auth
                              map auth user → trader row
        ◄───────────────────  returns backend session token
 account = rqfc.pod(id)
 account.buy("AAPL", 10) ───► POST /orders  (Authorization: Bearer backend token)
                              1. verify backend session/API key → trader
                              2. check membership / is_admin for pod
                              3. load pod's Alpaca keys
                              4. submit order ──────────────────────► Alpaca
                              5. log trade (service role) ──────────► Supabase
        ◄───────────────────  return fill

 React dashboard ───────────────────────────────────────────────► Backend /public/* feeds
```

> **Note on the dashboard's data source.** The React dashboard does **not** read
> Supabase directly anymore. It reads the backend's credential-free `/public/*`
> feeds, which mark positions to live Alpaca market data server-side. Supabase is
> still the system of record (the backend reads/writes it with the service role),
> but the browser only ever talks to the backend.

The package is a **thin client**: authenticate with the RQFC backend, then make
HTTP calls. The client should not require traders to configure Supabase project
URLs or anon keys. Those are backend implementation details. All Alpaca logic
stays **server-side** in the backend, where it can run with the pod's keys.

## Production trader experience

The production experience should be:

```bash
pip install rqfc
```

```python
import rqfc

rqfc.login("alice@example.com", "password")
account = rqfc.pod("Alpha Equities")
account.buy("AAPL", 10)
```

For automated strategies, admins can issue a revocable RQFC API key from the
admin portal:

```python
import rqfc

rqfc.login(api_key="rqfc_live_...")
account = rqfc.pod("Alpha Equities")
account.buy("AAPL", 10)
```

Traders should never need to know or configure `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, or
Alpaca API keys. The only optional client-side configuration is
`RQFC_BACKEND_URL`, used for local development or staging. The published package
should default to the production backend, currently `https://fund-tkb1.onrender.com`.

## Components

### 1. Supabase (database + auth)
- **Auth**: each trader is a Supabase Auth user (email + password). Login returns
  a JWT the backend verifies.
- **Postgres**: schema in `app/supabase/` (run in order: `001_schema.sql`,
  `002_seed.sql`, `003_trader_api_keys.sql`, `004_portfolio_accounting.sql`,
  `005_drop_unused.sql`).
  Public read on display tables; **all writes are service-role** (from the
  backend). The `pod_alpaca_credentials` table has *no* RLS policy, so only the
  backend can read it.

Key tables:
- `pods` — public pod info + `allocated_capital`. No Alpaca secrets.
- `traders` — rqfc accounts, linked to auth via `auth_user_id`, `is_admin` flag.
- `pod_memberships` — which traders may trade which pods, with `role`.
- `pod_alpaca_credentials` — **backend-only** Alpaca account id / keys per pod.
- `trader_api_keys` — backend-only; hashed rqfc API keys (003).
- `trades` — every order, tagged with `pod_id` + `trader_id`. Extended in 004 with
  `realized_pnl`, `fees`, `multiplier`, and option/instrument fields.
- `order_fills` — Alpaca fills (authoritative for positions/P&L); trades stay the
  public order log (004).
- `position_marks`, `portfolio_marks` — live marked-to-market positions and pod
  value, computed from fills + market data on each public-feed poll (004).
- `nav_history` — daily NAV closes synced from Alpaca, kept as the fallback series
  when intraday bars are unavailable. (Live positions and risk/return metrics are
  computed on the fly, not stored — the old `positions`/`metrics`/`price_history`/
  `benchmark_prices`/`config` tables were dropped in 005.)
- `capital_allocations` — audit log of funding changes.
- `members` (view) — `pod_memberships ⋈ traders`, so the dashboard has a stable roster API.

### 2. Backend (FastAPI — recommended)
A small Python service. FastAPI because the existing Alpaca logic is already
Python (`alpaca-py`) and moves over almost verbatim.

Responsibilities:
- Verify Supabase JWTs (HS256 with the project JWT secret, or JWKS).
- Enforce permissions (membership / admin).
- Hold Alpaca credentials and submit orders.
- Write trades and sync positions / NAV / metrics into Supabase (service role).

Proposed endpoints:

| Method | Path | Who | Does |
|---|---|---|---|
| POST | `/orders` | trader (for their pods) / admin (any) | place an order, log the trade |
| POST | `/orders/cancel` | same | cancel an open order |
| GET | `/pods/{id}/account` | members / admin | live equity, buying power |
| GET | `/pods/{id}/positions` | members / admin | live positions |
| GET | `/market/price`, `/market/bars` | any trader | market data (read-only) |
| POST | `/admin/pods` | admin | create a pod + its Alpaca account |
| POST | `/admin/pods/{id}/capital` | admin | fund/reset the pod's balance, log it |
| POST | `/admin/memberships` | admin | assign / remove a trader |
| POST | `/sync/{pod_id}` | admin / cron | pull positions + NAV from Alpaca → Supabase |

Hosting: Render / Railway / Fly.io (any container host). Secrets (Supabase
service-role key, Supabase JWT secret, Alpaca keys) live in the backend env.

### 3. rqfc package (thin client)
```python
import rqfc

rqfc.login("alice@example.com", "password")   # → backend /auth/login, caches token
account = rqfc.pod("Alpha Equities")         # pick a pod you're allowed to trade
account.buy("AAPL", 10)                       # → POST backend /orders
account.positions()                           # → GET backend /pods/{id}/positions

# admins only
admin = rqfc.admin()
admin.create_pod("Vol Arb", "options", capital=100_000)
admin.add_trader("bob@example.com", pod="Vol Arb", role="trader")
admin.allocate_capital("Vol Arb", 150_000)
```
The client keeps the same friendly method names (`buy`, `sell`, `short`, …) but
each one is now an authenticated HTTP call, not a direct Alpaca call.

### 4. Admin portal
A self-contained web UI served by the backend at `GET /portal`
(`backend/app/portal/index.html`, vanilla JS — no build step). It signs in with
Google Identity Services, sends the Google ID token to `POST /admin/login`, and
receives a portal token only if the verified email is in `ADMIN_GOOGLE_EMAILS`.
It drives the `/admin/*` API to create pods, create rqfc accounts (Supabase Auth user + trader), set Alpaca
credentials, allocate capital, and assign traders. It lives on the backend (not
the public GitHub Pages dashboard) because those actions need the service-role
key. `/admin/*` accepts the portal token **or** an admin trader bearer, so the
portal and the Python admin client are interchangeable.

The portal is also the control plane for trader API keys:
- Create a key for a trader with a friendly name.
- Show the plaintext key exactly once.
- Store only a server-peppered hash in the database.
- List key metadata: prefix, name, created time, last-used time, revoked time.
- Revoke keys without deleting audit history.

### 5. React dashboard
Read-only, public SPA (Vite + React, `HashRouter`, deployed to GitHub Pages).
It reads **only the backend's `/public/*` feeds** (via `VITE_BACKEND_URL`), not
Supabase directly. `src/data/useFund.ts` assembles the whole view from four feeds
polled on independent intervals:

| Feed | Powers |
|---|---|
| `GET /public/live` (5s) | per-pod account value, positions, P&L, roster |
| `GET /public/nav-series` (60s) | the center 1-minute NAV chart |
| `GET /public/trades` (8s) | completed-trades / blotter |
| `GET /public/ticker` (15s) | the stock ticker tape |

The backend marks positions to live Alpaca market data, so the public feeds are
self-contained — the browser never needs Supabase or Alpaca keys. Routes:
`/` (Live), `/leaderboard`, `/pods`, `/pods/:id`, `/about`. See `app/README.md`.

## Connecting the Alpaca accounts — one open decision

You chose **actual Alpaca funding** for capital allocation. How that's wired
depends on which Alpaca product you use:

### Option A — Alpaca **Broker API** (recommended for this)
Purpose-built for "a firm with many funded sub-accounts."
- One Broker API credential for the whole club (lives in backend env).
- Create one Alpaca account **per pod** programmatically.
- **Fund/allocate capital via journals** — real, API-driven, exactly what you want.
- Trade on behalf of any pod account with the single firm credential.
- `pod_alpaca_credentials` only stores each pod's `alpaca_account_id` (no per-pod secrets).
- Sandbox is free for development.

### Option B — Alpaca **Trading API** + separate paper accounts
- Manually create N Alpaca paper accounts (one per pod), store each keypair
  (encrypted) in `pod_alpaca_credentials`.
- "Funding" = resetting each paper account's balance in the dashboard — **not
  truly programmatic**, so `admin.allocate_capital()` can't fully work.
- Simpler to start, but doesn't deliver real capital allocation.

**Recommendation:** Broker API (sandbox) — it's the only option that makes
"admin allocates capital to a pod" a real, programmatic feature, and it removes
per-pod secret storage entirely.

## Security notes
- Traders never receive Alpaca keys; the backend holds them.
- Traders do not need Supabase URL/anon-key configuration in production; the
  backend owns Supabase Auth interaction.
- `pod_alpaca_credentials` is unreadable except by the service role. Use Supabase
  Vault / pgsodium to encrypt at rest in production.
- Backend session tokens are short-lived. Long-running automated strategies
  should use revocable RQFC API keys scoped by trader permissions.
- RQFC API keys are stored only as hashes, with a server-side pepper/secret.
- A trader can only act on pods in their `pod_memberships` (admins bypass).

## Build phases
1. **DB schema** — ✅ done (`app/supabase/001_schema.sql`, `002_seed.sql`,
   `003_trader_api_keys.sql`, `004_portfolio_accounting.sql`, `005_drop_unused.sql`).
2. **Confirm Alpaca product** — Broker API vs Trading API (above).
3. **Backend** — FastAPI skeleton: JWT verification, `/orders`, permission checks,
   Alpaca submission, trade logging. Move `trading.py`/`account.py`/`market_data.py`
   server-side.
4. **Package** — refactor `rqfc` into a thin authenticated client (`login`, `pod`,
   `admin`).
5. **Sync job** — scheduled `/sync` to refresh positions / NAV / metrics per pod.
6. **Auth setup** — create Supabase Auth users for traders; wire `auth_user_id`.

## Streamlined-auth implementation TODO

### Database
- [x] Add `trader_api_keys` table.
- [x] Store `key_hash`, `key_prefix`, `name`, `trader_id`, `created_at`,
      `last_used_at`, and `revoked_at`.
- [x] Enable RLS with no public write policies; backend service role manages rows.
- [x] Add indexes for `trader_id`, `key_hash`, and active key lookup.

### Backend auth
- [x] Add `POST /auth/login` so the backend, not the client, talks to Supabase
      Auth for email/password login.
- [x] Issue short-lived backend session tokens signed with a backend secret.
- [x] Update `get_current_trader` to accept backend session tokens.
- [x] Preserve existing Supabase-JWT support during migration.
- [x] Add API-key bearer auth using hashed key lookup.
- [x] Update API-key `last_used_at` on successful authentication.

### Backend admin API
- [x] Add `GET /admin/traders/{trader_id}/api-keys`.
- [x] Add `POST /admin/traders/{trader_id}/api-keys`.
- [x] Return plaintext API key only once at creation time.
- [x] Add `DELETE /admin/api-keys/{key_id}` to revoke keys.
- [x] Keep admin auth compatible with both portal tokens and admin trader tokens.

### Admin portal
- [x] Add API-key management to the trader admin screen.
- [x] Let admins select a trader, list keys, create a named key, copy the new key,
      and revoke old keys.
- [x] Clearly indicate that plaintext keys are shown once.

### Python package
- [x] Remove Supabase URL/anon-key requirements from `rqfc.configure` and
      `rqfc.login`.
- [x] Default to production backend URL; allow `RQFC_BACKEND_URL` or explicit
      `backend_url` override for staging/local dev.
- [x] Support `rqfc.login(email, password)` via backend `/auth/login`.
- [x] Support `rqfc.login(api_key="rqfc_live_...")` for automated strategies.
- [x] Preserve existing `rqfc.pod`, `Account`, `rqfc.admin`, and `Admin` APIs.

### Deployment
- [ ] Deploy backend with `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
      `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, Alpaca credentials,
      and backend signing/pepper secrets.
- [ ] Publish `rqfc` with production backend default.
- [ ] Keep dashboard public read-only on Supabase anon key.
- [ ] Document local/staging overrides for admins and developers.
