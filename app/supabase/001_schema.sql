-- ============================================================
-- Fund Transparency Portal — Schema (multi-trader pods)
--
-- Model:
--   pod      = a strategy team = ONE Alpaca account (1:1)
--   trader   = one rqfc account = one Supabase Auth user
--   3–4 traders share a pod's Alpaca account; admins trade any pod.
--
-- Trust boundary:
--   ALL writes (trades, positions, nav, metrics, capital) come from the
--   backend using the service-role key (bypasses RLS). Traders never write
--   directly and never hold Alpaca keys — the backend submits on their
--   behalf after checking their pod membership / admin role.
--   Public read is open everywhere EXCEPT pod_alpaca_credentials.
-- ============================================================

create extension if not exists "pgcrypto";

-- ── Pods (one Alpaca account each) ───────────────────────────
-- Public-facing only. Alpaca identifiers/keys live in
-- pod_alpaca_credentials, which is locked to the backend.
create table pods (
  id                uuid primary key default gen_random_uuid(),
  name              text not null,
  asset_class       text not null,
  description       text,
  benchmark_symbol  text not null default 'SPY',
  inception_date    date not null default current_date,
  allocated_capital numeric not null default 0,   -- current funded balance
  created_at        timestamptz not null default now()
);

-- ── Traders (rqfc accounts) ──────────────────────────────────
-- id is our own key so rows are seedable; auth_user_id links the
-- row to a Supabase Auth login. Backend maps JWT.sub -> auth_user_id.
create table traders (
  id           uuid primary key default gen_random_uuid(),
  auth_user_id uuid unique references auth.users(id) on delete set null,
  display_name text not null,
  is_admin     boolean not null default false,
  created_at   timestamptz not null default now()
);

-- Which pods a trader may trade. Admins (traders.is_admin) bypass this
-- and may trade any pod.
create table pod_memberships (
  pod_id      uuid not null references pods(id) on delete cascade,
  trader_id   uuid not null references traders(id) on delete cascade,
  role        text not null default 'trader' check (role in ('trader','pm')),
  assigned_at timestamptz not null default now(),
  primary key (pod_id, trader_id)
);

-- ── Pod Alpaca secrets (backend only — NEVER exposed) ────────
-- Trading-API mode: store each pod's own paper-account keypair here
--   (encrypted; use Supabase Vault / pgsodium in production).
-- Broker-API mode: keys are unnecessary — store only alpaca_account_id
--   and keep the firm's single Broker key in the backend env.
create table pod_alpaca_credentials (
  pod_id            uuid primary key references pods(id) on delete cascade,
  alpaca_account_id text,
  api_key_enc       text,
  api_secret_enc    text,
  updated_at        timestamptz not null default now()
);

-- ── Trades (written by backend; trader_id = who placed it) ───
create table trades (
  id              uuid primary key default gen_random_uuid(),
  pod_id          uuid not null references pods(id) on delete cascade,
  trader_id       uuid references traders(id) on delete set null,
  symbol          text not null,
  side            text not null check (side in ('buy','sell')),
  order_type      text,
  quantity        numeric,
  price           numeric,
  notional        numeric,
  limit_price     numeric,
  filled_qty      numeric,
  status          text,
  asset_class     text not null default 'equities',
  alpaca_order_id text unique,
  executed_at     timestamptz not null default now(),
  created_at      timestamptz not null default now()
);
create index on trades (pod_id, executed_at desc);
create index on trades (trader_id, executed_at desc);
create index on trades (executed_at desc);

-- ── Pod NAV time series (backend syncs daily closes from Alpaca) ──
-- Live positions/metrics are computed on the fly from fills + market data
-- (see order_fills / position_marks / portfolio_marks in 004); only the daily
-- NAV is persisted here as the fallback series when intraday bars are absent.
create table nav_history (
  pod_id       uuid not null references pods(id) on delete cascade,
  date         date not null,
  nav          numeric not null,
  cash         numeric not null default 0,
  daily_return numeric,
  primary key (pod_id, date)
);

-- ── Capital allocation audit log ─────────────────────────────
create table capital_allocations (
  id                uuid primary key default gen_random_uuid(),
  pod_id            uuid not null references pods(id) on delete cascade,
  new_capital       numeric not null,       -- funded balance after this change
  previous_capital  numeric,
  allocated_by      uuid references traders(id) on delete set null,
  note              text,
  created_at        timestamptz not null default now()
);
create index on capital_allocations (pod_id, created_at desc);

-- ── members view (keeps the frontend's roster API stable) ────
-- Presents pod_memberships + traders as the old "members" shape.
create view members
  with (security_invoker = true) as
select
  t.id                       as id,
  m.pod_id                   as pod_id,
  t.display_name             as name,
  m.role                     as role,
  null::text                 as avatar_url,
  m.assigned_at::date        as joined_at,
  t.is_admin                 as is_admin
from pod_memberships m
join traders t on t.id = m.trader_id;

-- ── Row Level Security ───────────────────────────────────────
-- Public read on display tables (transparency portal). Credentials
-- table has NO policies → only the service-role backend can touch it.
-- All writes are service-role (backend), so no insert/update policies.

alter table pods                    enable row level security;
alter table traders                 enable row level security;
alter table pod_memberships         enable row level security;
alter table pod_alpaca_credentials  enable row level security;
alter table trades                  enable row level security;
alter table nav_history             enable row level security;
alter table capital_allocations     enable row level security;

create policy "public read" on pods                for select to anon, authenticated using (true);
create policy "public read" on traders             for select to anon, authenticated using (true);
create policy "public read" on pod_memberships     for select to anon, authenticated using (true);
create policy "public read" on trades              for select to anon, authenticated using (true);
create policy "public read" on nav_history         for select to anon, authenticated using (true);
create policy "public read" on capital_allocations for select to anon, authenticated using (true);
-- (pod_alpaca_credentials intentionally has NO policy.)

grant select on members to anon, authenticated;

-- ── Realtime ─────────────────────────────────────────────────
alter publication supabase_realtime add table trades;
alter publication supabase_realtime add table nav_history;
