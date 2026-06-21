# RQFC — Deployment Runbook

Deploy the whole system from scratch and place a real (paper) trade that shows up
on the live dashboard. See `ARCHITECTURE.md` for the design and `DEPLOYMENT.md`
for platform rationale.

The four deployable pieces and their hosts:

| Piece | Host |
|---|---|
| Postgres + Auth | **Supabase** (cloud) |
| `backend/` FastAPI | **Railway** |
| `app/` React dashboard | **GitHub Pages** |
| `rqfc/` client | **PyPI** |

**Deploy order matters:** Supabase → Backend → App + Package. The app and the
package both need the live backend URL, and the backend needs the database.

## 0. Prerequisites
- A GitHub account with this repo pushed to it
- A Supabase account (free tier is fine)
- A Railway account (linked to GitHub)
- One Alpaca **paper** account → API key + secret (https://alpaca.markets)
- A Google OAuth **Web application** client (Google Cloud Console → APIs &
  Services → Credentials) for the admin portal login
- A PyPI account + API token

---

## 1. Database — Supabase
Create a project, then in **SQL Editor** run, in order:
1. `app/supabase/001_schema.sql`
2. `app/supabase/002_seed.sql`  *(optional — demo pods/trades so the dashboard isn't empty)*
3. `app/supabase/003_trader_api_keys.sql`
4. `app/supabase/004_portfolio_accounting.sql`
5. `app/supabase/005_drop_unused.sql`  *(drops legacy tables; harmless on a fresh DB)*

Then grab these from **Settings → API** (you'll paste them into Railway in §2):
- Project URL → `SUPABASE_URL`
- `anon` public key → `SUPABASE_ANON_KEY`
- `service_role` secret key → `SUPABASE_SERVICE_ROLE_KEY`
- **JWT Settings → JWT Secret** → `SUPABASE_JWT_SECRET`

---

## 2. Backend — Railway
`backend/railway.toml` already declares the Nixpacks build, the start command
(`uvicorn app.main:app --host 0.0.0.0 --port $PORT`), and the `/` healthcheck — so
the only manual work is the root directory and env vars.

1. **railway.app → New Project → Deploy from GitHub repo** → pick this repo.
2. Open the service → **Settings → Source → Root Directory = `backend`**.
   (Railway then reads `backend/railway.toml`; don't override the start command.)
3. **Settings → Networking → Generate Domain**. Note the URL, e.g.
   `https://fund-production.up.railway.app` — referred to as `BACKEND_URL` below.
4. **Variables** tab → add the following (Railway injects `$PORT` itself — do
   **not** set it):

   | Variable | Value |
   |---|---|
   | `SUPABASE_URL` | from §1 |
   | `SUPABASE_SERVICE_ROLE_KEY` | from §1 |
   | `SUPABASE_JWT_SECRET` | from §1 |
   | `SUPABASE_ANON_KEY` | from §1 (used for trader email/password login) |
   | `ALPACA_API_KEY` / `ALPACA_API_SECRET` | paper keys (fallback account for pods without their own) |
   | `ALPACA_TRADING_BASE_URL` | `https://paper-api.alpaca.markets` |
   | `GOOGLE_OAUTH_CLIENT_ID` | your Google OAuth Web client id |
   | `ADMIN_GOOGLE_EMAILS` | comma-separated admin emails allowed into the portal |
   | `ADMIN_PORTAL_SECRET` | any long random string |
   | `CORS_ORIGINS` | leave unset for now; set in §3 once you know the Pages URL |

5. In **Google Cloud Console → your OAuth Web client → Authorized JavaScript
   origins**, add `BACKEND_URL`. The admin portal page is served *from the
   backend* at `BACKEND_URL/portal`, so the origin Google checks is the backend's,
   **not** the Pages URL.
6. Verify the deploy:
   ```bash
   curl https://BACKEND_URL/                      # landing page / health → 200
   curl https://BACKEND_URL/public/leaderboard    # JSON (likely empty) → 200
   ```

---

## 3. App — GitHub Pages
The workflow `.github/workflows/deploy.yml` builds on every push to `main` that
touches `app/**` and adds the SPA `404.html` fallback. `vite.config.ts` sets
`base: '/fund/'`, so the site lands at `https://<user>.github.io/fund/`.

1. Repo → **Settings → Pages → Source = GitHub Actions**.
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   `VITE_BACKEND_URL = BACKEND_URL` (the Railway URL from §2). This is baked into
   the bundle at build time.
3. Trigger a build: push any change under `app/**`, or **Actions → Deploy to
   GitHub Pages → Run workflow**.
4. Back in Railway, set **`CORS_ORIGINS = https://<user>.github.io`** and redeploy.
   (Public feeds are GET-only so `*` also works, but lock it down.)
5. Open `https://<user>.github.io/fund/` — it should load and start polling
   `BACKEND_URL/public/*` (check the browser Network tab).

---

## 4. Package — PyPI
Two one-time fixes before the first publish (both are real blockers today):

1. **Pin the backend URL** in `package/rqfc/__init__.py` so traders need zero
   config:
   ```python
   DEFAULT_BACKEND_URL = "https://BACKEND_URL"   # was the old Render URL
   ```
2. **Resolve the distribution-name mismatch:** `pyproject.toml` says
   `name = "rqfc-dev"` while `package/setup.py` says `name = "rqfc"`. A root-level
   `python -m build` uses `pyproject.toml`, so it ships **`rqfc-dev`**. Pick the
   real PyPI name and set it in *both* files (the import name stays `rqfc`
   regardless — that's the package folder).

Then publish from the repo root:
```bash
pip install --upgrade build twine
rm -rf dist build package/*.egg-info
python -m build                       # → dist/<name>-1.0.0-py3-none-any.whl + .tar.gz
twine upload dist/*                   # needs a PyPI account + API token
```
Bump `version` in `pyproject.toml` (and `__version__`) for any later release —
PyPI rejects re-uploading the same version.

---

## 5. Admin portal — create pods, accounts, assignments
Open `https://BACKEND_URL/portal` and sign in with an allowlisted Google account.
The backend verifies the Google ID token using `GOOGLE_OAUTH_CLIENT_ID` and only
issues a portal token if the email is in `ADMIN_GOOGLE_EMAILS`.

1. **Pods** → create a pod (e.g. "Prod Test", equities, capital 100000). Leave the
   Alpaca fields blank to use the backend's `ALPACA_*` fallback account, or paste
   a pod-specific key/secret. Use **Alpaca** / **Capital** buttons to edit later.
2. **Traders** → create rqfc accounts (display name + email + password + role).
   This creates the Supabase Auth login *and* the linked trader row.
3. **Assignments** → assign a trader to a pod with a role.

---

## 6. End-to-end test (live system)
1. **Backend up:** `curl https://BACKEND_URL/public/leaderboard` → `200`.
2. **Install the published client** in a clean environment:
   ```bash
   pip install <name>                 # the PyPI name from §4 — no RQFC_BACKEND_URL
   ```                                # needed, since DEFAULT_BACKEND_URL is pinned
3. **Trade as the trader** you created in §5:
   ```python
   import rqfc
   rqfc.login("trader@example.com", "the-password-you-set")
   rqfc.whoami()                      # shows Prod Test
   acct = rqfc.pod("Prod Test")
   acct.buy("AAPL", 1)                # market order via the backend
   acct.sync()                        # push daily NAV to the dashboard
   ```
4. **Permission check:** trading a pod they're not in returns
   `[403] You are not assigned to this pod.`
5. **Dashboard:** open `https://<user>.github.io/fund/` → the AAPL trade is in the
   blotter and the position/NAV are marked to live market data.

## Done checklist
- [ ] All five SQL files applied in Supabase
- [ ] `curl BACKEND_URL/public/leaderboard` returns 200 from Railway
- [ ] Google admin login works at `BACKEND_URL/portal`
- [ ] Dashboard loads over HTTPS and polls `BACKEND_URL/public/*`
- [ ] `CORS_ORIGINS` is locked to the Pages origin
- [ ] `pip install <name>` from PyPI works with no `RQFC_BACKEND_URL` set
- [ ] A trade placed via the published client appears on the live dashboard

## Production hardening (later)
- Switch to Alpaca **Broker API** for real programmatic capital allocation (see ARCHITECTURE.md).
- Encrypt `pod_alpaca_credentials` with Supabase Vault / pgsodium.
- Run a scheduled `/sync` per pod (cron) so the dashboard's daily NAV stays fresh.
