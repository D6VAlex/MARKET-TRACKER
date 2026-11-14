from __future__ import annotations

import asyncio
from typing import Any

from market_tracker.config import CONFIG, CRYPTO_FALLBACK_IDS
from market_tracker.models import (AggregatedQuote, AssetClass, CryptoMeta,
                                   Quote)
from market_tracker.providers.crypto_providers import (BinanceProvider,
                                                       CoinbaseProvider,
                                                       CoinGeckoProvider,
                                                       KrakenProvider)
from market_tracker.providers.discovery import (CURATED_COINS,
                                                BinanceDiscoveryProvider)
from market_tracker.services.price_aggregator import PriceAggregator
from market_tracker.services.validation_service import ValidationService
from market_tracker.utils.exceptions import AllProvidersFailedError
from market_tracker.utils.logger import get_logger

log = get_logger("crypto_service")


def _module_top_n() -> int:
    from market_tracker import config as _cfg
    return getattr(_cfg, "CRYPTO_TOP_N", 25)


class CryptoService:
    def __init__(self, http, health, cache, db,
                 validation: ValidationService,
                 aggregator: PriceAggregator) -> None:
        self.http = http
        self.health = health
        self.cache = cache
        self.db = db
        self.validation = validation
        self.aggregator = aggregator

        self.binance = BinanceProvider(http, health)
        self.coingecko = CoinGeckoProvider(http, health,
                                           api_key=CONFIG.keys.coingecko_api_key)
        self.coinbase = CoinbaseProvider(http, health)
        self.kraken = KrakenProvider(http, health)
        self.binance_discovery = BinanceDiscoveryProvider(http, health)

        self.quote_chain: list[Any] = [self.binance, self.kraken, self.coinbase]
        self.discovery_chain: list[Any] = [self.coingecko, self.binance_discovery]

        self.meta_by_symbol: dict[str, CryptoMeta] = {
            m.symbol: m for m in CURATED_COINS}
        self.tracked_symbols: list[str] = [m.symbol for m in CURATED_COINS]
        self.last_discovery_ok: bool | None = None
        self._binance_weight_window: list[float] = []
        self._binance_weight_limit = 600.0

    def _binance_weight_ok(self, weight: float) -> bool:
        import time as _t
        cutoff = _t.monotonic() - 60.0
        self._binance_weight_window = [
            entry for entry in self._binance_weight_window
            if entry[1] > cutoff]
        used = sum(w for w, _ in self._binance_weight_window)
        return used + weight <= self._binance_weight_limit

    def _binance_weight_charge(self, weight: float) -> None:
        import time as _t
        self._binance_weight_window.append((weight, _t.monotonic()))

    @property
    def binance_weight_used(self) -> float:
        return sum(w for w, _ in self._binance_weight_window)

    async def refresh_discovery(self, top_n: int | None = None) -> list[str]:
        top_n = top_n or _module_top_n()
        errors: list[str] = []
        for provider in self.discovery_chain:
            try:
                if provider is self.coingecko:
                    quotes, metas = await provider.fetch_market_board(top_n=top_n)
                else:
                    if not self._binance_weight_ok(80.0):
                        errors.append("binance: local weight budget exceeded")
                        continue
                    self._binance_weight_charge(80.0)
                    quotes, metas = await provider.fetch_volume_board(top_n=top_n)
            except Exception as exc:
                errors.append(f"{provider.provider_id}: {exc}")
                continue
            if not quotes:
                errors.append(f"{provider.provider_id}: empty board")
                continue

            accepted, report = self.validation.validate_batch(quotes)
            if not accepted:
                errors.append(f"{provider.provider_id}: all quotes rejected")
                continue
            for q in accepted:
                self.cache.put(q)

            for m in metas:
                existing = self.meta_by_symbol.get(m.symbol)
                if existing is None:
                    self.meta_by_symbol[m.symbol] = m
                else:
                    self.meta_by_symbol[m.symbol] = CryptoMeta(
                        symbol=m.symbol, name=existing.name or m.name,
                        coingecko_id=m.coingecko_id or existing.coingecko_id,
                        rank=m.rank or existing.rank)

            symbols = [m.symbol for m in metas]
            for m in CURATED_COINS:
                if m.symbol not in symbols:
                    symbols.append(m.symbol)
            self.tracked_symbols = symbols
            self.last_discovery_ok = True
            if provider is not self.coingecko:
                log.info("discovery via fallback %s (CoinGecko unavailable)",
                         provider.provider_id)
            return symbols

        self.last_discovery_ok = False
        log.error("all discovery providers failed: %s", "; ".join(errors),
                  extra={"category": "all_providers_failed"})
        return self.tracked_symbols

    async def refresh_quotes(self) -> int:
        symbols = self.tracked_symbols
        if not symbols:
            return 0
        errors: list[str] = []
        batches: list[list] = []

        async def run_provider(provider) -> None:
            try:
                if provider is self.binance:
                    weight = 2.0 * max(len(symbols), 1)
                    if not self._binance_weight_ok(weight):
                        errors.append("binance: local weight budget exceeded")
                        return
                    quotes = await provider.fetch_batch(symbols)
                    self._binance_weight_charge(
                        2.0 * max(len(quotes), 1))
                else:
                    quotes = await provider.fetch_batch(symbols)
            except Exception as exc:
                errors.append(f"{provider.provider_id}: {exc}")
                log.warning("crypto provider %s failed: %s",
                            provider.provider_id, exc,
                            extra={"provider": provider.provider_id,
                                   "category": "failover"})
                return
            accepted, report = self.validation.validate_batch(quotes)
            if not accepted:
                errors.append(f"{provider.provider_id}: all rejected")
                return
            batches.append(accepted)

        await asyncio.gather(*(run_provider(p) for p in self.quote_chain))

        total = 0
        for accepted in batches:
            for q in accepted:
                self.cache.put(q)
            total += len(accepted)
        if total == 0:
            log.error("all crypto quote providers failed: %s",
                      "; ".join(errors),
                      extra={"category": "all_providers_failed"})
            raise AllProvidersFailedError("; ".join(errors))
        return total

    def ingest_ws_quote(self, quote: Quote) -> None:
        try:
            q = self.validation.validate_quote(quote)
            self.cache.put(q)
        except Exception as exc:
            log.debug("ws quote rejected: %s", exc)

    def aggregate(self) -> list[AggregatedQuote]:
        metas = {s: m.name for s, m in self.meta_by_symbol.items()}
        aggs = self.aggregator.aggregate(AssetClass.CRYPTO, metas)
        def sort_key(a: AggregatedQuote):
            rank = a.rank if a.rank is not None else 9999
            cap = a.market_cap if a.market_cap is not None else 0.0
            return (-cap, rank, a.symbol)
        aggs.sort(key=sort_key)
        return aggs
