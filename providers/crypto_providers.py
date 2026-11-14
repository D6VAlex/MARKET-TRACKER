from __future__ import annotations

from datetime import datetime, timezone

import asyncio
from typing import Any

from market_tracker.models import (AssetClass, CryptoMeta, DataFreshness, Quote,
                                   utcnow)
from market_tracker.providers.base_provider import BaseProvider
from market_tracker.utils.exceptions import (HttpError, MalformedDataError)
from market_tracker.utils.validators import parse_iso_utc, to_float

KRAKEN_MAP = {"BTC": "XBT", "DOGE": "XDG"}
KRAKEN_ALIAS = {v: k for k, v in KRAKEN_MAP.items()}


class BinanceProvider(BaseProvider):
    provider_id = "binance"
    is_primary = True
    display_name = "Binance"
    asset_class = AssetClass.CRYPTO
    supports_websocket = True
    limitations = ("Spot API; no market-cap or rank data; USDT-majors only; "
                   "24h change window is rolling (UTC-based). "
                   "REST weight limit ~1200/min; heavy endpoints may 429.")

    BASE = "https://api.binance.com"

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        symbols = [s for s in (symbols or []) if s != "USDT"]
        if not symbols:
            return []
        try:
            return await self._fetch_batch(symbols)
        except HttpError as exc:
            if exc.status != 400:
                raise
            self.log.warning("batch 400 — falling back to per-symbol fetch",
                             extra={"category": "malformed",
                                    "provider": self.provider_id})
            quotes: list[Quote] = []
            for sym in symbols:
                try:
                    quotes.extend(await self._fetch_batch([sym]))
                except HttpError:
                    continue
            if not quotes:
                raise
            return quotes

    async def _fetch_batch(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        pairs = [f"{s}USDT" for s in symbols]
        import json as _json
        from urllib.parse import quote as _quote
        compact = _json.dumps(pairs, separators=(",", ":"))
        url = (f"{self.BASE}/api/v3/ticker/24hr?symbols="
               + _quote(compact, safe=""))
        data = await self.http.get_json(url,
                                        provider=self.provider_id)
        if not isinstance(data, list):
            self._reject("expected list from ticker/24hr")
        now = utcnow()
        quotes: list[Quote] = []
        for item in data:
            symbol = str(item.get("symbol", ""))
            if not symbol.endswith("USDT"):
                continue
            base = symbol[:-4]
            try:
                price = to_float(item["lastPrice"], field="lastPrice",
                                 provider=self.provider_id)
                change = to_float(item["priceChangePercent"],
                                  field="priceChangePercent",
                                  provider=self.provider_id)
                volume = to_float(item["quoteVolume"], field="quoteVolume",
                                  provider=self.provider_id)
                bid_raw = item.get("bidPrice")
                ask_raw = item.get("askPrice")
                bid_val = None
                ask_val = None
                if bid_raw not in (None, "", "0", "0.0", "0.00000000"):
                    try:
                        bid_val = to_float(bid_raw, field="bidPrice",
                                           provider=self.provider_id)
                        if bid_val <= 0:
                            bid_val = None
                    except MalformedDataError:
                        bid_val = None
                if ask_raw not in (None, "", "0", "0.0", "0.00000000"):
                    try:
                        ask_val = to_float(ask_raw, field="askPrice",
                                           provider=self.provider_id)
                        if ask_val <= 0:
                            ask_val = None
                    except MalformedDataError:
                        ask_val = None
                quote = Quote(
                    symbol=base, asset_class=AssetClass.CRYPTO,
                    price=price,
                    bid=bid_val,
                    ask=ask_val,
                    change_pct_24h=change, volume_24h=volume,
                    source=self.provider_id, source_ts=now,
                    freshness=DataFreshness.REALTIME,
                )
                quotes.append(quote)
            except (KeyError, MalformedDataError) as exc:
                self.log.warning("skipping malformed Binance entry %s: %s",
                                 symbol, exc)
                continue
        return quotes

    async def fetch_server_time_offset(self) -> float:
        data = await self.http.get_json(f"{self.BASE}/api/v3/time",
                                        provider=self.provider_id)
        server_ms = to_float(data["serverTime"], field="serverTime",
                             provider=self.provider_id)
        return server_ms - utcnow().timestamp() * 1000.0


class CoinGeckoProvider(BaseProvider):
    provider_id = "coingecko"
    display_name = "CoinGecko"
    asset_class = AssetClass.CRYPTO
    limitations = ("Public API ~5-15 req/min; responses cached by CoinGecko "
                   "for 45-60s (no live sub-minute ticks); aggregated volume "
                   "across exchanges; ids are slugs, not ticker symbols.")

    BASE = "https://api.coingecko.com/api/v3"

    def __init__(self, http, health, api_key: str = "") -> None:
        super().__init__(http, health)
        self._headers = {"x-cg-demo-api-key": api_key} if api_key else {}

    async def fetch_market_board(self, top_n: int = 25,
                                 currency: str = "usd") -> tuple[list[Quote], list[CryptoMeta]]:
        url = f"{self.BASE}/coins/markets"
        params = {
            "vs_currency": currency,
            "order": "market_cap_desc",
            "per_page": min(top_n, 250),
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        data = await self.http.get_json(url, params=params,
                                        headers=self._headers,
                                        provider=self.provider_id)
        if not isinstance(data, list):
            self._reject("expected list from coins/markets")
        quotes: list[Quote] = []
        metas: list[CryptoMeta] = []
        for item in data:
            try:
                price = to_float(item["current_price"], field="current_price",
                                 provider=self.provider_id)
                ts_raw = item.get("last_updated")
                source_ts = utcnow()
                if ts_raw:
                    if isinstance(ts_raw, (int, float)):
                        source_ts = datetime.fromtimestamp(
                            float(ts_raw) / 1000.0, tz=timezone.utc)
                    elif isinstance(ts_raw, str):
                        source_ts = parse_iso_utc(ts_raw)
                quote = Quote(
                    symbol=str(item["symbol"]).upper(),
                    asset_class=AssetClass.CRYPTO,
                    price=price,
                    change_pct_24h=item.get("price_change_percentage_24h"),
                    volume_24h=item.get("total_volume"),
                    market_cap=item.get("market_cap"),
                    rank=item.get("market_cap_rank"),
                    source=self.provider_id,
                    source_ts=source_ts,
                    freshness=DataFreshness.REALTIME,
                )
                quotes.append(quote)
                metas.append(CryptoMeta(
                    symbol=str(item["symbol"]).upper(),
                    name=str(item.get("name", "")),
                    coingecko_id=str(item.get("id", "")),
                    rank=item.get("market_cap_rank"),
                ))
            except (KeyError, MalformedDataError) as exc:
                self.log.warning("skipping malformed CoinGecko entry: %s", exc)
                continue
        return quotes, metas

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        quotes, _ = await self.fetch_market_board(top_n=kwargs.get("top_n", 50))
        if symbols:
            wanted = {s.upper() for s in symbols}
            quotes = [q for q in quotes if q.symbol in wanted]
        return quotes


class CoinbaseProvider(BaseProvider):
    provider_id = "coinbase"
    display_name = "Coinbase Exchange"
    asset_class = AssetClass.CRYPTO
    limitations = ("Per-product REST ticker (needs 1 request per coin — "
                   "slower for boards); gives bid/ask; no market cap/rank; "
                   "24h volume in base currency, not quote.")

    BASE = "https://api.exchange.coinbase.com"

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        symbols = symbols or []
        quotes: list[Quote] = []
        sem = asyncio.Semaphore(5)

        async def one(sym: str) -> Quote | None:
            if self.sym_skipped(sym):
                return None
            product = f"{sym}-USD"
            async with sem:
                try:
                    data = await self.http.get_json(
                        f"{self.BASE}/products/{product}/ticker",
                        provider=self.provider_id)
                except HttpError as exc:
                    if exc.status == 404:
                        self.log.info(
                            "coinbase: %s not listed (404)", product)
                        self.sym_failed(sym, hard=True)
                    else:
                        self.log.warning("coinbase %s failed: %s",
                                         product, exc)
                        self.sym_failed(sym)
                    return None
                except Exception as exc:
                    self.log.warning("coinbase %s failed: %s",
                                     product, exc)
                    self.sym_failed(sym)
                    return None
            try:
                price = to_float(data["price"], field="price",
                                 provider=self.provider_id)
                ts = data.get("time")
                source_ts = utcnow()
                if ts:
                    from market_tracker.utils.validators import parse_iso_utc
                    source_ts = parse_iso_utc(str(ts))
                bid_raw = data.get("bid")
                ask_raw = data.get("ask")
                bid_val = None
                ask_val = None
                if bid_raw not in (None, "", "0"):
                    try:
                        bid_val = to_float(bid_raw, field="bid",
                                           provider=self.provider_id)
                        if bid_val <= 0:
                            bid_val = None
                    except MalformedDataError:
                        bid_val = None
                if ask_raw not in (None, "", "0"):
                    try:
                        ask_val = to_float(ask_raw, field="ask",
                                           provider=self.provider_id)
                        if ask_val <= 0:
                            ask_val = None
                    except MalformedDataError:
                        ask_val = None
                self.sym_ok(sym)
                return Quote(
                    symbol=sym, asset_class=AssetClass.CRYPTO, price=price,
                    bid=bid_val, ask=ask_val,
                    volume_24h=data.get("volume"),
                    source=self.provider_id, source_ts=source_ts,
                    freshness=DataFreshness.REALTIME,
                )
            except (KeyError, MalformedDataError) as exc:
                self.log.warning("coinbase malformed ticker %s: %s", product, exc)
                self.sym_failed(sym)
                return None

        results = await asyncio.gather(*(one(s) for s in symbols))
        return [q for q in results if q is not None]


class KrakenProvider(BaseProvider):
    provider_id = "kraken"
    display_name = "Kraken"
    asset_class = AssetClass.CRYPTO
    limitations = ("Public ticker with bid/ask + 24h volume; no market cap; "
                   "rate limit ~1 req/s sustained; occasional 'EService: "
                   "Busy' errors under load.")

    BASE = "https://api.kraken.com"

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        symbols = symbols or []
        if not symbols:
            return []
        pair_names = ",".join(f"{KRAKEN_MAP.get(s, s)}USD" for s in symbols)
        data = await self.http.get_json(
            f"{self.BASE}/0/public/Ticker",
            params={"pair": pair_names},
            provider=self.provider_id)
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            self._reject("missing 'result' in Kraken response")
        error = data.get("error") if isinstance(data, dict) else None
        if error and not result:
            self._reject(f"kraken error: {error}")
        quotes: list[Quote] = []
        for pair_key, t in result.items():
            base = _kraken_base(pair_key)
            if base is None:
                continue
            try:
                last_arr = t["c"]
                price = to_float(last_arr[0], field="c[0]",
                                 provider=self.provider_id)
                trade_ts = utcnow()
                bid = to_float(t["b"][0], field="b[0]", provider=self.provider_id)
                ask = to_float(t["a"][0], field="a[0]", provider=self.provider_id)
                if bid <= 0:
                    bid = None
                if ask <= 0:
                    ask = None
                quotes.append(Quote(
                    symbol=base, asset_class=AssetClass.CRYPTO,
                    price=price, bid=bid, ask=ask,
                    volume_24h=to_float(t["v"][1], field="v[1]",
                                        provider=self.provider_id),
                    source=self.provider_id, source_ts=trade_ts,
                    freshness=DataFreshness.REALTIME,
                ))
            except (KeyError, IndexError, MalformedDataError) as exc:
                self.log.warning("kraken malformed ticker %s: %s", pair_key, exc)
                continue
        return quotes


def _kraken_base(pair_key: str) -> str | None:
    k = pair_key.upper()
    if k.endswith("ZUSD"):
        base = k[:-4]
    elif k.endswith("USD"):
        base = k[:-3]
    else:
        return None
    if base.startswith("X") and len(base) > 3:
        base = base[1:]
    return KRAKEN_ALIAS.get(base, base)
