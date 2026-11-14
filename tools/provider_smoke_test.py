#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from market_tracker.utils.logger import setup_logging

setup_logging("ERROR")

from market_tracker.infrastructure.connection_manager import HttpClient
from market_tracker.infrastructure.health_monitor import ProviderHealthMonitor
from market_tracker.providers.crypto_providers import (BinanceProvider,
                                                       CoinbaseProvider,
                                                       CoinGeckoProvider,
                                                       KrakenProvider)
from market_tracker.providers.forex_providers import (ErApiProvider,
                                                      FrankfurterProvider,
                                                      YahooForexProvider)
from market_tracker.providers.discovery import BinanceDiscoveryProvider


async def main() -> int:
    http = HttpClient()
    hm = ProviderHealthMonitor()
    results: list[tuple[str, str, str]] = []

    async def check(name, coro_fn, summarize):
        try:
            detail = await coro_fn()
            results.append((name, "OK", summarize(detail)))
        except Exception as exc:
            results.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))

    await check(
        "binance", lambda: BinanceProvider(http, hm).fetch_batch(["BTC", "ETH"]),
        lambda qs: f"{len(qs)} quotes, BTC={qs[0].price:,.2f}")
    await check(
        "coingecko",
        lambda: CoinGeckoProvider(http, hm).fetch_market_board(top_n=10),
        lambda r: f"{len(r[0])} coins, #1={r[1][0].symbol} cap=${r[0][0].market_cap:,.0f}")
    await check(
        "coinbase", lambda: CoinbaseProvider(http, hm).fetch_batch(["BTC"]),
        lambda qs: f"{len(qs)} quotes, bid={qs[0].bid} ask={qs[0].ask}")
    await check(
        "kraken", lambda: KrakenProvider(http, hm).fetch_batch(["BTC", "DOGE"]),
        lambda qs: f"{len(qs)} quotes, BTC={qs[0].price:,.2f}")
    await check(
        "yahoo", lambda: YahooForexProvider(http, hm).fetch_batch(["EUR/USD", "USD/JPY"]),
        lambda qs: f"{len(qs)} pairs, EUR/USD={qs[0].price}")
    await check(
        "er_api", lambda: ErApiProvider(http, hm).fetch_batch(["EUR/USD", "USD/JOD"]),
        lambda qs: f"{len(qs)} pairs, USD/JOD={qs[1].price}")
    await check(
        "frankfurter", lambda: FrankfurterProvider(http, hm).fetch_batch(["EUR/USD", "USD/CNY"]),
        lambda qs: f"{len(qs)} pairs, ts={qs[0].source_ts:%Y-%m-%d}")
    await check(
        "binance-disco",
        lambda: BinanceDiscoveryProvider(http, hm).fetch_volume_board(top_n=10),
        lambda r: f"{len(r[0])} coins, #1={r[1][0].symbol}")

    await http.close()

    fails = 0
    print(f"{'provider':<15} {'status':<6} detail")
    print("-" * 70)
    for name, status, detail in results:
        mark = status
        if status != "OK":
            fails += 1
            mark = "FAIL"
        print(f"{name:<15} {mark:<6} {detail}")
    print("-" * 70)
    print(f"{len(results) - fails}/{len(results)} providers reachable")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
