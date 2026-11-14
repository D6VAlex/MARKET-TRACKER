from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest

from market_tracker.infrastructure.cache import QuoteCache
from market_tracker.infrastructure.circuit_breaker import (BreakerState,
                                                          CircuitBreaker)
from market_tracker.infrastructure.database import Database
from market_tracker.infrastructure.retry_manager import RetryManager
from market_tracker.models import AssetClass, DataFreshness, utcnow
from market_tracker.services.price_aggregator import PriceAggregator
from market_tracker.services.validation_service import ValidationService
from market_tracker.tests.conftest import make_quote
from market_tracker.utils.exceptions import (AuthError, CircuitOpenError,
                                             RateLimitError)


class TestRetryManager:
    async def test_succeeds_after_transient_failures(self):
        rm = RetryManager(max_attempts=4, base=0.01, cap=0.02)
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        assert await rm.run(flaky, label="flaky") == "ok"
        assert calls["n"] == 3

    async def test_gives_up_after_max_attempts(self):
        rm = RetryManager(max_attempts=3, base=0.01, cap=0.02)

        async def dead():
            raise ConnectionError("dead")

        with pytest.raises(ConnectionError):
            await rm.run(dead, label="dead")

    async def test_auth_errors_not_retried(self):
        rm = RetryManager(max_attempts=5, base=0.01, cap=0.02)
        calls = {"n": 0}

        async def unauthorized():
            calls["n"] += 1
            raise AuthError()

        with pytest.raises(AuthError):
            await rm.run(unauthorized, label="auth")
        assert calls["n"] == 1

    async def test_suppress_mode_returns_none(self):
        rm = RetryManager(max_attempts=2, base=0.01, cap=0.02)

        async def dead():
            raise ConnectionError("dead")

        assert await rm.run(dead, label="dead", suppress=True) is None

    async def test_backoff_is_increasing(self):
        rm = RetryManager(max_attempts=4, base=0.05, cap=1.0,
                          jitter_ratio=0.0)
        delays = [rm._delay(a, None) for a in (1, 2, 3)]
        assert delays[0] < delays[1] < delays[2]

    async def test_retry_after_header_respected(self):
        rm = RetryManager(max_attempts=3, base=0.01, cap=6.0)
        d = rm._delay(1, retry_after=0.3)
        assert 0.29 <= d <= 0.31


class TestCircuitBreaker:
    async def test_full_lifecycle(self):
        b = CircuitBreaker("t", failure_threshold=3, recovery_timeout=0.15,
                           half_open_successes=2)
        assert b.state is BreakerState.CLOSED

        async def fail():
            raise ConnectionError("x")

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await b.call(fail)
        assert b.state is BreakerState.OPEN

        with pytest.raises(CircuitOpenError):
            await b.call(fail)

        await asyncio.sleep(0.16)
        assert b.state is BreakerState.HALF_OPEN

        async def ok():
            return 1

        await b.call(ok)
        assert b.state is BreakerState.HALF_OPEN
        await b.call(ok)
        assert b.state is BreakerState.CLOSED

    async def test_half_open_failure_reopens(self):
        b = CircuitBreaker("t", failure_threshold=2, recovery_timeout=0.1,
                           half_open_successes=1)

        async def fail():
            raise ConnectionError("x")

        async def ok():
            return 1

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await b.call(fail)
        await asyncio.sleep(0.11)
        assert b.state is BreakerState.HALF_OPEN
        with pytest.raises(ConnectionError):
            await b.call(fail)
        assert b.state is BreakerState.OPEN

    async def test_failures_below_threshold_keep_closed(self):
        b = CircuitBreaker("t", failure_threshold=5)

        async def fail():
            raise ConnectionError("x")

        for _ in range(4):
            with pytest.raises(ConnectionError):
                await b.call(fail)
        assert b.state is BreakerState.CLOSED


class TestCache:
    def test_put_get_roundtrip(self):
        c = QuoteCache()
        c.put(make_quote(symbol="BTC", price=1.0, source="a"))
        q = c.get(AssetClass.CRYPTO, "BTC")
        assert q is not None and q.price == 1.0

    def test_newest_wins_per_key(self):
        c = QuoteCache()
        c.put(make_quote(symbol="BTC", price=1.0, source="a",
                         age_seconds=60))
        c.put(make_quote(symbol="BTC", price=2.0, source="a"))
        assert c.get(AssetClass.CRYPTO, "BTC").price == 2.0

    def test_eviction_after_ttl(self):
        c = QuoteCache(max_age_seconds=0.05)
        c.put(make_quote(symbol="BTC", price=1.0, source="a"))
        assert len(c) == 1
        time.sleep(0.08)
        assert c.evict_expired() == 1
        assert len(c) == 0

    def test_by_symbol_and_class_merges_providers(self):
        c = QuoteCache()
        c.put(make_quote(symbol="BTC", price=1.0, source="a"))
        c.put(make_quote(symbol="BTC", price=1.01, source="b"))
        c.put(make_quote(symbol="ETH", price=100.0, source="a"))
        quotes = c.get_by_symbol_and_class(
            make_quote(symbol="BTC", price=0.5, source="c"))
        assert {q.source for q in quotes} == {"a", "b"}


class TestDatabase:
    def test_record_and_query(self, db):
        quotes = [
            make_quote(symbol="BTC", price=50000, source="binance"),
            make_quote(symbol="ETH", price=3000, source="binance"),
        ]
        assert db.record_quotes(quotes) == 2
        lp = db.last_price(AssetClass.CRYPTO, "BTC")
        assert lp is not None and lp.price == 50000
        hist = db.price_history(AssetClass.CRYPTO, "BTC")
        assert len(hist) == 1

    def test_snapshot_roundtrip(self, db):
        quotes = [
            make_quote(symbol="EUR/USD", price=1.08,
                       asset_class=AssetClass.FOREX, source="er_api",
                       freshness=DataFreshness.DELAYED),
        ]
        assert db.save_snapshot(AssetClass.FOREX, quotes)
        restored = db.load_snapshot(AssetClass.FOREX)
        assert len(restored) == 1
        assert restored[0].price == pytest.approx(1.08)
        assert restored[0].freshness is DataFreshness.DELAYED

    def test_empty_snapshot_returns_empty(self, db):
        assert db.load_snapshot(AssetClass.CRYPTO) == []

    def test_prune_history(self, db):
        q_old = make_quote(symbol="BTC", price=1.0, source="x")
        q_old = q_old.model_copy(
            update={"received_at": utcnow() - timedelta(days=30)})
        db.record_quotes([q_old])
        assert db.prune_history(keep_days=14) == 1
        assert db.price_history(AssetClass.CRYPTO, "BTC") == []


class TestAggregator:
    def test_median_robust_to_one_bad_feed(self, cache):
        cache.put(make_quote(symbol="BTC", price=50000, source="a"))
        cache.put(make_quote(symbol="BTC", price=50010, source="b"))
        cache.put(make_quote(symbol="BTC", price=80000, source="c"))
        vs = ValidationService(cache)
        agg = PriceAggregator(cache, vs)
        rows = agg.aggregate(AssetClass.CRYPTO)
        btc = next(r for r in rows if r.symbol == "BTC")
        assert btc.price == pytest.approx(50010)
        assert btc.source_count == 3
        assert btc.disagreement_pct > 50

    def test_single_provider(self, cache):
        cache.put(make_quote(symbol="BTC", price=50000, source="a"))
        vs = ValidationService(cache)
        agg = PriceAggregator(cache, vs)
        rows = agg.aggregate(AssetClass.CRYPTO)
        assert rows[0].price == 50000
        assert rows[0].source_count == 1

    def test_best_bid_ask_composite(self, cache):
        cache.put(make_quote(symbol="BTC", price=50000, source="a",
                             bid=49998, ask=50003))
        cache.put(make_quote(symbol="BTC", price=50001, source="b",
                             bid=49999, ask=50002))
        vs = ValidationService(cache)
        agg = PriceAggregator(cache, vs)
        btc = agg.aggregate(AssetClass.CRYPTO)[0]
        assert btc.bid == 49999
        assert btc.ask == 50002

    def test_stale_feed_marked(self, cache):
        cache.put(make_quote(symbol="BTC", price=50000, source="a",
                             age_seconds=100,
                             freshness=DataFreshness.REALTIME))
        vs = ValidationService(cache)
        agg = PriceAggregator(cache, vs)
        btc = agg.aggregate(AssetClass.CRYPTO)[0]
        assert btc.freshness is DataFreshness.STALE
        assert btc.stale is True

    def test_graceful_degradation_all_stale(self, cache):
        cache.put(make_quote(symbol="BTC", price=50000, source="a",
                             age_seconds=99999))
        vs = ValidationService(cache)
        agg = PriceAggregator(cache, vs)
        btc = agg.aggregate(AssetClass.CRYPTO)[0]
        assert btc.freshness is DataFreshness.OFFLINE
        assert btc.price == 50000
