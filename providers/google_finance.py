from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from market_tracker.config import STOCKS_BOARD
from market_tracker.models import AssetClass, DataFreshness, Quote, utcnow
from market_tracker.providers.base_provider import BaseProvider
from market_tracker.utils.exceptions import HttpError, MalformedDataError
from market_tracker.utils.validators import to_float

_YAHOO_TO_GOOGLE: dict[str, str] = {}
for _entry in STOCKS_BOARD:
    _display = _entry["symbol"].split(".")[0]
    _raw = _entry["symbol"]
    if _raw.endswith(".HK"):
        _google = f"{_raw[:-3]}:HKG"
    elif _raw.endswith(".T"):
        _google = f"{_raw[:-2]}:TYO"
    elif _raw.endswith(".KS"):
        _google = f"{_raw[:-3]}:KRX"
    elif _raw.endswith(".AS"):
        _google = f"{_raw[:-3]}:AMS"
    elif _raw.endswith(".DE"):
        _google = f"{_raw[:-3]}:ETR"
    elif _raw.endswith(".L"):
        _google = f"{_raw[:-2]}:LON"
    elif _raw.endswith(".SW"):
        _google = f"{_raw[:-3]}:SWX"
    elif _raw == "BRK-B":
        _google = "BRK.B:NYSE"
    else:
        _google = f"{_raw}:{_entry['exchange'].replace('NYQ', 'NYSE').replace('NMS', 'NASDAQ')}"
    _YAHOO_TO_GOOGLE[_display] = _google

_DATA_PREFIX = "AF_initDataCallback({key: 'ds:2'"
_QUOTE_ROW = re.compile(
    r"\[\[\[\[\"(/[^\"]+)\",\[\"([^\"]+)\",\"([^\"]+)\"\],\"([^\"]*)\","
    r"(?:\d+|null),\"([A-Z]{3,4})\",\[([\d.eE+]+),([-0-9.eE+]+),"
    r"([-0-9.eE+]+)")


def parse_quote_payload(html: str) -> dict[str, Any] | None:
    if not html or len(html) < 100:
        return None
    i = html.find(_DATA_PREFIX)
    if i < 0:
        i = html.find("AF_initDataCallback")
        if i < 0:
            return None
    j = html.find("data:", i)
    if j < 0:
        return None
    snippet = html[j:j + 4000]
    m = _QUOTE_ROW.search(snippet)
    if not m:
        m = _QUOTE_ROW.search(html[max(0, i):i + 8000])
        if not m:
            return None
    try:
        last = float(m.group(6))
        change_abs = float(m.group(7))
        change_pct = float(m.group(8))
    except (ValueError, IndexError, TypeError):
        return None
    if last <= 0:
        return None
    return {
        "ticker": m.group(2),
        "exchange": m.group(3),
        "name": m.group(4),
        "currency": m.group(5),
        "last": last,
        "change_abs": change_abs,
        "change_pct": change_pct,
    }


class GoogleFinanceProvider(BaseProvider):
    provider_id = "google_finance"
    display_name = "Google Finance"
    asset_class = AssetClass.STOCKS
    limitations = ("Scrapes the public quote page (no official API); one "
                   "request per symbol; prices in listing currency; "
                   "intraday where the venue is open, otherwise the last "
                   "close.")

    BASE = "https://www.google.com/finance/quote"
    REALTIME_VENUES = {"NASDAQ", "NYSE", "NGM", "ASE", "BTS", "PCX", "NYS"}

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    def __init__(self, http, health) -> None:
        super().__init__(http, health)
        self.google_symbols: list[str] = list(_YAHOO_TO_GOOGLE.values())

    async def _fetch(self, symbols: list[str] | None, **kwargs: Any) -> list[Quote]:
        wanted = symbols or list(_YAHOO_TO_GOOGLE)
        display_for = {g: d for d, g in _YAHOO_TO_GOOGLE.items()}
        sem = asyncio.Semaphore(4)

        async def one(display: str) -> Quote | None:
            google_sym = _YAHOO_TO_GOOGLE.get(display)
            if google_sym is None:
                return None
            if self.sym_skipped(display):
                return None
            async with sem:
                try:
                    quote = await self._fetch_one(google_sym, display)
                except HttpError as exc:
                    if exc.status == 404:
                        self.log.info(
                            "google_finance: %s not found (404)", google_sym)
                        self.sym_failed(display, hard=True)
                    else:
                        self.log.warning("%s failed for %s: %s",
                                         self.provider_id, google_sym, exc)
                        self.sym_failed(display)
                    return None
                except MalformedDataError as exc:
                    self.log.warning("google_finance rejected %s: %s",
                                     google_sym, exc)
                    self.sym_failed(display)
                    return None
                except Exception as exc:
                    self.log.warning("%s failed for %s: %s",
                                     self.provider_id, google_sym, exc)
                    self.sym_failed(display)
                    return None
                self.sym_ok(display)
                return quote

        results = await asyncio.gather(*(one(d) for d in wanted))
        quotes = [q for q in results if q is not None]
        if not quotes and wanted:
            self._reject("no Google Finance quotes returned")
        return quotes

    async def _fetch_one(self, google_sym: str, display: str) -> Quote:
        data = await self.http.get_text(
            f"{self.BASE}/{google_sym}",
            headers={"User-Agent": self.UA},
            provider=self.provider_id)
        payload = parse_quote_payload(data)
        if payload is None:
            raise MalformedDataError(
                f"no quote payload for {google_sym}",
                provider=self.provider_id)
        price = to_float(payload["last"], field="last",
                         provider=self.provider_id)
        if price <= 0:
            raise MalformedDataError(
                f"non-positive price for {google_sym}",
                provider=self.provider_id)

        source_ts = utcnow()
        i = data.find(_DATA_PREFIX)
        if i >= 0:
            snippet = data[i:i + 2000]
            m = re.search(r"\[([0-9]{10})\][^0-9]", snippet)
            if m:
                try:
                    source_ts = datetime.fromtimestamp(
                        float(m.group(1)), tz=timezone.utc)
                except (ValueError, OSError, OverflowError):
                    source_ts = utcnow()

        change_pct = None
        try:
            cp = to_float(payload["change_pct"], field="change_pct",
                          provider=self.provider_id)
            if -100.0 < cp < 1000.0:
                change_pct = cp
        except MalformedDataError:
            change_pct = None

        exchange = payload["exchange"]
        freshness = DataFreshness.REALTIME
        stale_horizon = utcnow() - timedelta(minutes=20)
        if exchange not in self.REALTIME_VENUES or source_ts < stale_horizon:
            freshness = DataFreshness.DELAYED

        return Quote(
            symbol=display, asset_class=AssetClass.STOCKS,
            price=price, change_pct_24h=change_pct,
            source=self.provider_id, source_ts=source_ts,
            freshness=freshness,
        )
