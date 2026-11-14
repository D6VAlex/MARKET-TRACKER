from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from market_tracker.infrastructure.cache import QuoteCache
from market_tracker.infrastructure.database import Database
from market_tracker.infrastructure.health_monitor import ProviderHealthMonitor
from market_tracker.models import (AssetClass, DataFreshness, Quote, utcnow)
from market_tracker.services.price_aggregator import PriceAggregator
from market_tracker.services.validation_service import ValidationService


def make_quote(symbol: str = "BTC", price: float = 50000.0, *,
               source: str = "test", asset_class=AssetClass.CRYPTO,
               age_seconds: float = 0.0, freshness=DataFreshness.REALTIME,
               **overrides) -> Quote:
    kwargs = dict(
        symbol=symbol, asset_class=asset_class,
        price=price if price is not None else 1.0,
        source=source, source_ts=utcnow() - timedelta(seconds=age_seconds),
        freshness=freshness,
    )
    if "price" in overrides:
        kwargs["price"] = overrides.pop("price")
    kwargs.update(overrides)
    return Quote(**kwargs)


class FakeHttpClient:

    def __init__(self) -> None:
        self.routes: list[tuple[str, object]] = []
        self.requests: list[str] = []
        self.total_requests = 0
        self.total_failures = 0

    def route(self, substring: str, result) -> None:
        self.routes.append((substring, result))

    def _lookup(self, url: str):
        for substring, result in self.routes:
            if substring in url:
                return result
        raise AssertionError(f"no route for {url}")

    async def get_json(self, url, *, params=None, headers=None, provider="",
                       raw_text=False):
        self.requests.append(url)
        self.total_requests += 1
        result = self._lookup(url)
        if isinstance(result, BaseException):
            self.total_failures += 1
            raise result
        if callable(result) and not isinstance(result, type):
            result = result()
        if isinstance(result, type) and issubclass(result, BaseException):
            self.total_failures += 1
            raise result("scripted failure")
        return result

    async def get_text(self, url, *, params=None, headers=None, provider=""):
        body = await self.get_json(url, params=params, headers=headers,
                                   provider=provider, raw_text=True)
        if not isinstance(body, str):
            raise AssertionError(f"routed value is not text for {url}")
        return body

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_http() -> FakeHttpClient:
    return FakeHttpClient()


@pytest.fixture
def cache() -> QuoteCache:
    return QuoteCache()


@pytest.fixture
def health() -> ProviderHealthMonitor:
    return ProviderHealthMonitor()


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def validation(cache) -> ValidationService:
    return ValidationService(cache)


@pytest.fixture
def aggregator(cache, validation) -> PriceAggregator:
    return PriceAggregator(cache, validation)
