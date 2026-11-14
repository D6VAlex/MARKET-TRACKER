from __future__ import annotations


class MarketTrackerError(Exception):

    category = "unknown"

    def __init__(self, message: str, *, provider: str = "", detail: object = None):
        super().__init__(message)
        self.provider = provider
        self.detail = detail

    def __str__(self) -> str:
        base = super().__str__()
        parts = [f"[{self.category}]"]
        if self.provider:
            parts.append(f"provider={self.provider}")
        parts.append(base)
        return " ".join(parts)


class NetworkError(MarketTrackerError):
    category = "network"


class DnsError(NetworkError):
    category = "dns"


class TimeoutError_(MarketTrackerError):
    category = "timeout"


class ConnectionLostError(NetworkError):
    category = "connection_lost"


class HttpError(MarketTrackerError):
    category = "http"

    def __init__(self, message: str, *, status: int = 0, provider: str = "", detail=None):
        super().__init__(message, provider=provider, detail=detail)
        self.status = status


class RateLimitError(HttpError):

    category = "rate_limit"

    def __init__(self, message: str = "rate limited (HTTP 429)", *, provider: str = "", detail=None):
        super().__init__(message, status=429, provider=provider, detail=detail)


class AuthError(HttpError):

    category = "auth"

    def __init__(self, message: str = "authentication failed", *, status: int = 401,
                 provider: str = "", detail=None):
        super().__init__(message, status=status, provider=provider, detail=detail)


class DataValidationError(MarketTrackerError):
    category = "validation"


class MalformedDataError(DataValidationError):
    category = "malformed"


class StaleDataError(DataValidationError):
    category = "stale"


class OutlierError(DataValidationError):
    category = "outlier"


class DiscrepancyError(DataValidationError):

    category = "discrepancy"


class DatabaseError(MarketTrackerError):
    category = "database"


class CircuitOpenError(MarketTrackerError):

    category = "circuit_open"


class AllProvidersFailedError(MarketTrackerError):
    category = "all_providers_failed"


class WebSocketError(MarketTrackerError):
    category = "websocket"


def categorize_exception(exc: BaseException) -> str:
    if isinstance(exc, MarketTrackerError):
        return exc.category
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "connection_lost"
    if isinstance(exc, (KeyError, IndexError)):
        return "missing_field"
    if isinstance(exc, ValueError):
        return "malformed"
    return "unexpected"
