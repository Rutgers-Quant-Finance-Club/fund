# RQFC — End-to-End Runbook

Get the whole system running and place a real (paper) trade that shows up on the
dashboard. ~30 minutes. See `ARCHITECTURE.md` for the why.

## 0. Prerequisites
- A Supabase project (free tier is fine)
- One Alpaca **paper** account → API key + secret (https://alpaca.markets)
- Python 3.10+ and Node 18+

---

## 1. Database
In the Supabase dashboard → **SQL Editor**, run, in order:
1. `app/supabase/001_schema.sql`
2. `app/supabase/002_seed.sql`  *(optional — demo pods/trades so the dashboard isn't empty)*
3. `app/supabase/003_trader_api_keys.sql`
4. `app/supabase/004_portfolio_accounting.sql`

Then grab these from **Settings → API**:
- Project URL → `SUPABASE_URL`
- `anon` public key → `SUPABASE_ANON_KEY`
- `service_role` secret key → `SUPABASE_SERVICE_ROLE_KEY`
- **JWT Settings → JWT Secret** → `SUPABASE_JWT_SECRET`

---

## 2. Backend
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the values below
```
Fill `.env`:
- the four Supabase values from step 1
- `ALPACA_API_KEY` / `ALPACA_API_SECRET` — your paper keys. (These act as the
  fallback Alpaca account for any pod without its own stored creds, so a single
  pod works immediately.)
- `ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets` to make the paper
  trading endpoint explicit.
- `GOOGLE_OAUTH_CLIENT_ID` — a Google OAuth **Web application** client id whose
  authorized JavaScript origin includes `http://localhost:8000`
- `ADMIN_GOOGLE_EMAILS` — comma-separated Google emails allowed into the admin portal

Run it:
```bash
uvicorn app.main:app --reload --port 8000
```
Check: open `http://localhost:8000/` and confirm the backend landing page loads.

For Render, create a Web Service with:
- Root Directory: `backend`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

---

## 3. Admin portal — create pods, accounts, assignments
Open **http://localhost:8000/portal** and sign in with an allowlisted Google
account. The backend verifies the Google ID token using `GOOGLE_OAUTH_CLIENT_ID`
and only issues a portal token if the email is listed in `ADMIN_GOOGLE_EMAILS`.

In the portal:
1. **Pods** → create a pod (e.g. "Test Pod", equities, capital 100000). Leave the
   Alpaca fields blank to use the backend's `ALPACA_*` fallback account, or paste
   a pod-specific key/secret. Use **Alpaca** / **Capital** buttons to edit later.
2. **Traders** → create rqfc accounts (display name + email + password + role).
   This creates the Supabase Auth login *and* the linked trader row.
3. **Assignments** → assign a trader to a pod with a role.

That's the entire admin surface — no CLI needed. (A scripted alternative still
exists: edit + run `python -m scripts.seed_users` from `backend/`.)

---

## 4. Install the client
```bash
pip install -e .                 # from repo root
export RQFC_BACKEND_URL=http://localhost:8000
```

---

## 5. Trader: place a trade
Use the email + password you set for a trader in the portal.
```python
import rqfc
rqfc.login("alice@example.com", "the-password-you-set")
rqfc.whoami()                       # shows Test Pod

acct = rqfc.pod("Test Pod")
acct.buy("AAPL", 1)                 # market order via the backend
acct.account()                      # live equity / buying power
acct.positions()                    # live positions
acct.sync()                         # push positions + NAV to the dashboard
```

Permission check — Alice trading a pod she's not in should 403:
```python
rqfc.pod("Vol Arb").buy("SPY", 1)   # -> [403] You are not assigned to this pod.
```

---

## 6. See it on the dashboard
```bash
cd app
npm install
# .env: VITE_BACKEND_URL=http://localhost:8000   (the dashboard reads the
#       backend's /public/* feeds — no Supabase keys needed in the browser)
npm run dev
```
Open the app → **Test Pod**. The AAPL trade is in the blotter and the position /
NAV are marked to live market data from the `/public/*` feeds. (You can also
verify directly in Supabase → **Table Editor → trades**.)

---

## What "done" looks like
- [ ] Portal (`/portal`, Google admin login) creates pods, trader accounts, and assignments
- [ ] Non-admin Supabase logins get 403 on `/admin/*`
- [ ] A trader can trade only their assigned pod(s)
- [ ] Orders reach Alpaca and a `trades` row is written with the right `trader_id`
- [ ] `acct.sync()` populates `positions`, `nav_history`, `metrics`
- [ ] The dashboard reflects all of the above

## Production hardening (later)
- Switch to Alpaca **Broker API** for real programmatic capital allocation (see ARCHITECTURE.md).
- Encrypt `pod_alpaca_credentials` with Supabase Vault / pgsodium.
- Run a scheduled `/sync` per pod (cron) so the dashboard stays fresh.
- Lock `CORS_ORIGINS` to your dashboard domain; deploy the backend (Render/Railway/Fly).
