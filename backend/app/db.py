"""Supabase access for the backend, using the service-role key (bypasses RLS).

Everything that writes to the database goes through here. Traders never write
directly; the backend is the only writer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from supabase import create_client, Client

from .config import get_settings

_sb: Client | None = None


def sb() -> Client:
    global _sb
    if _sb is None:
        s = get_settings()
        _sb = create_client(s.supabase_url, s.supabase_service_key)
    return _sb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Traders & permissions ────────────────────────────────────────────────────

def get_trader_by_auth_id(auth_user_id: str):
    res = (
        sb().table("traders")
        .select("id, display_name, is_admin, auth_user_id")
        .eq("auth_user_id", auth_user_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_trader_by_id(trader_id: str):
    res = (
        sb().table("traders")
        .select("id, display_name, is_admin, auth_user_id")
        .eq("id", trader_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_traders():
    res = sb().table("traders").select("id, display_name, is_admin, auth_user_id, created_at").execute()
    return res.data or []


def list_trade_activity(trader_id: str = None, pod_id: str = None, limit: int = 100):
    limit = max(1, min(int(limit or 100), 500))
    q = (
        sb().table("trades")
        .select("id, pod_id, trader_id, symbol, side, order_type, instrument_type, quantity, price, notional, realized_pnl, status, asset_class, executed_at, pods(name), traders(display_name)")
        .order("executed_at", desc=True)
        .limit(limit)
    )
    if trader_id:
        q = q.eq("trader_id", trader_id)
    if pod_id:
        q = q.eq("pod_id", pod_id)
    rows = q.execute().data or []
    for row in rows:
        notional = row.get("notional")
        quantity = row.get("quantity")
        price = row.get("price")
        if notional is None and quantity is not None and price is not None:
            row["computed_notional"] = round(abs(float(quantity) * float(price)), 2)
        else:
            row["computed_notional"] = notional
    return rows


def list_public_trades(pod_id: str = None, limit: int = 100) -> list[dict]:
    """Public completed-trades feed: order log joined with pod + trader names.

    Powers the Live sidebar "Completed Trades" tab. Never exposes credentials.
    """
    limit = max(1, min(int(limit or 100), 500))
    q = (
        sb().table("trades")
        .select("id, pod_id, trader_id, symbol, side, order_type, instrument_type, "
                "quantity, price, notional, status, realized_pnl, asset_class, "
                "executed_at, pods(name), traders(display_name)")
        .order("executed_at", desc=True)
        .limit(limit)
    )
    if pod_id:
        q = q.eq("pod_id", pod_id)
    rows = q.execute().data or []
    out = []
    for r in rows:
        qty = r.get("quantity")
        price = r.get("price")
        notional = r.get("notional")
        if notional is None and qty is not None and price is not None:
            notional = round(abs(float(qty) * float(price)), 2)
        out.append({
            "id": r.get("id"),
            "pod_id": r.get("pod_id"),
            "pod_name": (r.get("pods") or {}).get("name"),
            "trader": (r.get("traders") or {}).get("display_name"),
            "trader_id": r.get("trader_id"),
            "symbol": r.get("symbol"),
            "side": r.get("side"),
            "instrument_type": r.get("instrument_type") or "equity",
            "quantity": qty,
            "price": price,
            "notional": notional,
            "type": (r.get("order_type") or "MARKET"),
            "status": r.get("status") or "filled",
            "realized_pnl": r.get("realized_pnl"),
            "executed_at": r.get("executed_at"),
        })
    return out


def list_pod_members(pod_id: str) -> list[dict]:
    """Public roster for a pod (names + roles only — no credentials)."""
    try:
        res = (
            sb().table("members")
            .select("id, name, role, is_admin")
            .eq("pod_id", pod_id)
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def get_nav_series(pod_id: str, daily_limit: int = 365, mark_limit: int = 1000) -> list[dict]:
    """Account-value time series for the live chart.

    Combines daily `nav_history` (from /sync) with the intraday `portfolio_marks`
    that accumulate every time the public live feed is polled. Genuinely live —
    no synthetic points.
    """
    series: list[dict] = []
    try:
        daily = (
            sb().table("nav_history")
            .select("date, nav")
            .eq("pod_id", pod_id)
            .order("date")
            .limit(daily_limit)
            .execute()
            .data
            or []
        )
        for d in daily:
            if d.get("nav") is not None:
                series.append({"t": f"{d['date']}T00:00:00Z", "value": float(d["nav"])})
    except Exception:
        pass
    try:
        marks = (
            sb().table("portfolio_marks")
            .select("marked_at, portfolio_value")
            .eq("pod_id", pod_id)
            .order("marked_at", desc=True)
            .limit(mark_limit)
            .execute()
            .data
            or []
        )
        for m in reversed(marks):
            if m.get("portfolio_value") is not None:
                series.append({"t": m["marked_at"], "value": float(m["portfolio_value"])})
    except Exception:
        pass
    series.sort(key=lambda r: r["t"])
    return series


def list_pod_trades_for_marking(pod_id: str, days: int = 730) -> list[dict]:
    """Trades ordered oldest-first for deriving public mark-to-market positions.

    Defaults to 2 years of history — enough for any reasonable position replay
    without pulling the entire lifetime of a busy pod on every snapshot.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        res = (
            sb().table("trades")
            .select("id, pod_id, trader_id, alpaca_order_id, symbol, side, quantity, price, notional, filled_qty, limit_price, asset_class, status, executed_at, created_at, multiplier, instrument_type, fees, realized_pnl, filled_at")
            .eq("pod_id", pod_id)
            .gte("executed_at", since)
            .order("executed_at", desc=False)
            .execute()
        )
    except Exception:
        res = (
            sb().table("trades")
            .select("id, pod_id, trader_id, alpaca_order_id, symbol, side, quantity, price, notional, filled_qty, limit_price, asset_class, status, executed_at, created_at")
            .eq("pod_id", pod_id)
            .gte("executed_at", since)
            .order("executed_at", desc=False)
            .execute()
        )
    return res.data or []


def list_order_fills(pod_id: str) -> list[dict]:
    try:
        res = (
            sb().table("order_fills")
            .select("*")
            .eq("pod_id", pod_id)
            .order("filled_at", desc=False)
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def upsert_order_fills(pod_id: str, fills: list[dict]) -> None:
    if not fills:
        return
    payload = []
    for fill in fills:
        payload.append({
            "pod_id": pod_id,
            "trader_id": fill.get("trader_id"),
            "trade_id": fill.get("trade_id"),
            "alpaca_order_id": str(fill.get("alpaca_order_id") or fill.get("fill_id")),
            "fill_id": str(fill.get("fill_id") or fill.get("alpaca_order_id")),
            "symbol": fill["symbol"],
            "side": fill["side"],
            "instrument_type": fill.get("instrument_type") or "equity",
            "underlying_symbol": fill.get("underlying_symbol"),
            "quantity": fill["quantity"],
            "price": fill["price"],
            "multiplier": fill.get("multiplier") or 1,
            "notional": fill["notional"],
            "fees": fill.get("fees") or 0,
            "realized_pnl": fill.get("realized_pnl"),
            "filled_at": fill["filled_at"],
            "raw": fill.get("raw"),
        })
    try:
        sb().table("order_fills").upsert(
            payload, on_conflict="pod_id,alpaca_order_id,fill_id"
        ).execute()
    except Exception:
        return


def replace_position_marks(pod_id: str, rows: list[dict]) -> None:
    try:
        if rows:
            now = _now()
            allowed = {
                "symbol", "instrument_type", "underlying_symbol", "quantity",
                "avg_entry_price", "current_price", "multiplier", "market_value",
                "cost_basis", "realized_pnl", "unrealized_pnl", "total_pnl",
            }
            sb().table("position_marks").upsert(
                [{"pod_id": pod_id, "updated_at": now, **{k: v for k, v in r.items() if k in allowed}} for r in rows],
                on_conflict="pod_id,symbol",
            ).execute()
        held = [r["symbol"] for r in rows]
        q = sb().table("position_marks").delete().eq("pod_id", pod_id)
        if held:
            q = q.not_.in_("symbol", held)
        q.execute()
    except Exception:
        return


_PORTFOLIO_MARKS_RETENTION_DAYS = 2


def insert_portfolio_mark(pod_id: str, row: dict) -> None:
    try:
        sb().table("portfolio_marks").insert({"pod_id": pod_id, **row}).execute()
        # Trim marks older than the retention window to keep the table bounded.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_PORTFOLIO_MARKS_RETENTION_DAYS)).isoformat()
        sb().table("portfolio_marks").delete().eq("pod_id", pod_id).lt("marked_at", cutoff).execute()
    except Exception:
        return


def create_auth_user(email: str, password: str) -> str:
    """Create a confirmed Supabase Auth user, return its id."""
    res = sb().auth.admin.create_user({
        "email": email, "password": password, "email_confirm": True,
    })
    return res.user.id


def create_trader(display_name: str, is_admin: bool = False, auth_user_id: str = None) -> dict:
    return sb().table("traders").insert({
        "display_name": display_name,
        "is_admin": is_admin,
        "auth_user_id": auth_user_id,
    }).execute().data[0]


def list_trader_api_keys(trader_id: str) -> list[dict]:
    res = (
        sb().table("trader_api_keys")
        .select("id, trader_id, name, key_prefix, revoked_at, last_used_at, created_at")
        .eq("trader_id", trader_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


def create_trader_api_key(trader_id: str, *, name: str, key_prefix: str, key_hash: str) -> dict:
    return (
        sb().table("trader_api_keys")
        .insert({
            "trader_id": trader_id,
            "name": name,
            "key_prefix": key_prefix,
            "key_hash": key_hash,
        })
        .execute()
        .data[0]
    )


def revoke_trader_api_key(key_id: str) -> bool:
    res = (
        sb().table("trader_api_keys")
        .update({"revoked_at": _now()})
        .eq("id", key_id)
        .is_("revoked_at", "null")
        .execute()
    )
    return bool(res.data)


def get_active_api_key_by_hash(key_hash: str):
    res = (
        sb().table("trader_api_keys")
        .select("id, trader_id, traders(id, display_name, is_admin, auth_user_id)")
        .eq("key_hash", key_hash)
        .is_("revoked_at", "null")
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def mark_api_key_used(key_id: str) -> None:
    sb().table("trader_api_keys").update({"last_used_at": _now()}).eq("id", key_id).execute()


def get_membership(pod_id: str, trader_id: str):
    res = (
        sb().table("pod_memberships")
        .select("pod_id, trader_id, role")
        .eq("pod_id", pod_id)
        .eq("trader_id", trader_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_trader_pods(trader_id: str):
    res = (
        sb().table("pod_memberships")
        .select("pod_id, role, pods(name, asset_class)")
        .eq("trader_id", trader_id)
        .execute()
    )
    return res.data or []


def list_memberships(pod_id: str = None):
    q = sb().table("pod_memberships").select("pod_id, trader_id, role, traders(display_name)")
    if pod_id:
        q = q.eq("pod_id", pod_id)
    return q.execute().data or []


def list_pods_admin():
    """All pods plus whether Alpaca creds are configured."""
    pods = sb().table("pods").select("*").order("created_at").execute().data or []
    creds = sb().table("pod_alpaca_credentials").select("pod_id, alpaca_account_id, api_key_enc").execute().data or []
    has = {c["pod_id"]: bool(c.get("api_key_enc")) for c in creds}
    for p in pods:
        p["has_alpaca"] = has.get(p["id"], False)
    return pods


# ── Pods & credentials ───────────────────────────────────────────────────────

def get_pod(pod_id: str):
    res = sb().table("pods").select("*").eq("id", pod_id).limit(1).execute()
    return res.data[0] if res.data else None


def find_pod_by_name(name: str):
    res = sb().table("pods").select("*").eq("name", name).limit(1).execute()
    return res.data[0] if res.data else None


def get_pod_alpaca(pod_id: str):
    """Return {alpaca_account_id, api_key, api_secret} for a pod, or None."""
    res = (
        sb().table("pod_alpaca_credentials")
        .select("alpaca_account_id, api_key_enc, api_secret_enc")
        .eq("pod_id", pod_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    return {
        "alpaca_account_id": row.get("alpaca_account_id"),
        "api_key": row.get("api_key_enc"),
        "api_secret": row.get("api_secret_enc"),
    }


def create_pod(name, asset_class, *, benchmark_symbol="SPY", description=None,
               allocated_capital=0, inception_date=None) -> dict:
    payload = {
        "name": name,
        "asset_class": asset_class,
        "benchmark_symbol": benchmark_symbol,
        "description": description,
        "allocated_capital": allocated_capital,
    }
    if inception_date:
        payload["inception_date"] = inception_date
    return sb().table("pods").insert(payload).execute().data[0]


def set_pod_alpaca(pod_id, *, alpaca_account_id=None, api_key=None, api_secret=None) -> None:
    sb().table("pod_alpaca_credentials").upsert({
        "pod_id": pod_id,
        "alpaca_account_id": alpaca_account_id,
        "api_key_enc": api_key,
        "api_secret_enc": api_secret,
        "updated_at": _now(),
    }, on_conflict="pod_id").execute()


def add_membership(pod_id, trader_id, role="trader") -> None:
    sb().table("pod_memberships").upsert({
        "pod_id": pod_id, "trader_id": trader_id, "role": role,
    }, on_conflict="pod_id,trader_id").execute()


def remove_membership(pod_id, trader_id) -> None:
    sb().table("pod_memberships").delete().eq("pod_id", pod_id).eq("trader_id", trader_id).execute()


def set_allocated_capital(pod_id, amount) -> None:
    sb().table("pods").update({"allocated_capital": amount}).eq("id", pod_id).execute()


def log_capital_allocation(pod_id, new_capital, previous_capital, allocated_by, note=None) -> None:
    sb().table("capital_allocations").insert({
        "pod_id": pod_id,
        "new_capital": new_capital,
        "previous_capital": previous_capital,
        "allocated_by": allocated_by,
        "note": note,
    }).execute()


# ── Trades, positions, NAV, metrics ──────────────────────────────────────────

def log_trade(pod_id, trader_id, trade: dict) -> None:
    payload = {"pod_id": pod_id, "trader_id": trader_id, **trade}
    try:
        sb().table("trades").insert(payload).execute()
    except Exception:
        legacy = {
            key: payload.get(key)
            for key in (
                "pod_id", "trader_id", "alpaca_order_id", "symbol", "side",
                "order_type", "quantity", "price", "notional", "limit_price",
                "filled_qty", "status", "asset_class", "executed_at",
            )
        }
        sb().table("trades").insert(legacy).execute()


def replace_positions(pod_id, rows: list) -> None:
    columns = {
        "symbol", "quantity", "avg_entry_price", "current_price",
        "market_value", "unrealized_pnl",
    }
    if rows:
        now = _now()
        sb().table("positions").upsert(
            [{"pod_id": pod_id, "updated_at": now, **{k: r.get(k) for k in columns}} for r in rows],
            on_conflict="pod_id,symbol",
        ).execute()
    held = [r["symbol"] for r in rows]
    q = sb().table("positions").delete().eq("pod_id", pod_id)
    if held:
        q = q.not_.in_("symbol", held)
    q.execute()


def upsert_nav(pod_id, rows: list) -> None:
    if not rows:
        return
    sb().table("nav_history").upsert(
        [{"pod_id": pod_id, **r} for r in rows], on_conflict="pod_id,date"
    ).execute()


def upsert_metrics(pod_id, row: dict) -> None:
    sb().table("metrics").upsert({"pod_id": pod_id, **row}, on_conflict="pod_id,as_of_date").execute()


def count_trades(pod_id) -> int:
    return sb().table("trades").select("id", count="exact").eq("pod_id", pod_id).execute().count or 0
