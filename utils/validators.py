from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from market_tracker.utils.exceptions import MalformedDataError

_ISO_COMPAT = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
               "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ")


def is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return False


def to_float(value: Any, *, field: str, provider: str = "") -> float:
    if value is None:
        raise MalformedDataError(f"field '{field}' is None", provider=provider)
    if isinstance(value, bool):
        raise MalformedDataError(f"field '{field}' is bool", provider=provider)
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise MalformedDataError(
            f"field '{field}' not numeric: {value!r}", provider=provider) from None
    if not math.isfinite(out):
        raise MalformedDataError(f"field '{field}' not finite: {value!r}", provider=provider)
    return out


def in_range(value: float, lo: float, hi: float) -> bool:
    return lo <= value <= hi


def parse_iso_utc(text: str) -> datetime:
    if not isinstance(text, str) or not text:
        raise MalformedDataError(f"bad timestamp: {text!r}")
    cleaned = text.strip()
    dt: datetime | None = None
    for fmt in _ISO_COMPAT:
        try:
            dt = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        try:
            dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            raise MalformedDataError(f"unparsable timestamp: {text!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def epoch_to_utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def is_recent(ts: datetime, max_age_seconds: float, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return abs((now - ts).total_seconds()) <= max_age_seconds


def plausible_price(value: float, *, lo: float = 1e-12, hi: float = 1e15) -> bool:
    return is_finite_number(value) and lo <= value <= hi


def price_decimals(price: float) -> int:
    if not is_finite_number(price) or price <= 0:
        return 2
    if price < 0.01:
        return 8
    if price < 1:
        return 6
    if price < 100:
        return 4
    return 2


def spread_pct(bid: float, ask: float) -> float:
    if bid <= 0 or ask <= 0:
        raise MalformedDataError(f"non-positive quote bid={bid} ask={ask}")
    if bid > ask:
        raise MalformedDataError(f"crossed market bid={bid} > ask={ask}")
    mid = (bid + ask) / 2.0
    if mid <= 0:
        raise MalformedDataError("non-positive mid price")
    return (ask - bid) / mid * 100.0


def safe_percentage(value: Any, *, lo: float = -100.0, hi: float = 1000.0) -> float | None:
    if value is None:
        return None
    try:
        out = to_float(value, field="pct_change")
    except MalformedDataError:
        return None
    if not in_range(out, lo, hi):
        return None
    return out
