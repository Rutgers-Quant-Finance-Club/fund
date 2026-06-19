"""Alpaca access, per pod.

Each pod has its own Alpaca (paper) account. Credentials come from
pod_alpaca_credentials; if a pod has no row, we fall back to the ALPACA_API_KEY /
ALPACA_API_SECRET env vars so a single pod can be validated end-to-end without
storing secrets in the DB.

This is the ONLY place Alpaca keys are used. They never leave the backend.
"""
from datetime import datetime, timedelta, timezone
import logging
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

from fastapi import HTTPException

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    GetPortfolioHistoryRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from .config import get_settings
from . import db
from .accounting import parse_instrument, parse_ts

logger = logging.getLogger(__name__)

# ── Credential + client cache ─────────────────────────────────────────────────
# _resolve_creds is called on every Alpaca call (trading_client, data_client,
# _alpaca_headers). Without caching this hits the DB O(N × calls/snapshot).
# TTL of 5 minutes is safe — credentials rarely rotate, and the admin endpoint
# calls invalidate_pod_creds_cache() when they do.

_CREDS_CACHE: dict[str, tuple[str, str, float]] = {}  # pod_id -> (key, secret, expire_at)
_CREDS_TTL = 300.0

_TRADING_CLIENTS: dict[str, TradingClient] = {}  # cache key -> client
_DATA_CLIENTS: dict[str, StockHistoricalDataClient] = {}


def invalidate_pod_creds_cache(pod_id: str) -> None:
    """Call after updating a pod's Alpaca credentials so the cache is flushed."""
    _CREDS_CACHE.pop(pod_id, None)
    # Remove any cached SDK clients that used this pod's credentials.
    for d in (_TRADING_CLIENTS, _DATA_CLIENTS):
        for k in [k for k in d if k.startswith(f"{pod_id}:")]:
            d.pop(k, None)


def _log_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _order_debug(order) -> dict:
    fields = (
        "id", "client_order_id", "created_at", "updated_at", "submitted_at",
        "filled_at", "expired_at", "canceled_at", "failed_at", "replaced_at",
        "asset_id", "symbol", "asset_class", "side", "order_type", "type",
        "qty", "notional", "filled_qty", "filled_avg_price", "limit_price",
        "stop_price", "status", "time_in_force", "extended_hours", "legs",
    )
    return {
        field: _log_value(getattr(order, field, None))
        for field in fields
        if getattr(order, field, None) is not None
    }


def _order_request_debug(req) -> dict:
    if hasattr(req, "model_dump"):
        data = req.model_dump(exclude_none=True)
    elif hasattr(req, "dict"):
        data = req.dict(exclude_none=True)
    else:
        data = {k: v for k, v in vars(req).items() if v is not None}
    return {key: _log_value(value) for key, value in data.items()}


def _position_debug(position) -> dict:
    fields = (
        "asset_id", "symbol", "exchange", "asset_class", "asset_marginable",
        "qty", "avg_entry_price", "side", "market_value", "cost_basis",
        "unrealized_pl", "unrealized_plpc", "unrealized_intraday_pl",
        "unrealized_intraday_plpc", "current_price", "lastday_price",
        "change_today", "qty_available",
    )
    return {
        field: _log_value(getattr(position, field, None))
        for field in fields
        if getattr(position, field, None) is not None
    }


# ── Credential resolution ────────────────────────────────────────────────────

def _resolve_creds(pod_id: str):
    now = time.time()
    cached = _CREDS_CACHE.get(pod_id)
    if cached and cached[2] > now:
        return cached[0], cached[1]

    creds = db.get_pod_alpaca(pod_id)
    if creds and creds.get("api_key") and creds.get("api_secret"):
        key, secret = creds["api_key"], creds["api_secret"]
        _CREDS_CACHE[pod_id] = (key, secret, now + _CREDS_TTL)
        return key, secret
    s = get_settings()
    if s.alpaca_api_key and s.alpaca_api_secret:
        # Env-level fallback: cache under a stable sentinel so we don't query
        # the DB on every call when a pod has no row in pod_alpaca_credentials.
        key, secret = s.alpaca_api_key, s.alpaca_api_secret
        _CREDS_CACHE[pod_id] = (key, secret, now + _CREDS_TTL)
        return key, secret
    raise HTTPException(
        status_code=400,
        detail=("Pod has no Alpaca credentials. Set them via "
                "POST /admin/pods/{id}/alpaca, or set ALPACA_API_KEY/SECRET in the backend env."),
    )


def trading_client(pod_id: str) -> TradingClient:
    key, secret = _resolve_creds(pod_id)
    cache_key = f"{pod_id}:{key[:8]}"
    if cache_key not in _TRADING_CLIENTS:
        settings = get_settings()
        _TRADING_CLIENTS[cache_key] = TradingClient(
            key, secret,
            paper=settings.alpaca_paper,
            url_override=settings.alpaca_trading_base_url,
        )
    return _TRADING_CLIENTS[cache_key]


def data_client(pod_id: str) -> StockHistoricalDataClient:
    key, secret = _resolve_creds(pod_id)
    cache_key = f"{pod_id}:{key[:8]}"
    if cache_key not in _DATA_CLIENTS:
        _DATA_CLIENTS[cache_key] = StockHistoricalDataClient(key, secret)
    return _DATA_CLIENTS[cache_key]


def _alpaca_headers(pod_id: str) -> dict:
    key, secret = _resolve_creds(pod_id)
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "accept": "application/json",
    }


def _get_json(url: str, headers: dict) -> dict | list:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


# ── Orders ───────────────────────────────────────────────────────────────────

def _tif(value: str) -> TimeInForce:
    return TimeInForce(value.lower())


def submit_order(pod_id: str, *, symbol: str, side: str, qty: float = None,
                 notional: float = None, order_type: str = "market",
                 limit_price: float = None, time_in_force: str = "day"):
    tc = trading_client(pod_id)
    sym = symbol.upper()
    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

    if order_type.lower() == "limit":
        if limit_price is None:
            raise HTTPException(400, "limit_price is required for limit orders.")
        req = LimitOrderRequest(symbol=sym, qty=qty, side=order_side,
                                time_in_force=_tif(time_in_force), limit_price=limit_price)
    else:
        req = MarketOrderRequest(symbol=sym, qty=qty, notional=notional,
                                 side=order_side, time_in_force=_tif(time_in_force))
    try:
        logger.info(
            "Submitting Alpaca order pod_id=%s request=%s",
            pod_id,
            _order_request_debug(req),
        )
        order = tc.submit_order(req)
        logger.info(
            "Received Alpaca submit_order response pod_id=%s order=%s",
            pod_id,
            _order_debug(order),
        )
        refreshed = _refresh_order(tc, order, pod_id=pod_id)
        logger.info(
            "Final Alpaca order used for trade log pod_id=%s order=%s",
            pod_id,
            _order_debug(refreshed),
        )
        return refreshed
    except Exception as e:
        logger.exception(
            "Alpaca rejected order pod_id=%s symbol=%s side=%s qty=%s notional=%s order_type=%s time_in_force=%s",
            pod_id, sym, side, qty, notional, order_type, time_in_force,
        )
        raise HTTPException(status_code=400, detail=f"Alpaca rejected the order: {e}")


def _refresh_order(tc: TradingClient, order, pod_id: str = None):
    """Briefly refresh a submitted order so market fills have qty/price when available."""
    order_id = getattr(order, "id", None)
    if not order_id:
        return order
    for _ in range(3):
        status = str(getattr(getattr(order, "status", None), "value", getattr(order, "status", ""))).lower()
        if status in {"filled", "partially_filled", "canceled", "expired", "rejected"}:
            logger.info(
                "Alpaca order refresh complete pod_id=%s order_id=%s status=%s",
                pod_id,
                order_id,
                status,
            )
            return order
        time.sleep(0.35)
        try:
            order = tc.get_order_by_id(order_id)
            logger.info(
                "Refreshed Alpaca order pod_id=%s order_id=%s attempt=%s order=%s",
                pod_id,
                order_id,
                _ + 1,
                _order_debug(order),
            )
        except Exception:
            logger.exception(
                "Failed to refresh Alpaca order pod_id=%s order_id=%s",
                pod_id,
                order_id,
            )
            return order
    return order


def _num(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def to_trade_row(order, order_type_label: str, asset_class: str,
                 requested_qty: float = None, requested_notional: float = None) -> dict:
    """Map an Alpaca order onto the `trades` table columns."""
    instrument = parse_instrument(order.symbol, asset_class)
    multiplier = float(instrument["multiplier"])
    qty          = _num(getattr(order, "qty", None)) or requested_qty
    filled_qty   = _num(getattr(order, "filled_qty", None))
    filled_price = _num(getattr(order, "filled_avg_price", None))
    limit_price  = _num(getattr(order, "limit_price", None))

    quantity = qty if qty is not None else filled_qty
    price    = filled_price if filled_price is not None else limit_price
    notional = _num(getattr(order, "notional", None)) or requested_notional or (
        round(abs(quantity * price * multiplier), 2) if (quantity is not None and price is not None) else None
    )
    submitted = getattr(order, "submitted_at", None) or getattr(order, "created_at", None)
    executed_at = submitted.isoformat() if submitted else datetime.now(timezone.utc).isoformat()

    row = {
        "alpaca_order_id": str(order.id),
        "symbol":          order.symbol,
        "side":            order.side.value.lower(),
        "order_type":      order_type_label,
        "quantity":        quantity,
        "price":           price,
        "notional":        notional,
        "limit_price":     limit_price,
        "filled_qty":      filled_qty,
        "status":          order.status.value if order.status else None,
        "asset_class":     asset_class,
        "multiplier":      multiplier,
        "instrument_type": instrument["instrument_type"],
        "underlying_symbol": instrument["underlying_symbol"],
        "option_expiration": instrument["option_expiration"],
        "option_type": instrument["option_type"],
        "option_strike": instrument["option_strike"],
        "filled_at": filled_at.isoformat() if (filled_at := getattr(order, "filled_at", None)) else None,
        "executed_at":     executed_at,
    }
    logger.info(
        "Mapped Alpaca order to trade row order_id=%s requested_qty=%s requested_notional=%s row=%s",
        row["alpaca_order_id"],
        requested_qty,
        requested_notional,
        row,
    )
    return row


def cancel_order(pod_id: str, order_id: str) -> None:
    trading_client(pod_id).cancel_order_by_id(order_id)


# ── Account, positions, history ──────────────────────────────────────────────

def get_account(pod_id: str) -> dict:
    acct = trading_client(pod_id).get_account()
    last_equity = float(acct.last_equity) if getattr(acct, "last_equity", None) else None
    equity = float(acct.equity)
    row = {
        "equity": equity,
        "cash": float(acct.cash),
        "buying_power": float(acct.buying_power),
        "portfolio_value": float(acct.portfolio_value),
        "last_equity": last_equity,
        "session_return": ((equity / last_equity) - 1) if last_equity else None,
        "status": str(acct.status),
    }
    logger.info("Fetched Alpaca account pod_id=%s account=%s", pod_id, row)
    return row


def get_fill_activities(pod_id: str, days: int = 90) -> list[dict]:
    """Fetch Alpaca FILL activities, paginating through all results."""
    settings = get_settings()
    date = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 90)))).strftime("%Y-%m-%d")
    base_url = settings.alpaca_trading_base_url.rstrip("/")
    headers = _alpaca_headers(pod_id)
    rows: list[dict] = []
    page_token: str | None = None

    while True:
        params: dict = {"date": date, "direction": "asc", "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        url = f"{base_url}/v2/account/activities/FILL?{urlencode(params)}"
        try:
            data = _get_json(url, headers)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not fetch Alpaca fill activities: {e}")
        if isinstance(data, dict):
            data = data.get("activities") or data.get("data") or []
        if not data:
            break
        for activity in data:
            symbol = str(activity.get("symbol") or "").upper()
            qty = activity.get("qty")
            price = activity.get("price")
            if not symbol or qty is None or price is None:
                continue
            rows.append({
                "fill_id": activity.get("id"),
                "alpaca_order_id": activity.get("order_id"),
                "symbol": symbol,
                "side": activity.get("side"),
                "quantity": abs(float(qty)),
                "price": float(price),
                "filled_at": activity.get("transaction_time") or activity.get("date"),
                "raw": activity,
            })
        if len(data) < 100:
            break
        # Alpaca paginates with the ID of the last item returned.
        page_token = data[-1].get("id")
        if not page_token:
            break

    return rows


def get_positions(pod_id: str) -> list:
    positions = trading_client(pod_id).get_all_positions()
    logger.info(
        "Fetched Alpaca positions pod_id=%s raw_positions=%s",
        pod_id,
        [_position_debug(p) for p in positions],
    )
    symbols = [p.symbol for p in positions]
    try:
        latest_prices = get_latest_prices(pod_id, symbols) if symbols else {}
    except Exception:
        latest_prices = {}
    rows = []
    for p in positions:
        instrument = parse_instrument(p.symbol)
        multiplier = float(instrument.get("multiplier") or 1)
        quantity = _num(getattr(p, "qty", None)) or 0.0
        avg_entry = _num(getattr(p, "avg_entry_price", None)) or 0.0
        alpaca_current = _num(getattr(p, "current_price", None))
        latest_price = latest_prices.get(p.symbol)
        current_price = alpaca_current if alpaca_current is not None else latest_price
        market_value = _num(getattr(p, "market_value", None))
        if market_value is None and current_price is not None:
            market_value = quantity * current_price * multiplier
        unrealized_pnl = _num(getattr(p, "unrealized_pl", None))
        if unrealized_pnl is None and current_price is not None:
            unrealized_pnl = (current_price - avg_entry) * quantity * multiplier
        rows.append({
            "symbol":          p.symbol,
            "instrument_type": instrument["instrument_type"],
            "underlying_symbol": instrument["underlying_symbol"],
            "quantity":        quantity,
            "avg_entry_price": avg_entry,
            "current_price":   round(current_price, 4) if current_price is not None else None,
            "market_value":    round(market_value, 2) if market_value is not None else None,
            "cost_basis":      _num(getattr(p, "cost_basis", None)),
            "multiplier":      multiplier,
            "unrealized_pnl":  round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
        })
    logger.info("Mapped Alpaca positions pod_id=%s rows=%s", pod_id, rows)
    return rows


def get_nav_history(pod_id: str, days: int = 365) -> list:
    """Daily NAV rows from Alpaca's portfolio history."""
    tc = trading_client(pod_id)
    date_start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    hist = tc.get_portfolio_history(
        GetPortfolioHistoryRequest(date_start=date_start, timeframe="1D")
    )
    rows = []
    prev_equity = None
    for ts, equity in zip(hist.timestamp, hist.equity):
        if equity is None:
            continue
        equity = float(equity)
        date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        daily_return = ((equity / prev_equity - 1) if prev_equity else None)
        rows.append({
            "date": date,
            "nav": round(equity, 2),
            "cash": 0,
            "daily_return": round(daily_return, 6) if daily_return is not None else None,
        })
        prev_equity = equity
    return rows


def get_intraday_nav_history(pod_id: str, minutes: int = 390) -> list:
    """Minute NAV rows from Alpaca portfolio history for live charts."""
    tc = trading_client(pod_id)
    minutes = max(1, min(int(minutes or 390), 1440))
    date_start = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d")
    hist = tc.get_portfolio_history(
        GetPortfolioHistoryRequest(date_start=date_start, timeframe="1Min")
    )
    rows = []
    prev_equity = None
    for ts, equity in zip(hist.timestamp, hist.equity):
        if equity is None:
            continue
        equity = float(equity)
        timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        minute_return = ((equity / prev_equity - 1) if prev_equity else None)
        rows.append({
            "timestamp": timestamp,
            "nav": round(equity, 2),
            "minute_return": round(minute_return, 6) if minute_return is not None else None,
        })
        prev_equity = equity
    return rows


def get_position_notional_history(pod_id: str, minutes: int = 390) -> list:
    """Minute-level gross/net notional for current holdings using 1Min bars."""
    positions = trading_client(pod_id).get_all_positions()
    holdings = {p.symbol: float(p.qty) for p in positions if float(p.qty) != 0}
    return get_notional_history_for_holdings(pod_id, holdings, minutes)


def get_notional_history_for_holdings(pod_id: str, holdings: dict[str, float], minutes: int = 390) -> list:
    """Minute-level gross/net notional for provided holdings using 1Min bars."""
    if not holdings:
        return []

    minutes = max(1, min(int(minutes or 390), 1440))
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    option_holdings = {
        symbol: qty for symbol, qty in holdings.items()
        if parse_instrument(symbol).get("instrument_type") == "option"
    }
    stock_holdings = {symbol: qty for symbol, qty in holdings.items() if symbol not in option_holdings}

    by_ts: dict[str, dict] = {}
    if stock_holdings:
        dc = data_client(pod_id)
        req = StockBarsRequest(
            symbol_or_symbols=list(stock_holdings.keys()),
            timeframe=TimeFrame.Minute,
            start=start,
        )
        bars_by_symbol = dc.get_stock_bars(req).data
        _accumulate_notional_bars(by_ts, bars_by_symbol, stock_holdings, multiplier=1)

    if option_holdings:
        try:
            option_bars = get_option_bars(pod_id, list(option_holdings.keys()), start)
            for symbol, bars in option_bars.items():
                qty = option_holdings.get(symbol, 0)
                for bar in bars:
                    ts = bar["timestamp"]
                    row = by_ts.setdefault(ts, {"timestamp": ts, "gross_notional": 0.0, "net_notional": 0.0})
                    value = qty * float(bar["close"]) * 100
                    row["gross_notional"] += abs(value)
                    row["net_notional"] += value
        except Exception:
            latest = get_latest_prices(pod_id, list(option_holdings.keys()))
            ts = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
            row = by_ts.setdefault(ts, {"timestamp": ts, "gross_notional": 0.0, "net_notional": 0.0})
            for symbol, qty in option_holdings.items():
                if symbol in latest:
                    value = qty * latest[symbol] * 100
                    row["gross_notional"] += abs(value)
                    row["net_notional"] += value

    return [
        {
            "timestamp": row["timestamp"],
            "gross_notional": round(row["gross_notional"], 2),
            "net_notional": round(row["net_notional"], 2),
        }
        for row in sorted(by_ts.values(), key=lambda r: r["timestamp"])
    ]


def _accumulate_notional_bars(by_ts: dict, bars_by_symbol: dict, holdings: dict[str, float], multiplier: float) -> None:
    for symbol, bars in bars_by_symbol.items():
        qty = holdings.get(symbol, 0)
        for bar in bars:
            ts = bar.timestamp.replace(second=0, microsecond=0).isoformat()
            row = by_ts.setdefault(ts, {"timestamp": ts, "gross_notional": 0.0, "net_notional": 0.0})
            value = qty * float(bar.close) * multiplier
            row["gross_notional"] += abs(value)
            row["net_notional"] += value


def get_minute_closes(
    pod_id: str, symbols: list[str], start: datetime,
) -> dict[str, list[tuple[datetime, float]]]:
    """1Min close series per symbol — the market data behind the live NAV chart.

    Stocks come from the bars API (falling back to the free IEX feed when the
    account's plan rejects recent SIP data); options from the options bars API.
    Returns {symbol: [(minute_timestamp, close), ...]} sorted by time.
    """
    upper = [s.upper() for s in symbols]
    option_symbols = [s for s in upper if parse_instrument(s)["instrument_type"] == "option"]
    stock_symbols = [s for s in upper if s not in option_symbols]
    out: dict[str, list[tuple[datetime, float]]] = {}

    if stock_symbols:
        dc = data_client(pod_id)
        req = StockBarsRequest(symbol_or_symbols=stock_symbols, timeframe=TimeFrame.Minute, start=start)
        try:
            bars_by_symbol = dc.get_stock_bars(req).data
        except Exception:
            req = StockBarsRequest(
                symbol_or_symbols=stock_symbols, timeframe=TimeFrame.Minute,
                start=start, feed=DataFeed.IEX,
            )
            bars_by_symbol = dc.get_stock_bars(req).data
        for sym, bars in bars_by_symbol.items():
            out[sym] = [
                (b.timestamp.replace(second=0, microsecond=0), float(b.close)) for b in bars
            ]

    if option_symbols:
        try:
            for sym, rows in get_option_bars(pod_id, option_symbols, start).items():
                pts = []
                for row in rows:
                    ts = parse_ts(row.get("timestamp"))
                    if ts is not None:
                        pts.append((ts.replace(second=0, microsecond=0), float(row["close"])))
                if pts:
                    out[sym] = sorted(pts)
        except Exception:
            pass

    return {sym: sorted(pts) for sym, pts in out.items()}


def get_option_bars(pod_id: str, symbols: list[str], start: datetime) -> dict[str, list[dict]]:
    if not symbols:
        return {}
    params = urlencode({
        "symbols": ",".join(symbols),
        "timeframe": "1Min",
        "start": start.isoformat(),
    })
    url = f"https://data.alpaca.markets/v1beta1/options/bars?{params}"
    data = _get_json(url, _alpaca_headers(pod_id))
    bars = data.get("bars", data) if isinstance(data, dict) else {}
    result = {}
    for symbol, rows in (bars or {}).items():
        result[symbol] = [{
            "timestamp": (row.get("t") or row.get("timestamp")),
            "close": float(row.get("c") if row.get("c") is not None else row.get("close")),
        } for row in rows]
    return result


def get_option_latest_prices(pod_id: str, symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    params = urlencode({"symbols": ",".join(symbols)})
    url = f"https://data.alpaca.markets/v1beta1/options/trades/latest?{params}"
    data = _get_json(url, _alpaca_headers(pod_id))
    trades = data.get("trades", data) if isinstance(data, dict) else {}
    prices = {}
    for symbol, trade in (trades or {}).items():
        price = trade.get("p") if isinstance(trade, dict) else None
        if price is None and isinstance(trade, dict):
            price = trade.get("price")
        if price is not None:
            prices[symbol] = float(price)
    return prices


# ── Market data ──────────────────────────────────────────────────────────────

def get_price(pod_id: str, symbol: str) -> float:
    dc = data_client(pod_id)
    req = StockLatestTradeRequest(symbol_or_symbols=symbol.upper())
    trade = dc.get_stock_latest_trade(req)[symbol.upper()]
    return float(trade.price)


def get_latest_prices(pod_id: str, symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    upper = [s.upper() for s in symbols]
    option_symbols = [s for s in upper if parse_instrument(s).get("instrument_type") == "option"]
    stock_symbols = [s for s in upper if s not in option_symbols]
    prices = {}
    if option_symbols:
        try:
            prices.update(get_option_latest_prices(pod_id, option_symbols))
        except Exception:
            pass
    if not stock_symbols:
        return prices
    dc = data_client(pod_id)
    try:
        trades = dc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=stock_symbols))
        prices.update({symbol: float(trades[symbol].price) for symbol in stock_symbols if symbol in trades})
        return prices
    except Exception:
        for symbol in stock_symbols:
            try:
                prices[symbol] = get_price(pod_id, symbol)
            except Exception:
                continue
        return prices


def get_snapshots(pod_id: str, symbols: list[str]) -> dict[str, dict]:
    """Latest price + daily change for a basket of stocks (powers the ticker tape).

    Uses the multi-symbol snapshot endpoint so the whole basket is one request.
    Returns {symbol: {"price": float, "change_pct": float}}.
    """
    if not symbols:
        return {}
    upper = [s.upper() for s in symbols]
    params = urlencode({"symbols": ",".join(upper)})
    url = f"https://data.alpaca.markets/v2/stocks/snapshots?{params}"
    data = _get_json(url, _alpaca_headers(pod_id))
    snapshots = data.get("snapshots", data) if isinstance(data, dict) else {}
    out: dict[str, dict] = {}
    for symbol, snap in (snapshots or {}).items():
        if not isinstance(snap, dict):
            continue
        latest = (snap.get("latestTrade") or {}).get("p")
        if latest is None:
            latest = (snap.get("dailyBar") or {}).get("c")
        prev_close = (snap.get("prevDailyBar") or {}).get("c")
        if latest is None:
            continue
        change_pct = ((float(latest) / float(prev_close) - 1) * 100) if prev_close else 0.0
        out[symbol] = {"price": round(float(latest), 2), "change_pct": round(change_pct, 2)}
    return out


def get_bars(pod_id: str, symbol: str, days: int = 30) -> list:
    dc = data_client(pod_id)
    start = (datetime.now(timezone.utc) - timedelta(days=days))
    req = StockBarsRequest(symbol_or_symbols=symbol.upper(),
                           timeframe=TimeFrame.Day, start=start)
    bars = dc.get_stock_bars(req).data.get(symbol.upper(), [])
    return [{
        "date": b.timestamp.strftime("%Y-%m-%d"),
        "open": float(b.open), "high": float(b.high),
        "low": float(b.low), "close": float(b.close), "volume": float(b.volume),
    } for b in bars]
