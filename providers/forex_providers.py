from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from market_tracker.config import FOREX_PAIRS
from market_tracker.models import AssetClass, DataFreshness, Quote, utcnow
from market_tracker.providers.base_provider import BaseProvider
from market_tracker.utils.exceptions import MalformedDataError
from market_tracker.utils.validators import parse_iso_utc, to_float

YAHOO_SYMBOLS = {f"{a}{b}=X": (a, b) for a, b in FOREX_PAIRS}


def _pair_symbol(base: str, quote: str) -> str:
    return f"{base}/{quote}"


def _fx_decimals(quote_ccy: str) -> int:
    from market_tracker.config import FOREX_PIP_DECIMALS
    return FOREX_PIP_DECIMALS.get(quote_ccy, 5)


class YahooForexProvider(BaseProvider):
    provider_id = "yahoo"
    is_primary = True
    display_name = "Yahoo Finance"
    asset_class = AssetClass.FOREX
    limitations = ("Unofficial endpoint (no SLA, may change or throttle); "
                   "aggregated FX quotes, often 15-20 min delayed; "
                   "regularMarketTime reflects the venue's session, so "
                   "weekend quotes freeze at Friday close.")

    BASE = "https://query1.finance.yahoo.com"
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        wanted = symbols or [f"{a}/{b}" for a, b in FOREX_PAIRS]
        quotes: list[Quote] = []
        for pair in wanted:
            try:
                q = await self._fetch_one(pair)
            except MalformedDataError:
                raise
            except Exception as exc:
                self.log.warning("yahoo %s failed: %s", pair, exc)
                continue
            if q is not None:
                quotes.append(q)
        if not quotes and wanted:
            self._reject("no quotes returned")
        return quotes

    async def _fetch_one(self, pair: str) -> Quote | None:
        base, quote = pair.split("/")
        ysym = f"{base}{quote}=X"
        url = f"{self.BASE}/v8/finance/chart/{ysym}"
        params = {"interval": "1d", "range": "5d",
                  "includePrePost": "false"}
        data = await self.http.get_json(url, params=params,
                                        headers={"User-Agent": self.UA},
                                        provider=self.provider_id)
        chart = data.get("chart")
        if not isinstance(chart, dict):
            self._reject("no 'chart' object")
        result_list = chart.get("result")
        if not result_list or not isinstance(result_list, list):
            err = chart.get("error")
            raise MalformedDataError(f"yahoo chart error: {err}",
                                     provider=self.provider_id)
        res = result_list[0]
        meta = res.get("meta") or {}
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        ts = meta.get("regularMarketTime")
        if price is None:
            raise MalformedDataError(f"no price in meta for {pair}",
                                     provider=self.provider_id)
        price = to_float(price, field="price", provider=self.provider_id)
        source_ts = utcnow()
        if ts is not None:
            source_ts = datetime.fromtimestamp(to_float(ts, field="time",
                                               provider=self.provider_id), tz=timezone.utc)
        change = None
        if prev is not None:
            prev_f = to_float(prev, field="prevClose", provider=self.provider_id)
            if prev_f > 0:
                change = (price - prev_f) / prev_f * 100.0
        freshness = DataFreshness.REALTIME
        if source_ts < utcnow() - timedelta(minutes=15):
            freshness = DataFreshness.DELAYED
        return Quote(
            symbol=_pair_symbol(base, quote), asset_class=AssetClass.FOREX,
            price=price, change_pct_24h=change,
            source=self.provider_id, source_ts=source_ts,
            freshness=freshness,
        )


class ErApiProvider(BaseProvider):
    provider_id = "er_api"
    display_name = "ER-API (open)"
    asset_class = AssetClass.FOREX
    delay_seconds = 3600.0
    limitations = ("Daily-updated reference rates (once per day, ~00:05 UTC "
                   "refresh); no intraday movement; no bid/ask; USD-base "
                   "rates only — cross rates are computed by inversion, "
                   "which loses precision on thinly traded crosses.")

    BASE = "https://open.er-api.com/v6/latest"

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        wanted = symbols or [f"{a}/{b}" for a, b in FOREX_PAIRS]
        data = await self.http.get_json(f"{self.BASE}/USD",
                                        provider=self.provider_id)
        if not isinstance(data, dict) or data.get("result") != "success":
            self._reject("er-api result != success")
        rates = data.get("rates")
        if not isinstance(rates, dict):
            self._reject("er-api missing rates")
        ts = data.get("time_last_update_unix")
        source_ts = datetime.fromtimestamp(
            to_float(ts, field="time_last_update_unix",
                     provider=self.provider_id), tz=timezone.utc)
        quotes: list[Quote] = []
        for pair in wanted:
            base, quote = pair.split("/")
            try:
                if base == "USD":
                    px = to_float(rates[quote], field=quote,
                                  provider=self.provider_id)
                else:
                    usd_per_base = to_float(rates[base], field=base,
                                            provider=self.provider_id)
                    if usd_per_base <= 0:
                        continue
                    px = 1.0 / usd_per_base
            except (KeyError, MalformedDataError):
                self.log.warning("er-api missing currency in %s", pair)
                continue
            quotes.append(Quote(
                symbol=_pair_symbol(base, quote), asset_class=AssetClass.FOREX,
                price=px, source=self.provider_id, source_ts=source_ts,
                freshness=DataFreshness.DELAYED,
            ))
        return quotes


class FrankfurterProvider(BaseProvider):
    provider_id = "frankfurter"
    display_name = "Frankfurter (ECB)"
    asset_class = AssetClass.FOREX
    delay_seconds = 86400.0
    limitations = ("ECB reference rates published once per business day "
                   "around 16:00 CET; no weekend/holiday updates; no bid/ask; "
                   "EUR-base — USD pairs derived by inversion. Treat as "
                   "end-of-day reference, never as live trading prices.")

    BASE = "https://api.frankfurter.app"

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        wanted = symbols or [f"{a}/{b}" for a, b in FOREX_PAIRS]
        needed: set[str] = set()
        for pair in wanted:
            base, quote = pair.split("/")
            needed.add(base)
            needed.add(quote)
        data = await self.http.get_json(
            f"{self.BASE}/latest",
            params={"from": "EUR", "to": ",".join(sorted(needed))},
            provider=self.provider_id)
        if not isinstance(data, dict) or "rates" not in data:
            self._reject("frankfurter missing rates")
        rates: dict[str, float] = {"EUR": 1.0}
        for ccy, val in (data.get("rates") or {}).items():
            try:
                rates[ccy] = to_float(val, field=ccy, provider=self.provider_id)
            except MalformedDataError:
                continue
        date_str = str(data.get("date", ""))
        try:
            source_ts = datetime.fromisoformat(date_str).replace(
                hour=16, tzinfo=timezone.utc)
        except ValueError:
            source_ts = utcnow() - timedelta(days=1)
        quotes: list[Quote] = []
        for pair in wanted:
            base, quote = pair.split("/")
            if base not in rates or quote not in rates:
                continue
            eur_per_base = rates[base]
            eur_per_quote = rates[quote]
            if eur_per_base <= 0 or eur_per_quote <= 0:
                continue
            px = eur_per_quote / eur_per_base
            quotes.append(Quote(
                symbol=_pair_symbol(base, quote),
                asset_class=AssetClass.FOREX, price=px,
                source=self.provider_id, source_ts=source_ts,
                freshness=DataFreshness.DELAYED,
            ))
        return quotes
