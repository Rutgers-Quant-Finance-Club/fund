# RQFC Fund — Deployment

How the three deployable units connect and where each should run. See
`ARCHITECTURE.md` for the design and `RUNBOOK.md` for the step-by-step deployment.

## The three units (+ two managed services)

```
   ┌─────────────────────────────┐
   │  app/  React SPA (static)   │   GitHub Pages
   │  reads VITE_BACKEND_URL      │
   └──────────────┬──────────────┘
                  │  HTTPS GET /public/*   (browser, no auth)
                  ▼
   ┌─────────────────────────────┐        ┌──────────────────┐
   │  backend/  FastAPI server   │───────▶│ Supabase (cloud) │  Postgres + Auth
   │  holds ALL secrets          │  svc   └──────────────────┘  (system of record)
   │  /public/*  /orders  /admin │  role
   │  /portal (admin UI)         │───────▶┌──────────────────┐
   └──────────────▲──────────────┘  keys  │ Alpaca (cloud)   │  broker / market data
                  │  HTTPS (auth: session / API key)         └──────────────────┘
   ┌──────────────┴──────────────┐
   │  package/  rqfc Python client│   PyPI  (pip install)
   │  traders' laptops / cron     │
   └─────────────────────────────┘
```

The **backend is the hub**. Every secret (Supabase service-role key, Supabase JWT
secret, Alpaca keys, Google OAuth) lives only there. The app and the package both
talk *only* to the backend — neither ever sees Supabase or Alpaca credentials.

---

## `app/` — public dashboard (static)
- **What:** Vite + React SPA; pure static files after `npm run build` (`base: '/fund/'`). No server, no secrets.
- **Does:** polls the backend's credential-free `/public/*` feeds and renders charts / blotter / leaderboard.
- **Config:** only `VITE_BACKEND_URL` (baked in at build time).
- **Connection:** browser → backend over HTTPS — the only link.

**Platform: GitHub Pages** — already wired in `.github/workflows/deploy.yml`
(builds on push to `main` touching `app/**`, adds an SPA `404.html` fallback,
injects the `VITE_BACKEND_URL` secret). Free, zero-maintenance. Switch to
**Cloudflare Pages / Netlify** only for a custom apex domain or instant cache
invalidation — then set `base` back to `'/'` in `vite.config.ts`.

---

## `backend/` — FastAPI engine (always-on server)
- **What:** long-running Python server (`uvicorn app.main:app`). Heavier deps: `alpaca-py`, `pandas`, `numpy`, `supabase`, `google-auth`.
- **Does:** trader auth (backend sessions / rqfc API keys / legacy JWTs), pod-permission checks, submits orders to Alpaca, writes Supabase with the service role, computes the live marked-to-market `/public/*` feeds, serves the Google-login admin portal at `/portal`.
- **Connections:** receives from the app (public GETs) and the package (authed); calls out to Supabase + Alpaca.
- **State:** has **in-process caches** (e.g. 50s NAV-series cache) and a `ThreadPoolExecutor` fan-out across pods → wants a **single, always-warm instance**. Not serverless; don't aggressively autoscale (cache isn't shared between instances).

**Platform: Railway (preferred) / Render paid / Fly.io.**
- `backend/railway.toml` is ready (Nixpacks, `uvicorn` start, `/` healthcheck) → **Railway** is smoothest: stays warm, usage-priced.
- Currently on **Render** (`fund-tkb1.onrender.com` is the package + Pages default). Render works, but the **free tier sleeps after ~15 min** — fatal here because every visitor polls every 5s and the first hit after sleep is a slow cold start. Use Render's **$7 Starter** (always-on) or move to Railway.
- **Avoid serverless** (Vercel Functions / Lambda): in-process caches, thread fan-out, and pandas/numpy/alpaca-py cold-start weight all fight the serverless model.

**Deploy checklist:**
- **Root directory = `backend/`** (the app is `app.main:app`, not `main:app`).
- Env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`, `ALPACA_API_KEY` / `ALPACA_API_SECRET` (+ `ALPACA_TRADING_BASE_URL`), `GOOGLE_OAUTH_CLIENT_ID`, `ADMIN_GOOGLE_EMAILS`, `ADMIN_PORTAL_SECRET`.
- **Lock `CORS_ORIGINS`** to your Pages URL in prod (defaults to `*`; acceptable since public feeds are GET-only, but tighten it).
- Add the **backend's** URL (e.g. `https://fund-production.up.railway.app`) to the Google OAuth client's authorized JavaScript origins — the admin portal page is served from the backend at `/portal`, so that's the origin Google checks (not the Pages URL). Otherwise the portal login breaks.

---

## `package/` — the `rqfc` client (published, not hosted)
- **What:** thin Python client, single dependency (`requests`). **Not a server — published, not deployed.**
- **Does:** `rqfc.login(...)` → backend `/auth/login`; `rqfc.pod("…").buy(...)` → backend `/orders`; admins get `rqfc.admin()`. Defaults to `https://fund-tkb1.onrender.com`, override with `RQFC_BACKEND_URL`.
- **Connection:** traders' machines / strategy cron jobs → backend over HTTPS.

**Platform: PyPI.** Run `python -m build` + `twine upload` from the repo root
(`pyproject.toml` is configured). Then `pip install rqfc` works everywhere. Until
then, `pip install git+https://github.com/.../fund.git` is the stopgap.

- **Pin `DEFAULT_BACKEND_URL`** (`package/rqfc/__init__.py`) to your real backend URL before publishing, so traders need zero config.
- ⚠️ **Name mismatch:** `pyproject.toml` builds `rqfc-dev` while `package/setup.py` says `rqfc`, both v1.0.0. Pick one name before publishing or PyPI uploads will conflict.

---

## Recommended stack (TL;DR)

| Unit | Platform | Why |
|---|---|---|
| `app/` | **GitHub Pages** (already wired) | free static; workflow done. Cloudflare Pages only for a custom domain |
| `backend/` | **Railway** (or Render Starter / Fly) | needs always-warm single instance + secrets; `railway.toml` ready; never sleeping free tiers or serverless |
| `package/` | **PyPI** | publish, don't host; pin the prod backend URL first |
| Postgres + Auth | **Supabase Cloud** | managed system of record |
| Broker / data | **Alpaca** (paper) | external; keys stay in the backend env |

## Deploy order (avoids broken links)
1. **Supabase** — run the `app/supabase/*.sql` files (see `RUNBOOK.md`).
2. **Backend** — deploy to Railway/Render with the env vars above; note its URL.
3. **App + package** — set that URL as the app's `VITE_BACKEND_URL` (repo secret)
   and as the package's `DEFAULT_BACKEND_URL`; then ship the app and publish the package.
