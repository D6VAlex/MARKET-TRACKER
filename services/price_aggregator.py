from __future__ import annotations

import statistics
from typing import Iterable

from market_tracker.config import CONFIG
from market_tracker.infrastructure.cache import QuoteCache
from market_tracker.models import (AggregatedQuote, AssetClass, DataFreshness,
                                   Quote, utcnow)
from market_tracker.services.validation_service import ValidationService
from market_tracker.utils.logger import get_logger

log = get_logger("aggregator")


class PriceAggregator:
    def __init__(self, cache: QuoteCache,
                 validation: ValidationService) -> None:
        self.cache = cache
        self.validation = validation

    def aggregate(self, asset_class: AssetClass,
                  metas: dict[str, str] | None = None) -> list[AggregatedQuote]:
        metas = metas or {}
        symbols = sorted(self.cache.symbols(asset_class))
        out: list[AggregatedQuote] = []
        for sym in symbols:
            per_provider = self._freshest_per_provider(asset_class, sym)
            if not per_provider:
                continue
            agg = self._aggregate_symbol(sym, asset_class, per_provider,
                                         metas.get(sym, sym))
            if agg is not None:
                out.append(agg)
        return out

    def _freshest_per_provider(self, asset_class: AssetClass,
                               symbol: str) -> list[Quote]:
        quotes = [q for q in self.cache.get_all(asset_class)
                  if q.symbol == symbol]
        by_prov: dict[str, Quote] = {}
        for q in quotes:
            cur = by_prov.get(q.source)
            if cur is None or q.source_ts > cur.source_ts:
                by_prov[q.source] = q
        return list(by_prov.values())

    def _aggregate_symbol(self, symbol: str, asset_class: AssetClass,
                          quotes: list[Quote], name: str) -> AggregatedQuote | None:
        if not quotes:
            return None
        v = CONFIG.validation

        quotes = sorted(quotes, key=lambda q: q.source_ts, reverse=True)

        prices = [q.price for q in quotes]
        price = statistics.median(prices) if len(prices) > 2 else prices[0]

        disagreement = None
        if len(prices) >= 2:
            spread = (max(prices) - min(prices)) / min(prices) * 100.0 \
                if min(prices) > 0 else None
            disagreement = spread

        bids = [q.bid for q in quotes if q.bid]
        asks = [q.ask for q in quotes if q.ask]
        best_bid = max(bids) if bids else None
        best_ask = min(asks) if asks else None
        if best_bid and best_ask and best_bid > best_ask:
            best_bid = best_ask = None

        lead = quotes[0]
        freshness = self.validation.assess_freshness(
            lead, stale_after=CONFIG.scheduler.stale_after_seconds,
            dead_after=CONFIG.scheduler.dead_after_seconds)

        cap_quote = next((q for q in quotes if q.market_cap is not None), lead)
        rank_quote = next((q for q in quotes if q.rank is not None), lead)

        return AggregatedQuote(
            symbol=symbol, name=name, asset_class=asset_class,
            price=price, bid=best_bid, ask=best_ask,
            change_pct_24h=lead.change_pct_24h,
            volume_24h=next((q.volume_24h for q in quotes
                             if q.volume_24h is not None), None),
            market_cap=cap_quote.market_cap,
            rank=rank_quote.rank,
            sources=[q.source for q in quotes],
            source_count=len(quotes),
            disagreement_pct=disagreement,
            freshness=freshness,
            source_ts=lead.source_ts,
            last_update_ts=utcnow(),
        )
