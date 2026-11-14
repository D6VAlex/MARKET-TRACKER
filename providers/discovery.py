from __future__ import annotations

from typing import Any

from market_tracker.models import (AssetClass, CryptoMeta, DataFreshness, Quote,
                                   utcnow)
from market_tracker.providers.base_provider import BaseProvider
from market_tracker.utils.exceptions import MalformedDataError
from market_tracker.utils.validators import to_float

CURATED_COINS: list[CryptoMeta] = [
    CryptoMeta(symbol="BTC", name="Bitcoin", coingecko_id="bitcoin", rank=1),
    CryptoMeta(symbol="ETH", name="Ethereum", coingecko_id="ethereum", rank=2),
    CryptoMeta(symbol="USDT", name="Tether", coingecko_id="tether", rank=3),
    CryptoMeta(symbol="BNB", name="BNB", coingecko_id="bnb", rank=4),
    CryptoMeta(symbol="SOL", name="Solana", coingecko_id="solana", rank=5),
    CryptoMeta(symbol="XRP", name="XRP", coingecko_id="xrp", rank=6),
    CryptoMeta(symbol="USDC", name="USD Coin", coingecko_id="usd-coin", rank=7),
    CryptoMeta(symbol="DOGE", name="Dogecoin", coingecko_id="dogecoin", rank=8),
    CryptoMeta(symbol="ADA", name="Cardano", coingecko_id="cardano", rank=9),
    CryptoMeta(symbol="TRX", name="TRON", coingecko_id="tron", rank=10),
]

_STABLECOINS = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USD1",
                "EUR", "AEUR", "XUSD", "USDP", "EURI", "USDH",
                "RLUSD", "RWUSD", "XUSD"}
_LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


_TOKENIZED_PREFIXES = (
    "NVDA", "MSTR", "SPX", "SOXL", "SNDK", "TSLA", "AMD", "INTC",
    "GOOG", "AMZN", "META", "MSFT", "COIN", "HOOD", "AAPL", "BABA", "SPCX",
)


def _is_leveraged(base: str) -> bool:
    if len(base) > 4 and any(base.endswith(s) for s in _LEVERAGED_SUFFIXES):
        return True
    return (len(base) >= 5 and base.endswith("B")
            and any(base.startswith(p) for p in _TOKENIZED_PREFIXES))


class BinanceDiscoveryProvider(BaseProvider):

    provider_id = "binance"
    display_name = "Binance (volume discovery)"
    asset_class = AssetClass.CRYPTO
    limitations = ("Discovery ranks by 24h quote volume, not market cap "
                   "(no cap data on this path); excludes stablecoins and "
                   "leveraged tokens; heavy endpoint (~1.9 MB) — polled "
                   "infrequently.")

    BASE = "https://api.binance.com"

    async def fetch_volume_board(self, top_n: int = 30) -> tuple[list[Quote], list[CryptoMeta]]:
        data = await self.http.get_json(f"{self.BASE}/api/v3/ticker/24hr",
                                        provider=self.provider_id)
        if not isinstance(data, list):
            self._reject("expected list from ticker/24hr")
        now = utcnow()
        rows: list[tuple[float, dict]] = []
        for item in data:
            symbol = str(item.get("symbol", ""))
            if not symbol.endswith("USDT"):
                continue
            base = symbol[:-4]
            if base in _STABLECOINS or _is_leveraged(base):
                continue
            try:
                vol = to_float(item["quoteVolume"], field="quoteVolume",
                               provider=self.provider_id)
            except (KeyError, MalformedDataError):
                continue
            rows.append((vol, item))
        rows.sort(key=lambda r: r[0], reverse=True)

        quotes: list[Quote] = []
        metas: list[CryptoMeta] = []
        for i, (vol, item) in enumerate(rows[:top_n]):
            symbol = str(item["symbol"])
            base = symbol[:-4]
            try:
                price = to_float(item["lastPrice"], field="lastPrice",
                                 provider=self.provider_id)
                change = to_float(item["priceChangePercent"],
                                  field="priceChangePercent",
                                  provider=self.provider_id)
                quote = Quote(
                    symbol=base, asset_class=AssetClass.CRYPTO,
                    price=price, change_pct_24h=change, volume_24h=vol,
                    rank=i + 1,
                    source=self.provider_id, source_ts=now,
                    freshness=DataFreshness.REALTIME,
                )
            except (KeyError, MalformedDataError):
                continue
            quotes.append(quote)
            metas.append(CryptoMeta(symbol=base, name=base, rank=i + 1))
        return quotes, metas

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        quotes, _ = await self.fetch_volume_board(
            top_n=kwargs.get("top_n", 30))
        if symbols:
            wanted = {s.upper() for s in symbols}
            quotes = [q for q in quotes if q.symbol in wanted]
        return quotes
