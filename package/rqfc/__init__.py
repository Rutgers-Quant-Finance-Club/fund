# rqfc — RQFC fund trading client
#
# A thin, authenticated client for the RQFC trading backend. Traders log in with
# credentials or an API key issued by an admin; the backend holds each pod's
# Alpaca keys and submits trades on their behalf. No Alpaca keys ever touch this
# client.
#
# Trader usage:
#   import rqfc
#   rqfc.login("alice@example.com", "password")
#   # or: rqfc.login(api_key="rqfc_...")
#   acct = rqfc.pod("Alpha Equities")   # a pod you're assigned to
#   acct.buy("AAPL", 10)
#   acct.positions()
#   acct.sync()                          # refresh the dashboard
#
# Admin usage:
#   rqfc.login("admin@example.com", "password")
#   admin = rqfc.admin()
#   pod = admin.create_pod("Vol Arb", "options", capital=100000,
#                          alpaca_api_key="PK...", alpaca_api_secret="...")
#   admin.add_trader(pod["id"], trader_id, role="trader")
#   admin.allocate_capital(pod["id"], 150000)
from __future__ import annotations

import os

from ._session import Session
from .client import Account
from .admin import Admin

__version__ = "1.0.0"
DEFAULT_BACKEND_URL = "https://fund-tkb1.onrender.com"

_session: Session | None = None


def configure(backend_url: str = None) -> Session:
    """Set backend connection details.

    Defaults to RQFC_BACKEND_URL, then the production RQFC API. Local dev can
    pass backend_url="http://localhost:8000".
    """
    global _session
    backend_url = backend_url or os.environ.get("RQFC_BACKEND_URL", DEFAULT_BACKEND_URL)
    _session = Session(backend_url)
    return _session


def login(email: str = None, password: str = None, *, api_key: str = None,
          backend_url: str = None) -> dict:
    """Authenticate and start a session. Returns your profile."""
    global _session
    if backend_url is not None or _session is None:
        sess = configure(backend_url)
    else:
        sess = _session
    if api_key:
        if email or password:
            raise ValueError("Use either email/password or api_key, not both.")
        sess.use_api_key(api_key)
    else:
        if not email or not password:
            raise ValueError("Call rqfc.login(email, password) or rqfc.login(api_key='rqfc_...').")
        profile = sess.login(email, password)
        if profile:
            me = profile
            tag = " (admin)" if me.get("is_admin") else ""
            print(
                f"Logged in as {me['display_name']}{tag}. "
                f"Pods: {[p['pods']['name'] for p in me.get('pods', [])] or 'none assigned'}"
            )
            return me

    me = sess.get("/me")
    tag = " (admin)" if me.get("is_admin") else ""
    print(f"Logged in as {me['display_name']}{tag}. Pods: {[p['pods']['name'] for p in me['pods']] or 'none assigned'}")
    return me


def _require_session() -> Session:
    if _session is None or not _session.access_token:
        raise RuntimeError(
            "Not logged in. Call rqfc.login(email, password) or "
            "rqfc.login(api_key='rqfc_...') first."
        )
    return _session


def pod(name_or_id: str) -> Account:
    """Select a pod to trade, by name or id."""
    return Account(_require_session(), name_or_id)


def admin() -> Admin:
    """Get the admin interface (requires an admin account)."""
    return Admin(_require_session())


def whoami() -> dict:
    """Your profile and pod assignments."""
    return _require_session().get("/me")


__all__ = [
    "DEFAULT_BACKEND_URL",
    "configure",
    "login",
    "pod",
    "admin",
    "whoami",
    "Account",
    "Admin",
]
