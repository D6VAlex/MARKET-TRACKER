from __future__ import annotations

import abc
import asyncio
import time
from typing import Any, Callable

from market_tracker.infrastructure.connection_manager import HttpClient
from market_tracker.models import AssetClass, DataFreshness, Quote, utcnow
from market_tracker.utils.logger import get_logger


class BaseProvider(abc.ABC):
    provider_id: str = "base"
    display_name: str = "Base"
    asset_class: AssetClass = AssetClass.CRYPTO
    supports_websocket: bool = False
    delay_seconds: float = 0.0
    rate_limit_per_min: int | None = None
    limitations: str = ""

    is_primary: bool = False

    def __init__(self, http: HttpClient, health) -> None:
        self.http = http
        self.health = health
        self.log = get_logger(f"provider.{self.provider_id}")
        self.breaker = health.register(self.provider_id, self.display_name,
                                       self.asset_class,
                                       is_primary=self.is_primary)
        self._sym_fail_count: dict[str, int] = {}
        self._sym_skip_cycles: dict[str, int] = {}

    def sym_skipped(self, symbol: str) -> bool:
        remaining = self._sym_skip_cycles.get(symbol, 0)
        if remaining > 0:
            self._sym_skip_cycles[symbol] = remaining - 1
            return True
        return False

    def sym_failed(self, symbol: str, *, hard: bool = False) -> None:
        count = self._sym_fail_count.get(symbol, 0) + 1
        self._sym_fail_count[symbol] = count
        if hard or count >= 3:
            self._sym_skip_cycles[symbol] = 40 if hard else 12
            self._sym_fail_count[symbol] = 0
            self.log.info("%s: backing off symbol %s for %s cycles",
                          self.provider_id, symbol,
                          self._sym_skip_cycles[symbol])

    def sym_ok(self, symbol: str) -> None:
        self._sym_fail_count.pop(symbol, None)
        self._sym_skip_cycles.pop(symbol, None)

    @property
    def freshness(self) -> DataFreshness:
        if self.delay_seconds >= 60:
            return DataFreshness.DELAYED
        return DataFreshness.REALTIME

    async def fetch_batch(self, symbols: list[str] | None = None,
                          **kwargs: Any) -> list[Quote]:
        started = time.monotonic()
        self.health.record_request(self.provider_id)

        async def _do() -> list[Quote]:
            return await self._fetch(symbols=symbols, **kwargs)

        try:
            quotes = await self.breaker.call(_do)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self.health.record_failure(self.provider_id, exc)
            raise
        latency = (time.monotonic() - started) * 1000
        self.health.record_success(self.provider_id, latency_ms=latency)
        return quotes

    @abc.abstractmethod
    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        ...

    def _reject(self, reason: str) -> None:
        from market_tracker.utils.exceptions import MalformedDataError
        raise MalformedDataError(reason, provider=self.provider_id)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.provider_id}>"
