-- Drop tables that the rebuilt backend no longer uses.
--
-- These were part of the original self-contained design that cached prices and
-- pre-computed positions/metrics in Postgres. The current backend computes the
-- live portfolio from `order_fills` + live Alpaca market data on demand (see
-- 004_portfolio_accounting.sql), so nothing reads these anymore:
--   • positions        → superseded by position_marks (live, marked-to-market)
--   • metrics          → the dashboard computes risk/return client-side
--   • price_history    → prices come straight from Alpaca, never persisted
--   • benchmark_prices → never referenced
--   • config           → risk-free rate / trading days are constants in code
--
-- Run on an existing database. Fresh installs from 001 never create these.
-- nav_history is intentionally KEPT (fallback NAV series in db.get_nav_series).

drop table if exists positions cascade;
drop table if exists metrics cascade;
drop table if exists price_history cascade;
drop table if exists benchmark_prices cascade;
drop table if exists config cascade;
