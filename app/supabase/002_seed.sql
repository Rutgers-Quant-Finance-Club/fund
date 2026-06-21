-- ============================================================
-- Demo seed data — development / staging only
-- Generates ~90 days of realistic NAV history and trades
-- ============================================================

-- ── Pods ─────────────────────────────────────────────────────

insert into pods (id, name, asset_class, description, benchmark_symbol, inception_date, allocated_capital) values
  ('a1000000-0000-0000-0000-000000000001', 'Alpha Equities', 'equities',
   'Long/short US large-cap equity strategy focused on momentum and value factors.',
   'SPY', current_date - 90, 5000000),
  ('a1000000-0000-0000-0000-000000000002', 'Vol Arb', 'options',
   'Volatility arbitrage using equity options — primarily earnings and index plays.',
   'SPY', current_date - 90, 2500000),
  ('a1000000-0000-0000-0000-000000000003', 'Rates Pod', 'fixed_income',
   'Systematic fixed income strategy trading US Treasuries and investment-grade bonds.',
   'AGG', current_date - 90, 3000000);

-- ── Traders & memberships ────────────────────────────────────

-- Demo traders. auth_user_id is null here; in production each trader is
-- created as a Supabase Auth user and linked via auth_user_id. is_admin
-- traders can trade any pod and allocate capital.
insert into traders (id, display_name, is_admin) values
  ('c1000000-0000-0000-0000-000000000001', 'Jordan Kim',    true),
  ('c1000000-0000-0000-0000-000000000002', 'Alex Chen',     false),
  ('c1000000-0000-0000-0000-000000000003', 'Sam Rivera',    false),
  ('c1000000-0000-0000-0000-000000000004', 'Morgan Lee',    true),
  ('c1000000-0000-0000-0000-000000000005', 'Casey Torres',  false),
  ('c1000000-0000-0000-0000-000000000006', 'Drew Patel',    false),
  ('c1000000-0000-0000-0000-000000000007', 'Riley Johnson', false),
  ('c1000000-0000-0000-0000-000000000008', 'Quinn Murphy',  false);

insert into pod_memberships (pod_id, trader_id, role) values
  ('a1000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000001', 'pm'),
  ('a1000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000002', 'trader'),
  ('a1000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000003', 'trader'),

  ('a1000000-0000-0000-0000-000000000002', 'c1000000-0000-0000-0000-000000000004', 'pm'),
  ('a1000000-0000-0000-0000-000000000002', 'c1000000-0000-0000-0000-000000000005', 'trader'),
  ('a1000000-0000-0000-0000-000000000002', 'c1000000-0000-0000-0000-000000000006', 'trader'),

  ('a1000000-0000-0000-0000-000000000003', 'c1000000-0000-0000-0000-000000000007', 'pm'),
  ('a1000000-0000-0000-0000-000000000003', 'c1000000-0000-0000-0000-000000000008', 'trader');

-- ── NAV History (90 days, random-walk with drift) ─────────────
-- Uses generate_series to produce daily rows; each day's NAV drifts from previous.

do $$
declare
  v_date      date;
  v_pod_id    uuid;
  v_start     numeric;
  v_vol       numeric;
  v_nav       numeric;
  v_prev_nav  numeric;
  v_dr        numeric;
  v_rng       numeric;
  v_pod_ids   uuid[] := array[
    'a1000000-0000-0000-0000-000000000001'::uuid,
    'a1000000-0000-0000-0000-000000000002'::uuid,
    'a1000000-0000-0000-0000-000000000003'::uuid
  ];
  v_starts    numeric[] := array[5000000, 2500000, 3000000];
  v_vols      numeric[] := array[0.012, 0.018, 0.006];
  v_drifts    numeric[] := array[0.0004, 0.0003, 0.00015];
  i           int;
begin
  for i in 1..3 loop
    v_pod_id   := v_pod_ids[i];
    v_start    := v_starts[i];
    v_vol      := v_vols[i];
    v_prev_nav := v_start;

    for v_date in
      select gs::date
      from generate_series(current_date - 89, current_date, interval '1 day') gs
      where extract(dow from gs) not in (0, 6)   -- skip weekends
    loop
      v_rng := (random() * 2 - 1);
      v_dr  := v_drifts[i] + v_vol * v_rng;
      v_nav := v_prev_nav * (1 + v_dr);

      insert into nav_history (pod_id, date, nav, cash, daily_return)
      values (v_pod_id, v_date, round(v_nav, 2), round(v_nav * 0.05, 2), round(v_dr, 6))
      on conflict (pod_id, date) do nothing;

      v_prev_nav := v_nav;
    end loop;
  end loop;
end;
$$;

-- ── Sample trades (last 14 days) ─────────────────────────────

do $$
declare
  m1_pm     uuid; m1_t1 uuid; m1_t2 uuid;
  m2_pm     uuid; m2_t1 uuid; m2_t2 uuid;
  m3_pm     uuid; m3_t1 uuid;
  symbols1  text[] := array['AAPL','MSFT','NVDA','GOOGL','AMZN','META'];
  symbols2  text[] := array['SPY','QQQ','IWM'];
  symbols3  text[] := array['TLT','IEF','SHY','BND'];
  sides     text[] := array['buy','sell'];
  i         int;
  sym       text;
  side_val  text;
  qty       numeric;
  px        numeric;
  ts        timestamptz;
begin
  select trader_id into m1_pm from pod_memberships where pod_id='a1000000-0000-0000-0000-000000000001' and role='pm'     limit 1;
  select trader_id into m1_t1 from pod_memberships where pod_id='a1000000-0000-0000-0000-000000000001' and role='trader' limit 1;
  select trader_id into m1_t2 from pod_memberships where pod_id='a1000000-0000-0000-0000-000000000001' and role='trader' offset 1 limit 1;
  select trader_id into m2_pm from pod_memberships where pod_id='a1000000-0000-0000-0000-000000000002' and role='pm'     limit 1;
  select trader_id into m2_t1 from pod_memberships where pod_id='a1000000-0000-0000-0000-000000000002' and role='trader' limit 1;
  select trader_id into m2_t2 from pod_memberships where pod_id='a1000000-0000-0000-0000-000000000002' and role='trader' offset 1 limit 1;
  select trader_id into m3_pm from pod_memberships where pod_id='a1000000-0000-0000-0000-000000000003' and role='pm'     limit 1;
  select trader_id into m3_t1 from pod_memberships where pod_id='a1000000-0000-0000-0000-000000000003' and role='trader' limit 1;

  for i in 1..40 loop
    sym      := symbols1[1 + (floor(random()*6))::int];
    side_val := sides[1 + (floor(random()*2))::int];
    qty      := (floor(random()*200)+10)::numeric;
    px       := (150 + random()*700)::numeric;
    ts       := now() - (random()*14 || ' days')::interval - (random()*8 || ' hours')::interval;
    insert into trades (pod_id, trader_id, symbol, side, quantity, price, notional, asset_class, executed_at)
    values ('a1000000-0000-0000-0000-000000000001',
            (array[m1_pm,m1_t1,m1_t2])[1+(floor(random()*3))::int],
            sym, side_val, qty, round(px,2), round(qty*px,2), 'equities', ts);
  end loop;

  for i in 1..20 loop
    sym      := symbols2[1 + (floor(random()*3))::int];
    side_val := sides[1 + (floor(random()*2))::int];
    qty      := (floor(random()*50)+5)::numeric;
    px       := (400 + random()*150)::numeric;
    ts       := now() - (random()*14 || ' days')::interval - (random()*8 || ' hours')::interval;
    insert into trades (pod_id, trader_id, symbol, side, quantity, price, notional, asset_class, executed_at)
    values ('a1000000-0000-0000-0000-000000000002',
            (array[m2_pm,m2_t1,m2_t2])[1+(floor(random()*3))::int],
            sym, side_val, qty, round(px,2), round(qty*px,2), 'options', ts);
  end loop;

  for i in 1..15 loop
    sym      := symbols3[1 + (floor(random()*4))::int];
    side_val := sides[1 + (floor(random()*2))::int];
    qty      := (floor(random()*300)+50)::numeric;
    px       := (88 + random()*20)::numeric;
    ts       := now() - (random()*14 || ' days')::interval - (random()*8 || ' hours')::interval;
    insert into trades (pod_id, trader_id, symbol, side, quantity, price, notional, asset_class, executed_at)
    values ('a1000000-0000-0000-0000-000000000003',
            (array[m3_pm,m3_t1])[1+(floor(random()*2))::int],
            sym, side_val, qty, round(px,2), round(qty*px,2), 'fixed_income', ts);
  end loop;
end;
$$;
