"""Pod-level risk/return metrics, computed from a NAV series."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _sharpe(returns: pd.Series, rf: float = 0.05) -> float:
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    excess = returns - rf / 252
    return round(float((excess.mean() / excess.std()) * np.sqrt(252)), 4)


def _sortino(returns: pd.Series, rf: float = 0.05):
    if len(returns) < 2:
        return 0.0
    excess = returns - rf / 252
    downside = excess[excess < 0]
    if len(downside) == 0 or downside.std() == 0:
        return None  # no downside → undefined; store null
    return round(float((excess.mean() / downside.std()) * np.sqrt(252)), 4)


def _max_drawdown(returns: pd.Series) -> float:
    if len(returns) < 2:
        return 0.0
    cumulative = (1 + returns).cumprod()
    drawdown = (cumulative - cumulative.cummax()) / cumulative.cummax()
    return round(float(drawdown.min()), 4)


def _volatility(returns: pd.Series) -> float:
    if len(returns) < 2:
        return 0.0
    return round(float(returns.std() * np.sqrt(252)), 4)


def _sanitize(v):
    """Replace nan/inf with None so the dict is always JSON-safe."""
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return None
    return v


def compute(nav_rows: list, rf: float = 0.05, trade_count: int = 0) -> dict | None:
    """nav_rows: list of {date, nav, daily_return}. Returns a metrics dict."""
    if not nav_rows:
        return None
    nav = pd.Series([float(r["nav"]) for r in nav_rows])
    ret = nav.pct_change().dropna()
    n = len(ret)

    start, end = float(nav.iloc[0]), float(nav.iloc[-1])
    cum_return = (end / start - 1) if start else 0.0
    ann_return = ((1 + cum_return) ** (252.0 / n) - 1) if n else 0.0
    max_dd = _max_drawdown(ret)
    calmar = (ann_return / abs(max_dd)) if max_dd else None
    var_95 = float(ret.quantile(0.05)) if n else None
    win_rate = float((ret > 0).mean()) if n else None

    raw = {
        "as_of_date":        nav_rows[-1]["date"],
        "cumulative_return": round(cum_return, 6),
        "annualized_return": round(ann_return, 6),
        "volatility":        _volatility(ret),
        "sharpe":            _sharpe(ret, rf),
        "sortino":           _sortino(ret, rf),
        "beta":              None,
        "alpha":             None,
        "max_drawdown":      max_dd,
        "calmar":            round(calmar, 4) if calmar is not None else None,
        "var_95":            round(var_95, 6) if var_95 is not None else None,
        "win_rate":          round(win_rate, 4) if win_rate is not None else None,
        "trade_count":       int(trade_count),
    }
    return {k: _sanitize(v) for k, v in raw.items()}
