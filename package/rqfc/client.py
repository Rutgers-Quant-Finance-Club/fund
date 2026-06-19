"""The trader-facing Account object.

Every method is an authenticated call to the backend, which holds the pod's
Alpaca keys and submits on your behalf. You can only act on pods you're assigned
to (admins can act on any pod).
"""
from datetime import datetime, timezone

from ._session import Session, looks_like_uuid


def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%b %d, %Y  %H:%M:%S UTC")
    except Exception:
        return iso


_STATUS_LABELS = {
    "filled": "Order filled",
    "partially_filled": "Order partially filled",
    "accepted": "Order accepted (pending fill)",
    "pending_new": "Order accepted (pending fill)",
    "new": "Order accepted (pending fill)",
    "canceled": "Order canceled",
    "rejected": "Order rejected",
    "expired": "Order expired",
}


class OrderResult(dict):
    """Order response dict that prints as a readable confirmation."""

    def __repr__(self) -> str:
        status = self.get("status", "unknown")
        trade = self.get("trade") or {}
        symbol = trade.get("symbol", "?")
        side = (trade.get("side") or "").upper()
        qty = trade.get("quantity") or trade.get("filled_qty")
        price = trade.get("price")
        filled_qty = trade.get("filled_qty")
        notional = trade.get("notional")
        order_type = (trade.get("order_type") or "MARKET").upper()
        order_id = self.get("order_id") or trade.get("alpaca_order_id", "—")
        filled_at = _fmt_ts(trade.get("filled_at"))
        label = _STATUS_LABELS.get(status, f"Order {status}")

        # headline
        price_str = f" @ ${price:,.4f}" if price is not None else ""
        notional_str = f"  (${notional:,.2f} notional)" if notional is not None else ""
        qty_str = f"{qty:g}" if qty is not None else "?"
        headline = f"  {label} — {side} {qty_str}x {symbol}{price_str}{notional_str}"

        rows = [
            headline,
            "  " + "-" * max(len(headline) - 2, 48),
            f"  {'Order ID':<14} {order_id}",
            f"  {'Type':<14} {order_type}",
        ]
        if filled_qty is not None and qty is not None:
            rows.append(f"  {'Filled':<14} {filled_qty:g} / {qty:g} shares")
        if trade.get("filled_at"):
            rows.append(f"  {'Filled at':<14} {filled_at}")
        elif trade.get("executed_at"):
            rows.append(f"  {'Submitted':<14} {_fmt_ts(trade.get('executed_at'))}")
        return "\n".join(rows)


class PositionList(list):
    """List of positions that prints as a clean table."""

    def __repr__(self) -> str:
        if not self:
            return "  (no open positions)"
        col = "  {:<8}  {:>10}  {:>11}  {:>11}  {:>13}  {:>13}"
        rows = [
            col.format("SYMBOL", "QTY", "ENTRY", "MARK", "MKT VALUE", "UNREAL P&L"),
            "  " + "-" * 74,
        ]
        for p in self:
            pnl = p.get("unrealized_pnl") or 0
            sign = "+" if pnl >= 0 else ""
            rows.append(col.format(
                p.get("symbol", "?"),
                f"{p.get('quantity', 0):.4f}",
                f"${p.get('avg_entry_price', 0):.4f}",
                f"${p.get('current_price') or 0:.4f}",
                f"${abs(p.get('market_value') or 0):,.2f}",
                f"{sign}${abs(pnl):,.2f}",
            ))
        return "\n".join(rows)


class AccountSummary(dict):
    """Account dict that prints as a clean summary."""

    def __repr__(self) -> str:
        pv = self.get("portfolio_value", 0)
        cash = self.get("cash", 0)
        bp = self.get("buying_power", 0)
        sr = self.get("session_return")
        day = f"  {'Day Return':.<24} {'+' if sr >= 0 else ''}{sr*100:.2f}%" if sr is not None else ""
        return (
            f"\n  {'Portfolio Value':.<24} ${pv:>12,.2f}\n"
            f"  {'Cash':.<24} ${cash:>12,.2f}\n"
            f"  {'Buying Power':.<24} ${bp:>12,.2f}"
            + (f"\n{day}" if day else "")
        )


class SyncResult(dict):
    """Sync response that prints as a short summary."""

    def __repr__(self) -> str:
        metrics = "updated" if self.get("metrics") else "insufficient history"
        return (
            f"  Synced: {self.get('positions', 0)} position(s), "
            f"{self.get('nav_days', 0)} NAV day(s), metrics {metrics}."
        )


# Map common Alpaca rejection phrases to plain-language messages.
_ORDER_ERRORS = [
    (["insufficient", "buying power", "funds", "exceed"], "Insufficient funds."),
    (["not found", "no asset", "unknown symbol"], "Symbol not found or not tradeable."),
    (["market is closed", "outside", "not tradable"], "Market is closed or outside trading hours."),
    (["already exists", "duplicate"], "Duplicate order — this order was already placed."),
    (["qty", "quantity", "minimum"], "Invalid quantity (too small or fractional not supported)."),
]


class Account:
    """A pod you can trade. Obtain one via rqfc.pod(name_or_id)."""

    def __init__(self, session: Session, pod_ref: str):
        self._s = session
        self.pod_id = self._resolve_pod(pod_ref)

    def _resolve_pod(self, ref: str) -> str:
        if looks_like_uuid(ref):
            return ref
        for p in self._s.get("/pods"):
            if p["name"].lower() == str(ref).lower():
                return p["id"]
        raise ValueError(f"No pod named '{ref}'. Run rqfc.whoami() to see your pods.")

    # ── Orders ───────────────────────────────────────────────────────────────

    def _order(self, **kw):
        try:
            return OrderResult(self._s.post("/orders", {"pod_id": self.pod_id, **kw}))
        except RuntimeError as exc:
            raw = str(exc).lower()
            label = kw.get("symbol", "?").upper()
            for keywords, friendly in _ORDER_ERRORS:
                if any(k in raw for k in keywords):
                    print(f"  Order rejected ({label}): {friendly}")
                    return None
            # Unknown rejection — show the Alpaca detail but don't crash.
            print(f"  Order rejected ({label}): {exc}")
            return None

    def buy(self, symbol, qty, order_type="market", limit_price=None, time_in_force="day"):
        """Buy shares. order_type: 'market' (default) or 'limit'."""
        return self._order(symbol=symbol, side="buy", qty=qty, order_type=order_type,
                           order_label=order_type, limit_price=limit_price,
                           time_in_force=time_in_force)

    def sell(self, symbol, qty, order_type="market", limit_price=None, time_in_force="day"):
        """Sell shares you hold in the pod."""
        return self._order(symbol=symbol, side="sell", qty=qty, order_type=order_type,
                           order_label=order_type, limit_price=limit_price,
                           time_in_force=time_in_force)

    def short(self, symbol, qty, time_in_force="day"):
        """Short sell. Close with cover()."""
        return self._order(symbol=symbol, side="sell", qty=qty, order_label="short",
                           time_in_force=time_in_force)

    def cover(self, symbol, qty, time_in_force="day"):
        """Buy back a short position."""
        return self._order(symbol=symbol, side="buy", qty=qty, order_label="cover",
                           time_in_force=time_in_force)

    def dollar_buy(self, symbol, amount, time_in_force="day"):
        """Buy by dollar amount, e.g. dollar_buy('AAPL', 5000)."""
        return self._order(symbol=symbol, side="buy", notional=amount, order_label="dollar_buy",
                           time_in_force=time_in_force)

    def dollar_sell(self, symbol, amount, time_in_force="day"):
        """Sell by dollar amount."""
        return self._order(symbol=symbol, side="sell", notional=amount, order_label="dollar_sell",
                           time_in_force=time_in_force)

    def cancel(self, order_id):
        """Cancel an open order by its Alpaca order id."""
        return self._s.post("/orders/cancel", {"pod_id": self.pod_id, "order_id": order_id})

    # ── Read ─────────────────────────────────────────────────────────────────

    def account(self) -> AccountSummary:
        """Live equity, cash, and buying power for the pod."""
        return AccountSummary(self._s.get(f"/pods/{self.pod_id}/account"))

    def positions(self) -> PositionList:
        """Live open positions for the pod."""
        return PositionList(self._s.get(f"/pods/{self.pod_id}/positions"))

    def price(self, symbol):
        """Latest trade price."""
        return self._s.get("/market/price", {"symbol": symbol, "pod_id": self.pod_id})

    def bars(self, symbol, days=30):
        """Daily OHLCV bars."""
        return self._s.get("/market/bars", {"symbol": symbol, "pod_id": self.pod_id, "days": days})

    def sync(self) -> SyncResult:
        """Pull the pod's positions + NAV from Alpaca into the dashboard."""
        return SyncResult(self._s.post(f"/sync/{self.pod_id}"))
