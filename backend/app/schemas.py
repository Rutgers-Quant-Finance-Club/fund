from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class OrderRequest(BaseModel):
    pod_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: Optional[float] = None
    notional: Optional[float] = None
    order_type: Literal["market", "limit"] = "market"
    order_label: str = "market"
    limit_price: Optional[float] = None
    time_in_force: Literal["day", "gtc", "ioc", "fok", "opg", "cls"] = "day"

    @field_validator("symbol")
    @classmethod
    def symbol_uppercase(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol must not be empty")
        return v

    @field_validator("qty", "notional", "limit_price")
    @classmethod
    def must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("must be positive")
        return v


class CancelRequest(BaseModel):
    pod_id: str
    order_id: str


class CreatePodRequest(BaseModel):
    name: str
    asset_class: str
    benchmark_symbol: str = "SPY"
    description: Optional[str] = None
    allocated_capital: float = 0
    # Optionally attach Alpaca creds in the same call.
    alpaca_account_id: Optional[str] = None
    alpaca_api_key: Optional[str] = None
    alpaca_api_secret: Optional[str] = None


class SetAlpacaRequest(BaseModel):
    alpaca_account_id: Optional[str] = None
    alpaca_api_key: Optional[str] = None
    alpaca_api_secret: Optional[str] = None


class CapitalRequest(BaseModel):
    amount: float
    note: Optional[str] = None


class MembershipRequest(BaseModel):
    pod_id: str
    trader_id: str
    role: str = "trader"


class PortalLogin(BaseModel):
    credential: str


class TraderLogin(BaseModel):
    email: str
    password: str


class CreateTraderRequest(BaseModel):
    email: str
    password: str
    display_name: str
    is_admin: bool = False


class CreateTraderApiKeyRequest(BaseModel):
    name: str = "default"
