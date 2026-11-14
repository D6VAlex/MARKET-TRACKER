from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    STOCKS = "stocks"


class DataFreshness(str, Enum):
    LIVE = "live"
    REALTIME = "realtime"
    DELAYED = "delayed"
    STALE = "stale"
    OFFLINE = "offline"


FRESHNESS_LABEL = {
    DataFreshness.LIVE: "LIVE",
    DataFreshness.REALTIME: "REAL-TIME",
    DataFreshness.DELAYED: "DELAYED",
    DataFreshness.STALE: "STALE — last known good",
    DataFreshness.OFFLINE: "OFFLINE — no data",
}


class Quote(BaseModel):

    model_config = ConfigDict(frozen=True)

    symbol: str
    asset_class: AssetClass
    price: float = Field(gt=0)
    bid: float | None = Field(default=None)
    ask: float | None = Field(default=None)
    change_pct_24h: float | None = None
    volume_24h: float | None = None
    market_cap: float | None = None
    rank: int | None = None
    source: str
    source_ts: datetime
    received_at: datetime = Field(default_factory=utcnow)
    freshness: DataFreshness = DataFreshness.REALTIME

    @field_validator("bid", "ask", mode="before")
    @classmethod
    def _normalize_quote_side(cls, v: Any) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f <= 0 or not math.isfinite(f):
            return None
        return f

    @property
    def age_seconds(self) -> float:
        return max(0.0, (utcnow() - self.source_ts).total_seconds())

    def key(self) -> tuple[AssetClass, str]:
        return (self.asset_class, self.symbol)


class CryptoMeta(BaseModel):

    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str
    coingecko_id: str | None = None
    rank: int | None = None


class StockMeta(BaseModel):

    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str
    exchange: str = ""
    currency: str = "USD"
    sector: str = ""


class AggregatedQuote(BaseModel):

    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str = ""
    asset_class: AssetClass
    price: float
    bid: float | None = None
    ask: float | None = None
    change_pct_24h: float | None = None
    volume_24h: float | None = None
    market_cap: float | None = None
    rank: int | None = None

    sources: list[str] = Field(default_factory=list)
    source_count: int = 0
    disagreement_pct: float | None = None
    freshness: DataFreshness = DataFreshness.REALTIME
    source_ts: datetime | None = None
    last_update_ts: datetime | None = None

    prev_price: float | None = None

    @property
    def stale(self) -> bool:
        return self.freshness in (DataFreshness.STALE, DataFreshness.OFFLINE)


class ProviderHealth(BaseModel):

    provider_id: str
    display_name: str
    asset_class: AssetClass | None = None
    state: str = "unknown"
    breaker_state: str = "closed"
    last_success: datetime | None = None
    last_failure: datetime | None = None
    last_error: str | None = None
    last_error_category: str | None = None
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0
    latency_ms: float | None = None
    avg_latency_ms: float | None = None
    reliability: float = 1.0
    is_primary: bool = False
    supports_ws: bool = False
    ws_state: str = "n/a"


class SystemStatus(BaseModel):

    active_provider_crypto: str = "—"
    active_provider_forex: str = "—"
    active_provider_stocks: str = "—"
    backup_available_crypto: list[str] = Field(default_factory=list)
    backup_available_forex: list[str] = Field(default_factory=list)
    backup_available_stocks: list[str] = Field(default_factory=list)
    internet_online: bool | None = None
    internet_state_text: str = "UNKNOWN"
    last_sync: datetime | None = None
    failed_requests: int = 0
    total_requests: int = 0
    ws_states: dict[str, str] = Field(default_factory=dict)
    providers: list[ProviderHealth] = Field(default_factory=list)
    db_ok: bool = True
    cache_entries: int = 0
    stale_count: int = 0

    @property
    def uptime_text(self) -> str:
        return ""


class TickerCellUpdate(BaseModel):

    row_key: tuple[str, str]
    fields: dict[str, Any]
