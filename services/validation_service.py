from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from market_tracker.config import CONFIG
from market_tracker.infrastructure.cache import QuoteCache
from market_tracker.models import (AssetClass, DataFreshness, Quote,
                                   utcnow)
from market_tracker.utils.exceptions import (DataValidationError,
                                             MalformedDataError,
                                             OutlierError, StaleDataError)
from market_tracker.utils.logger import get_logger
from market_tracker.utils.validators import (in_range, is_finite_number,
                                             plausible_price, spread_pct)

log = get_logger("validation")


@dataclass
class ValidationReport:
    accepted: int = 0
    rejected: int = 0
    flagged_outlier: int = 0
    flagged_discrepancy: int = 0
    rejected_details: list[tuple[str, str]] = field(default_factory=list)

    def merge(self, other: "ValidationReport") -> None:
        self.accepted += other.accepted
        self.rejected += other.rejected
        self.flagged_outlier += other.flagged_outlier
        self.flagged_discrepancy += other.flagged_discrepancy
        self.rejected_details.extend(other.rejected_details)


class ValidationService:
    def __init__(self, cache: QuoteCache) -> None:
        self.cache = cache
        self.report = ValidationReport()
        self._outlier_logged: set[tuple[str, str]] = set()

    def validate_quote(self, quote: Quote, *, check_cross_source: bool = True) -> Quote:
        v = CONFIG.validation

        if not plausible_price(quote.price, lo=v.min_price, hi=v.max_price):
            raise MalformedDataError(
                f"implausible price {quote.price!r} for {quote.symbol}",
                provider=quote.source)

        if quote.bid is not None and quote.ask is not None:
            try:
                sp = spread_pct(quote.bid, quote.ask)
                if sp > v.max_bid_ask_spread_pct:
                    raise MalformedDataError(
                        f"spread {sp:.2f}% exceeds {v.max_bid_ask_spread_pct}% "
                        f"for {quote.symbol}", provider=quote.source)
            except MalformedDataError:
                raise
            except DataValidationError as exc:
                raise MalformedDataError(str(exc), provider=quote.source) from exc

        now = utcnow()
        age = (now - quote.source_ts).total_seconds()
        grace = v.max_price_age_seconds
        if quote.freshness is DataFreshness.DELAYED:
            grace += 3 * 86400.0 + 3600.0
        if age > grace:
            raise StaleDataError(
                f"source_ts {quote.source_ts.isoformat()} is {age:.0f}s old "
                f"(max {grace:.0f}s) for {quote.symbol}",
                provider=quote.source)
        if age < -300:
            raise StaleDataError(
                f"source_ts {quote.source_ts.isoformat()} is in the future "
                f"by {-age:.0f}s for {quote.symbol}", provider=quote.source)

        if quote.change_pct_24h is not None and \
                abs(quote.change_pct_24h) > v.outlier_pct_change:
            self.report.flagged_outlier += 1
            flag_key = (quote.symbol, quote.source)
            if flag_key not in self._outlier_logged:
                self._outlier_logged.add(flag_key)
                log.warning("outlier flag: %s 24h change %.1f%% from %s",
                            quote.symbol, quote.change_pct_24h, quote.source,
                            extra={"category": "outlier",
                                   "provider": quote.source})

        if check_cross_source:
            self._check_cross_source(quote)

        self.report.accepted += 1
        return quote

    def _check_cross_source(self, quote: Quote) -> None:
        v = CONFIG.validation
        others = [cached for cached in self.cache.get_by_symbol_and_class(quote)
                  if cached.source != quote.source]
        contemporaneous = [
            c for c in others
            if abs((c.source_ts - quote.source_ts).total_seconds()) <= 120
        ]
        if not contemporaneous:
            return
        ref = contemporaneous[0]
        if ref.price <= 0:
            return
        diff_pct = abs(quote.price - ref.price) / ref.price * 100.0
        if diff_pct > v.cross_source_tolerance_pct:
            self.report.flagged_discrepancy += 1
            log.warning("cross-source discrepancy: %s %s=%.6g vs %s=%.6g "
                        "(%.2f%% > %.2f%% tolerance)",
                        quote.symbol, quote.source, quote.price,
                        ref.source, ref.price, diff_pct,
                        v.cross_source_tolerance_pct,
                        extra={"category": "discrepancy",
                               "provider": quote.source})

    def validate_batch(self, quotes: list[Quote]) -> tuple[list[Quote], ValidationReport]:
        report = ValidationReport()
        accepted: list[Quote] = []
        for q in quotes:
            try:
                accepted.append(self.validate_quote(q))
                report.accepted += 1
            except DataValidationError as exc:
                report.rejected += 1
                report.rejected_details.append((q.symbol, str(exc)))
                log.warning("rejected quote %s from %s: %s",
                            q.symbol, q.source, exc,
                            extra={"category": exc.category,
                                   "provider": q.source})
        return accepted, report

    @staticmethod
    def assess_freshness(quote: Quote, *, stale_after: float,
                          dead_after: float) -> DataFreshness:
        age = (utcnow() - quote.source_ts).total_seconds()
        if quote.freshness is DataFreshness.DELAYED and age < 3 * 86400 + 7200:
            return DataFreshness.DELAYED
        if age <= stale_after:
            return quote.freshness
        if age <= dead_after:
            return DataFreshness.STALE
        return DataFreshness.OFFLINE
