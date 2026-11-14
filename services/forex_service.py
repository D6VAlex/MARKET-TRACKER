from __future__ import annotations

import asyncio
from typing import Any

from market_tracker.config import CURRENCY_NAMES, FOREX_PAIRS
from market_tracker.models import AggregatedQuote, AssetClass
from market_tracker.providers.forex_providers import (ErApiProvider,
                                                       FrankfurterProvider,
                                                       YahooForexProvider)
from market_tracker.services.price_aggregator import PriceAggregator
from market_tracker.services.validation_service import ValidationService
from market_tracker.utils.exceptions import AllProvidersFailedError
from market_tracker.utils.logger import get_logger

log = get_logger("forex_service")

_PAIR_NAMES = {f"{a}/{b}": f"{a}/{b}" for a, b in FOREX_PAIRS}


class ForexService:
    def __init__(self, http, health, cache, db,
                 validation: ValidationService,
                 aggregator: PriceAggregator) -> None:
        self.http = http
        self.health = health
        self.cache = cache
        self.db = db
        self.validation = validation
        self.aggregator = aggregator

        self.yahoo = YahooForexProvider(http, health)
        self.er_api = ErApiProvider(http, health)
        self.frankfurter = FrankfurterProvider(http, health)

        self.chain: list[Any] = [self.yahoo, self.er_api, self.frankfurter]
        self.tracked_pairs: list[str] = list(_PAIR_NAMES)

    async def refresh(self) -> int:
        errors: list[str] = []
        batches: list[list] = []

        async def run_provider(provider) -> None:
            try:
                quotes = await provider.fetch_batch(self.tracked_pairs)
            except Exception as exc:
                errors.append(f"{provider.provider_id}: {exc}")
                log.warning("forex provider %s failed: %s",
                            provider.provider_id, exc,
                            extra={"provider": provider.provider_id,
                                   "category": "failover"})
                return
            accepted, report = self.validation.validate_batch(quotes)
            if not accepted:
                errors.append(f"{provider.provider_id}: all rejected")
                return
            batches.append(accepted)

        await asyncio.gather(*(run_provider(p) for p in self.chain))

        total = 0
        for accepted in batches:
            for q in accepted:
                self.cache.put(q)
            total += len(accepted)
        if total == 0:
            log.error("all forex providers failed: %s", "; ".join(errors),
                      extra={"category": "all_providers_failed"})
            raise AllProvidersFailedError("; ".join(errors))
        return total

    def aggregate(self) -> list[AggregatedQuote]:
        aggs = self.aggregator.aggregate(AssetClass.FOREX)
        order = {p: i for i, p in enumerate(_PAIR_NAMES)}
        aggs.sort(key=lambda a: (order.get(a.symbol, 999), a.symbol))
        out = []
        for a in aggs:
            if "/" in a.symbol:
                base, quote = a.symbol.split("/", 1)
                name = (f"{CURRENCY_NAMES.get(base, base)} / "
                        f"{CURRENCY_NAMES.get(quote, quote)}")
                out.append(a.model_copy(update={"name": name}))
            else:
                out.append(a)
        return out
