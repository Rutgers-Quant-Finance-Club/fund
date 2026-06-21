# RQFC Fund — Dashboard (`app/`)

Public, read-only React dashboard for the multi-pod paper-trading fund. Anyone
can view each pod's live account value, the trade blotter, open positions, and
per-pod / per-trader performance. The SPA never writes and never authenticates.

> **Where the data comes from.** This dashboard reads **only** from the FastAPI
> backend's public transparency feeds (`/public/*`), not from Supabase directly.
> The backend marks positions to live Alpaca market data and exposes credential-
> free JSON. See `../backend/README.md` and `../docs/ARCHITECTURE.md`.

## Stack
- **Vite + React 18 + TypeScript**, `HashRouter` (GitHub Pages-safe).
- **@tanstack/react-query** for fetching/polling, **recharts** for the NAV chart,
  **@tanstack/react-table** for tables, **tailwindcss** + **lucide-react** + **zod**.
- Path alias `@/` → `src/` (see `tsconfig` / `vite.config.ts`).

## Run locally
```bash
cd app
npm install
# .env — point at a running backend (local or deployed):
#   VITE_BACKEND_URL=http://localhost:8000
npm run dev
```
The only required env var is **`VITE_BACKEND_URL`** (the FastAPI backend base
URL). If unset, requests are made relative to the current origin. The legacy
`VITE_SUPABASE_*` vars are no longer used by the live app (see *Legacy* below).

## Pages / routes
| Route | Page | Contents |
|---|---|---|
| `/` | **Live** (`pages/Live.tsx`) | Multi-pod NAV chart (`PortfolioChart`) + left sidebar with Completed-Trades / Positions tabs and a pod filter. |
| `/leaderboard` | **Leaderboard** | Per-trader table + per-pod standings. |
| `/pods` | **Pods** | Pod cards / index. |
| `/pods/:id` | **Pod Detail** | One pod's chart, positions, blotter, roster, metrics. |
| `/about` | **About** | Methodology + transparency note. |

A sliding stock `Ticker` tape and the `Masthead` nav wrap every page (`Layout.tsx`),
all inside an `ErrorBoundary`.

## Data flow
`src/data/useFund.ts` is the single data hook. It assembles the whole `FundData`
object (`Pod` / `Trader` / `Trade` / `Position`) from four backend feeds and polls
them on independent intervals:

| Feed | Hook interval | Purpose |
|---|---|---|
| `GET /public/live` | 5s | live per-pod snapshot (account value, positions, P&L, roster) |
| `GET /public/nav-series?minutes=390` | 60s | per-pod **1-minute** NAV series (powers the center chart) |
| `GET /public/trades?limit=200` | 8s | completed-trades / order log feed |
| `GET /public/ticker` | 15s | the stock ticker tape |

The latest NAV point is anchored to the 5s live account value so the line keeps
ticking between minute bars. Pod `code` = array index + 1; pastel `tint` assigned
by index. Per-pod Sharpe / max-drawdown are computed client-side
(`src/data/compute.ts`) from the NAV series. Attribution is **pod-level**, so a
trader's live P&L is shown as their pod's P&L; per-trader rows are built from each
pod's `members`. Empty pages render a `NoData` state pointing to the admin portal.

## Layout
```
src/
  data/        useFund.ts (the data hook), compute.ts (metrics), types.ts, colors.ts
  lib/         backend.ts (VITE_BACKEND_URL helper), formatters.ts, cn.ts, theme.tsx
  pages/       Live, Leaderboard, Pods, PodDetail, About
  components/  Layout, Masthead, Ticker, PortfolioChart, TradesFeed, Positions, ui, ErrorBoundary
  App.tsx      HashRouter + routes
  main.tsx
supabase/      SQL the backend's DB is provisioned from (001_schema … 004_portfolio_accounting)
```

## Deploy — GitHub Pages
`.github/workflows/deploy.yml` builds `app/` and publishes to Pages on pushes to
`main` that touch `app/**`. `vite.config.ts` sets `base: '/fund/'` (the repo name);
the workflow copies `dist/index.html` → `dist/404.html` as an SPA fallback. Set a
`VITE_BACKEND_URL` repo secret to point the deployed site at the backend
(defaults to `https://fund-tkb1.onrender.com`).

## Legacy / dead code
`src/hooks/*` and `src/lib/supabase.ts` are the original Supabase-direct data
layer (`@supabase/supabase-js`, realtime). They are **no longer imported** by any
page or component — the app is fully backend-fed. They're kept for reference only
and can be deleted along with the `@supabase/supabase-js` dependency.
