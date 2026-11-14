from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from market_tracker.config import STOCKS_BOARD
from market_tracker.models import AssetClass, DataFreshness, Quote, utcnow
from market_tracker.providers.base_provider import BaseProvider
from market_tracker.utils.exceptions import MalformedDataError
from market_tracker.utils.validators import to_float

import asyncio


class YahooStocksBase(BaseProvider):

    asset_class = AssetClass.STOCKS
    host = "query1.finance.yahoo.com"

    REALTIME_VENUES = {"NMS", "NYQ", "NGM", "ASE", "BTS", "PCX", "NYS"}

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        wanted = symbols or [s["symbol"] for s in STOCKS_BOARD]
        sem = asyncio.Semaphore(6)

        async def one(sym: str) -> Quote | None:
            async with sem:
                try:
                    return await self._fetch_one(sym)
                except MalformedDataError:
                    raise
                except Exception as exc:
                    self.log.warning("%s failed for %s: %s",
                                     self.provider_id, sym, exc)
                    return None

        results = await asyncio.gather(*(one(s) for s in wanted))
        quotes = [q for q in results if q is not None]
        if not quotes and wanted:
            self._reject("no stock quotes returned")
        return quotes

    async def _fetch_one(self, symbol: str) -> Quote:
        url = (f"https://{self.host}/v8/finance/chart/"
               f"{symbol}?interval=1d&range=5d&includePrePost=false")
        data = await self.http.get_json(
            url, headers={"User-Agent": self.UA},
            provider=self.provider_id)

        chart = data.get("chart") if isinstance(data, dict) else None
        if not isinstance(chart, dict):
            self._reject("no 'chart' object")
        result = chart.get("result")
        if not result or not isinstance(result, list):
            err = chart.get("error")
            raise MalformedDataError(f"chart error: {err}",
                                     provider=self.provider_id)
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            raise MalformedDataError(f"no price for {symbol}",
                                     provider=self.provider_id)
        price = to_float(price, field="price", provider=self.provider_id)

        ts = meta.get("regularMarketTime")
        source_ts = utcnow()
        if ts is not None:
            source_ts = datetime.fromtimestamp(
                to_float(ts, field="time", provider=self.provider_id),
                tz=timezone.utc)

        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        change = None
        if prev is not None:
            prev_f = to_float(prev, field="prevClose",
                              provider=self.provider_id)
            if prev_f > 0:
                change = (price - prev_f) / prev_f * 100.0

        exchange = str(meta.get("exchangeName", "") or "")
        currency = str(meta.get("currency", "USD") or "USD")
        volume = meta.get("regularMarketVolume")

        freshness = DataFreshness.REALTIME
        if exchange not in self.REALTIME_VENUES or \
                source_ts < utcnow() - timedelta(minutes=20):
            freshness = DataFreshness.DELAYED

        display = symbol.split(".")[0] if "." in symbol else symbol

        return Quote(
            symbol=display, asset_class=AssetClass.STOCKS,
            price=price, change_pct_24h=change,
            volume_24h=to_float(volume, field="volume",
                                provider=self.provider_id)
            if volume is not None else None,
            source=self.provider_id, source_ts=source_ts,
            freshness=freshness,
        )


class YahooStocksProvider(YahooStocksBase):
    provider_id = "yahoo_stocks"
    is_primary = True
    display_name = "Yahoo Finance (Stocks)"
    host = "query1.finance.yahoo.com"
    limitations = ("Unofficial endpoint (no SLA, may change/throttle); "
                   "one request per symbol; US venues near real-time, "
                   "non-US venues and closed markets delayed; prices in "
                   "the listing currency (HKD/JPY/EUR/...), not USD.")


class Yahoo2StocksProvider(YahooStocksBase):

    provider_id = "yahoo2_stocks"
    display_name = "Yahoo Finance Mirror (Stocks)"
    host = "query2.finance.yahoo.com"
    limitations = ("Same data as the primary Yahoo endpoint via a different "
                   "front-end host (query2); protects against per-host "
                   "throttling but not against Yahoo-wide outages.")
