from __future__ import annotations

from datetime import timedelta

import pytest

from market_tracker.models import AssetClass, DataFreshness, utcnow
from market_tracker.tests.conftest import make_quote
from market_tracker.utils.exceptions import (MalformedDataError,
                                             StaleDataError)


class TestStructural:
    def test_valid_quote_passes(self, validation):
        q = make_quote(price=50000.0)
        out = validation.validate_quote(q)
        assert out.price == 50000.0

    def test_zero_price_rejected(self, validation):
        from market_tracker.utils.validators import plausible_price
        q = make_quote(price=1.0)
        bad = q.model_copy(update={"price": 0.0})
        with pytest.raises(MalformedDataError):
            validation.validate_quote(bad)

    def test_negative_price_rejected(self, validation):
        q = make_quote(price=1.0)
        bad = q.model_copy(update={"price": -5.0})
        with pytest.raises(MalformedDataError):
            validation.validate_quote(bad)

    def test_absurd_price_rejected(self, validation):
        with pytest.raises(MalformedDataError):
            validation.validate_quote(make_quote(price=1e18))

    def test_crossed_bid_ask_rejected(self, validation):
        with pytest.raises(MalformedDataError):
            validation.validate_quote(
                make_quote(price=100.0, bid=101.0, ask=100.5))

    def test_wide_spread_rejected(self, validation):
        with pytest.raises(MalformedDataError):
            validation.validate_quote(
                make_quote(price=100.0, bid=90.0, ask=110.0))

    def test_tight_spread_accepted(self, validation):
        q = validation.validate_quote(
            make_quote(price=100.0, bid=99.95, ask=100.05))
        assert q.bid == 99.95


class TestStaleness:
    def test_fresh_quote_passes(self, validation):
        validation.validate_quote(make_quote(age_seconds=10))

    def test_old_quote_rejected(self, validation):
        with pytest.raises(StaleDataError):
            validation.validate_quote(make_quote(age_seconds=600))

    def test_delayed_provider_gets_extended_grace(self, validation):
        q = make_quote(age_seconds=26 * 3600,
                       freshness=DataFreshness.DELAYED)
        validation.validate_quote(q)

    def test_future_quote_rejected(self, validation):
        q = make_quote()
        future = q.model_copy(
            update={"source_ts": utcnow() + timedelta(minutes=30)})
        with pytest.raises(StaleDataError):
            validation.validate_quote(future)


class TestOutlier:
    def test_extreme_change_flagged_not_rejected(self, validation):
        q = make_quote(change_pct_24h=45.0)
        out = validation.validate_quote(q)
        assert out.change_pct_24h == 45.0
        assert validation.report.flagged_outlier >= 1

    def test_normal_change_not_flagged(self, validation):
        validation.validate_quote(make_quote(change_pct_24h=3.2))
        assert validation.report.flagged_outlier == 0


class TestCrossSource:
    def test_discrepancy_flagged(self, validation, cache):
        cache.put(make_quote(symbol="BTC", price=50000.0, source="prov_a"))
        q = make_quote(symbol="BTC", price=52000.0, source="prov_b")
        validation.validate_quote(q)
        assert validation.report.flagged_discrepancy >= 1

    def test_agreement_not_flagged(self, validation, cache):
        cache.put(make_quote(symbol="BTC", price=50000.0, source="prov_a"))
        validation.validate_quote(
            make_quote(symbol="BTC", price=50100.0, source="prov_b"))
        assert validation.report.flagged_discrepancy == 0

    def test_stale_reference_ignored(self, validation, cache):
        cache.put(make_quote(symbol="BTC", price=40000.0, source="prov_a",
                             age_seconds=7200))
        validation.validate_quote(
            make_quote(symbol="BTC", price=50000.0, source="prov_b"))
        assert validation.report.flagged_discrepancy == 0


class TestFreshnessAssessment:
    def test_live_within_stale_window(self, validation):
        q = make_quote(age_seconds=10, freshness=DataFreshness.LIVE)
        assert validation.assess_freshness(
            q, stale_after=45, dead_after=180) is DataFreshness.LIVE

    def test_stale_after_window(self, validation):
        q = make_quote(age_seconds=100, freshness=DataFreshness.REALTIME)
        assert validation.assess_freshness(
            q, stale_after=45, dead_after=180) is DataFreshness.STALE

    def test_offline_beyond_dead_window(self, validation):
        q = make_quote(age_seconds=500, freshness=DataFreshness.REALTIME)
        assert validation.assess_freshness(
            q, stale_after=45, dead_after=180) is DataFreshness.OFFLINE

    def test_delayed_keeps_label_within_day(self, validation):
        q = make_quote(age_seconds=3600 * 20,
                       freshness=DataFreshness.DELAYED)
        assert validation.assess_freshness(
            q, stale_after=45, dead_after=180) is DataFreshness.DELAYED


class TestBatch:
    def test_batch_separates_good_and_bad(self, validation):
        good = make_quote(symbol="BTC", price=100.0, source="x")
        bad = make_quote(symbol="ETH", price=1.0, source="x").model_copy(
            update={"price": 0.0})
        junk = make_quote(symbol="SOL", price=20.0, source="x",
                          age_seconds=9999)
        accepted, report = validation.validate_batch([good, bad, junk])
        syms = {q.symbol for q in accepted}
        assert syms == {"BTC"}
        assert report.rejected == 2
