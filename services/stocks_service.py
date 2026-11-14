from __future__ import annotations

import asyncio
from typing import Any

from market_tracker.config import CURRENCY_NAMES, STOCKS_BOARD
from market_tracker.models import (AggregatedQuote, AssetClass, StockMeta)
from market_tracker.providers.google_finance import GoogleFinanceProvider
from market_tracker.providers.stock_providers import (Yahoo2StocksProvider,
                                                      YahooStocksProvider)
from market_tracker.services.price_aggregator import PriceAggregator
from market_tracker.services.validation_service import ValidationService
from market_tracker.utils.exceptions import AllProvidersFailedError
from market_tracker.utils.logger import get_logger

log = get_logger("stocks_service")


class StocksService:
    def __init__(self, http, health, cache, db,
                 validation: ValidationService,
                 aggregator: PriceAggregator) -> None:
        self.http = http
        self.health = health
        self.cache = cache
        self.db = db
        self.validation = validation
        self.aggregator = aggregator

        self.yahoo = YahooStocksProvider(http, health)
        self.yahoo2 = Yahoo2StocksProvider(http, health)
        self.google = GoogleFinanceProvider(http, health)

        self.chain: list[Any] = [self.yahoo, self.yahoo2, self.google]

        self.meta_by_symbol: dict[str, StockMeta] = {}
        self.yahoo_to_display: dict[str, str] = {}
        for entry in STOCKS_BOARD:
            display = entry["symbol"].split(".")[0]
            meta = StockMeta(
                symbol=display, name=entry["name"],
                exchange=entry["exchange"], currency=entry["currency"],
                sector=entry["sector"])
            self.meta_by_symbol[display] = meta
            self.yahoo_to_display[entry["symbol"]] = display
        self.tracked_yahoo_symbols: list[str] = [
            e["symbol"] for e in STOCKS_BOARD]

    async def refresh(self) -> int:
        errors: list[str] = []
        batches: list[list] = []

        async def run_provider(provider) -> None:
            try:
                if provider is self.google:
                    quotes = await provider.fetch_batch()
                else:
                    quotes = await provider.fetch_batch(
                        self.tracked_yahoo_symbols)
            except Exception as exc:
                errors.append(f"{provider.provider_id}: {exc}")
                log.warning("stocks provider %s failed: %s",
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
            log.error("all stocks providers failed: %s", "; ".join(errors),
                      extra={"category": "all_providers_failed"})
            raise AllProvidersFailedError("; ".join(errors))
        return total

    def aggregate(self) -> list[AggregatedQuote]:
        aggs = self.aggregator.aggregate(AssetClass.STOCKS)
        for a in aggs:
            meta = self.meta_by_symbol.get(a.symbol)
            if meta is not None:
                a = a.model_copy(update={"name": meta.name})
        order = {m.symbol: i for i, m in enumerate(
            self.meta_by_symbol.values())}
        aggs.sort(key=lambda a: (order.get(a.symbol, 999), a.symbol))

        out = []
        for a in aggs:
            meta = self.meta_by_symbol.get(a.symbol)
            name = meta.name if meta else a.name
            out.append(a.model_copy(update={"name": name}))
        return out
