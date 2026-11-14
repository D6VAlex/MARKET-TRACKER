from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from market_tracker.infrastructure.cache import QuoteCache
from market_tracker.infrastructure.health_monitor import ProviderHealthMonitor
from market_tracker.models import AssetClass, DataFreshness, utcnow
from market_tracker.providers.crypto_providers import (BinanceProvider,
                                                       CoinbaseProvider,
                                                       KrakenProvider)
from market_tracker.providers.forex_providers import (ErApiProvider,
                                                       FrankfurterProvider,
                                                       YahooForexProvider)
from market_tracker.services.crypto_service import CryptoService
from market_tracker.services.forex_service import ForexService
from market_tracker.services.price_aggregator import PriceAggregator
from market_tracker.services.validation_service import ValidationService
from market_tracker.tests.conftest import make_quote
from market_tracker.utils.exceptions import (AuthError, CircuitOpenError,
                                             HttpError, MalformedDataError,
                                             RateLimitError)

BINANCE_PAYLOAD = [
    {"symbol": "BTCUSDT", "lastPrice": "42000.50",
     "priceChangePercent": "-2.15", "quoteVolume": "25000000000",
     "bidPrice": "42000.49", "askPrice": "42000.51"},
    {"symbol": "ETHUSDT", "lastPrice": "2299.31",
     "priceChangePercent": "1.02", "quoteVolume": "9000000000",
     "bidPrice": "2299.30", "askPrice": "2299.32"},
    {"symbol": "GARBAGETICKER", "lastPrice": "nonsense"},
]


class TestBinance:
    async def test_parses_valid_tickers(self, fake_http, health):
        fake_http.route("ticker/24hr", BINANCE_PAYLOAD)
        p = BinanceProvider(fake_http, health)
        quotes = await p.fetch_batch(["BTC", "ETH"])
        assert len(quotes) == 2
        btc = next(q for q in quotes if q.symbol == "BTC")
        assert btc.price == pytest.approx(42000.50)
        assert btc.change_pct_24h == pytest.approx(-2.15)
        assert btc.bid == pytest.approx(42000.49)
        assert btc.source == "binance"

    async def test_empty_symbols_short_circuits(self, fake_http, health):
        p = BinanceProvider(fake_http, health)
        assert await p.fetch_batch([]) == []

    async def test_malformed_response_raises(self, fake_http, health):
        fake_http.route("ticker/24hr", {"unexpected": "object"})
        p = BinanceProvider(fake_http, health)
        with pytest.raises(MalformedDataError):
            await p.fetch_batch(["BTC"])


class TestCoinbase:
    async def test_parses_ticker(self, fake_http, health):
        fake_http.route(
            "products/BTC-USD/ticker",
            {"price": "43210.55", "bid": "43210.54", "ask": "43210.56",
             "volume": "12345", "time": utcnow().isoformat()})
        p = CoinbaseProvider(fake_http, health)
        quotes = await p.fetch_batch(["BTC"])
        assert quotes[0].price == pytest.approx(43210.55)
        assert quotes[0].ask == pytest.approx(43210.56)

    async def test_down_product_returns_empty_not_crash(self, fake_http, health):
        fake_http.route("ticker", MalformedDataError("not found"))
        p = CoinbaseProvider(fake_http, health)
        quotes = await p.fetch_batch(["BTC"])
        assert quotes == []


KRAKEN_PAYLOAD = {
    "error": [],
    "result": {
        "XXBTZUSD": {"a": ["42000.10", "1", "1.000"],
                     "b": ["42000.00", "2", "2.000"],
                     "c": ["42000.05", "0.001"],
                     "v": ["1000", "24000"]},
        "XDGUSD": {"a": ["0.1234", "1", "1.000"],
                   "b": ["0.1233", "2", "2.000"],
                   "c": ["0.12335", "5000"],
                   "v": ["1000", "8000000"]},
    },
}


class TestKraken:
    async def test_maps_kraken_symbols(self, fake_http, health):
        fake_http.route("Ticker", KRAKEN_PAYLOAD)
        p = KrakenProvider(fake_http, health)
        quotes = await p.fetch_batch(["BTC", "DOGE"])
        syms = {q.symbol for q in quotes}
        assert syms == {"BTC", "DOGE"}
        btc = next(q for q in quotes if q.symbol == "BTC")
        assert btc.price == pytest.approx(42000.05)
        doge = next(q for q in quotes if q.symbol == "DOGE")
        assert doge.price == pytest.approx(0.12335)

    async def test_kraken_error_raises(self, fake_http, health):
        fake_http.route("Ticker", {"error": ["EService: Busy"], "result": {}})
        p = KrakenProvider(fake_http, health)
        with pytest.raises(MalformedDataError):
            await p.fetch_batch(["BTC"])


YAHOO_EURUSD = {
    "chart": {"result": [{"meta": {
        "regularMarketPrice": 1.0855,
        "chartPreviousClose": 1.0880,
        "regularMarketTime": utcnow().timestamp(),
    }}], "error": None},
}


class TestYahoo:
    async def test_parses_chart(self, fake_http, health):
        fake_http.route("chart/EURUSD=X", YAHOO_EURUSD)
        p = YahooForexProvider(fake_http, health)
        quotes = await p.fetch_batch(["EUR/USD"])
        q = quotes[0]
        assert q.price == pytest.approx(1.0855)
        assert q.change_pct_24h == pytest.approx(
            (1.0855 - 1.0880) / 1.0880 * 100, rel=1e-3)

    async def test_missing_price_raises(self, fake_http, health):
        payload = {"chart": {"result": [{"meta": {}}], "error": None}}
        fake_http.route("chart/EURUSD=X", payload)
        p = YahooForexProvider(fake_http, health)
        with pytest.raises(MalformedDataError):
            await p.fetch_batch(["EUR/USD"])


class TestErApi:
    async def test_usd_base_and_inversion(self, fake_http, health):
        fake_http.route("latest/USD", {
            "result": "success",
            "time_last_update_unix": utcnow().timestamp() - 600,
            "rates": {"EUR": 0.92, "JPY": 150.0, "JOD": 0.709}})
        p = ErApiProvider(fake_http, health)
        quotes = await p.fetch_batch(["EUR/USD", "USD/JPY", "USD/JOD"])
        by = {q.symbol: q for q in quotes}
        assert by["EUR/USD"].price == pytest.approx(1 / 0.92)
        assert by["USD/JPY"].price == pytest.approx(150.0)
        assert by["USD/JOD"].price == pytest.approx(0.709)
        assert by["EUR/USD"].freshness is DataFreshness.DELAYED


class TestFrankfurter:
    async def test_cross_rate_computation(self, fake_http, health):
        fake_http.route("latest", {
            "date": utcnow().date().isoformat(),
            "rates": {"USD": 1.08, "JPY": 162.0}})
        p = FrankfurterProvider(fake_http, health)
        quotes = await p.fetch_batch(["EUR/USD", "USD/JPY"])
        by = {q.symbol: q for q in quotes}
        assert by["EUR/USD"].price == pytest.approx(1.08)
        assert by["USD/JPY"].price == pytest.approx(162.0 / 1.08)


class TestCryptoFailover:
    def _service(self, fake_http, health, cache, db):
        validation = ValidationService(cache)
        aggregator = PriceAggregator(cache, validation)
        return CryptoService(fake_http, health, cache, db, validation,
                             aggregator)

    async def test_all_sources_polled_in_parallel(self, fake_http, health,
                                                  cache, db):
        fake_http.route("ticker/24hr", BINANCE_PAYLOAD)
        fake_http.route("kraken.com", KRAKEN_PAYLOAD)
        fake_http.route("coinbase.com", [])
        svc = self._service(fake_http, health, cache, db)
        svc.tracked_symbols = ["BTC", "DOGE"]
        n = await svc.refresh_quotes()
        assert n >= 2
        urls = " ".join(fake_http.requests)
        assert "binance" in urls and "kraken" in urls

    async def test_failover_to_kraken(self, fake_http, health, cache, db):
        fake_http.route("binance.com/api/v3/ticker/24hr",
                        MalformedDataError("500 down"))
        fake_http.route("kraken.com", KRAKEN_PAYLOAD)
        svc = self._service(fake_http, health, cache, db)
        svc.tracked_symbols = ["BTC", "DOGE"]
        n = await svc.refresh_quotes()
        assert n == 2
        urls = " ".join(fake_http.requests)
        assert "binance" in urls and "kraken" in urls

    async def test_all_providers_fail_raises(self, fake_http, health, cache, db):
        from market_tracker.utils.exceptions import AllProvidersFailedError
        fake_http.route("ticker/24hr", RateLimitError())
        fake_http.route("kraken.com", MalformedDataError("down"))
        fake_http.route("coinbase.com", AuthError())
        svc = self._service(fake_http, health, cache, db)
        svc.tracked_symbols = ["BTC"]
        with pytest.raises(AllProvidersFailedError):
            await svc.refresh_quotes()

    async def test_http_429_then_next_provider(self, fake_http, health,
                                              cache, db):
        fake_http.route("binance.com/api/v3/ticker/24hr", RateLimitError())
        fake_http.route("kraken.com", KRAKEN_PAYLOAD)
        svc = self._service(fake_http, health, cache, db)
        svc.tracked_symbols = ["BTC"]
        n = await svc.refresh_quotes()
        assert n == 2


class TestForexFailover:
    def _service(self, fake_http, health, cache, db):
        validation = ValidationService(cache)
        aggregator = PriceAggregator(cache, validation)
        return ForexService(fake_http, health, cache, db, validation,
                            aggregator)

    async def test_failover_yahoo_to_erapi(self, fake_http, health, cache, db):
        fake_http.route("finance.yahoo.com", MalformedDataError("down"))
        fake_http.route("er-api.com", {
            "result": "success",
            "time_last_update_unix": utcnow().timestamp() - 600,
            "rates": {"EUR": 0.92, "GBP": 0.79, "JPY": 150, "CHF": 0.88,
                      "CAD": 1.36, "AUD": 0.66, "NZD": 0.60, "CNY": 7.25,
                      "JOD": 0.709, "SGD": 1.34, "HKD": 7.79, "TRY": 34.5,
                      "MXN": 20.2, "SAR": 3.75, "AED": 3.67, "EGP": 48.6,
                      "KRW": 1385.0, "INR": 84.4, "RUB": 100.5, "BRL": 5.85,
                      "ZAR": 18.2, "SEK": 10.8, "NOK": 10.9, "DKK": 6.96,
                      "PLN": 3.98}})
        svc = self._service(fake_http, health, cache, db)
        n = await svc.refresh()
        assert n == 35
        aggs = svc.aggregate()
        assert aggs[0].symbol == "EUR/USD"
        assert aggs[0].freshness is DataFreshness.DELAYED

    async def test_all_fail_marks_no_new_data(self, fake_http, health,
                                              cache, db):
        from market_tracker.utils.exceptions import AllProvidersFailedError
        for pattern in ("finance.yahoo.com", "er-api.com", "frankfurter.app"):
            fake_http.route(pattern, MalformedDataError("down"))
        svc = self._service(fake_http, health, cache, db)
        with pytest.raises(AllProvidersFailedError):
            await svc.refresh()


class TestCircuitIntegration:
    async def test_breaker_opens_after_threshold(self, fake_http, health, db):
        cache = QuoteCache()
        validation = ValidationService(cache)
        aggregator = PriceAggregator(cache, validation)
        from market_tracker.infrastructure.circuit_breaker import CircuitBreaker
        fake_http.route("ticker/24hr", MalformedDataError("down"))
        svc = CryptoService(fake_http, health, cache, db, validation,
                            aggregator)
        svc.binance.breaker = CircuitBreaker(
            "binance", failure_threshold=3, recovery_timeout=0.2,
            half_open_successes=1)
        svc.tracked_symbols = ["BTC"]
        from market_tracker.utils.exceptions import AllProvidersFailedError
        for i in range(3):
            with pytest.raises(AllProvidersFailedError):
                await svc.refresh_quotes()
        with pytest.raises(AllProvidersFailedError):
            await svc.refresh_quotes()
        snap = health.snapshot()
        binance_h = next(s for s in snap if s.provider_id == "binance")
        assert binance_h.breaker_state == "open"


def _stock_chart(symbol: str, price: float, prev: float,
                 exchange: str = "NMS", ts=None) -> dict:
    from market_tracker.models import utcnow
    return {"chart": {"result": [{"meta": {
        "symbol": symbol, "regularMarketPrice": price,
        "chartPreviousClose": prev,
        "regularMarketTime": (ts or utcnow()).timestamp(),
        "regularMarketVolume": 12_345_678,
        "currency": "USD", "exchangeName": exchange,
        "shortName": f"{symbol} Inc.",
    }}], "error": None}}


class TestStocksProviders:
    async def test_parses_us_equity(self, fake_http, health):
        from market_tracker.providers.stock_providers import YahooStocksProvider
        fake_http.route("chart/AAPL", _stock_chart("AAPL", 230.5, 228.1))
        p = YahooStocksProvider(fake_http, health)
        quotes = await p.fetch_batch(["AAPL"])
        q = quotes[0]
        assert q.symbol == "AAPL"
        assert q.price == pytest.approx(230.5)
        assert q.change_pct_24h == pytest.approx(
            (230.5 - 228.1) / 228.1 * 100, rel=1e-3)
        assert q.volume_24h == pytest.approx(12_345_678)
        assert q.freshness is DataFreshness.REALTIME

    async def test_foreign_venue_marked_delayed(self, fake_http, health):
        from market_tracker.providers.stock_providers import YahooStocksProvider
        fake_http.route("chart/7203.T",
                        _stock_chart("7203.T", 3418.0, 3400.0,
                                     exchange="JPX"))
        p = YahooStocksProvider(fake_http, health)
        quotes = await p.fetch_batch(["7203.T"])
        assert quotes[0].symbol == "7203"
        assert quotes[0].freshness is DataFreshness.DELAYED

    async def test_missing_price_raises(self, fake_http, health):
        from market_tracker.providers.stock_providers import YahooStocksProvider
        fake_http.route("chart/GARBAGE",
                        {"chart": {"result": [{"meta": {}}], "error": None}})
        p = YahooStocksProvider(fake_http, health)
        with pytest.raises(MalformedDataError):
            await p.fetch_batch(["GARBAGE"])

    async def test_mirror_failover(self, fake_http, health, cache, db):
        from market_tracker.providers.stock_providers import YahooStocksProvider
        from market_tracker.services.stocks_service import StocksService
        fake_http.route("query1.finance.yahoo.com",
                        MalformedDataError("primary down"))
        fake_http.route("query2.finance.yahoo.com",
                        _stock_chart("AAPL", 230.5, 228.1))
        validation = ValidationService(cache)
        aggregator = PriceAggregator(cache, validation)
        svc = StocksService(fake_http, health, cache, db, validation,
                            aggregator)
        svc.tracked_yahoo_symbols = ["AAPL"]
        n = await svc.refresh()
        assert n == 1
        urls = " ".join(fake_http.requests)
        assert "query1" in urls and "query2" in urls

    async def test_all_stock_providers_fail(self, fake_http, health, cache, db):
        from market_tracker.providers.stock_providers import YahooStocksProvider
        from market_tracker.services.stocks_service import StocksService
        from market_tracker.utils.exceptions import AllProvidersFailedError
        for host in ("query1.finance.yahoo.com",
                     "query2.finance.yahoo.com"):
            fake_http.route(host, MalformedDataError("down"))
        validation = ValidationService(cache)
        aggregator = PriceAggregator(cache, validation)
        svc = StocksService(fake_http, health, cache, db, validation,
                            aggregator)
        svc.tracked_yahoo_symbols = ["AAPL"]
        with pytest.raises(AllProvidersFailedError):
            await svc.refresh()

    async def test_curated_board_names_in_aggregate(self, fake_http, health,
                                                    cache, db):
        from market_tracker.services.stocks_service import StocksService
        fake_http.route("chart/AAPL", _stock_chart("AAPL", 230.5, 228.1))
        fake_http.route("chart/0700.HK", _stock_chart(
            "0700.HK", 386.6, 384.0, exchange="HKG"))
        validation = ValidationService(cache)
        aggregator = PriceAggregator(cache, validation)
        svc = StocksService(fake_http, health, cache, db, validation,
                            aggregator)
        svc.tracked_yahoo_symbols = ["AAPL", "0700.HK"]
        await svc.refresh()
        aggs = svc.aggregate()
        by = {a.symbol: a for a in aggs}
        assert by["AAPL"].name == "Apple Inc."
        assert by["0700"].name == "Tencent Holdings"


GF_PAGE = (
    "<script>AF_initDataCallback({key: 'ds:2', hash: '1', data:[[["
    "[\"/m/07zmbvf\",[\"AAPL\",\"NASDAQ\"],\"Apple Inc\",0,\"USD\","
    "[356.27,1.3899841,0.39167723,2,2,2],null,354.88,\"#666666\",\"US\","
    "\"/m/0k8z\",[1794618900],\"America/New_York\",-18000,"
    "\"/m/07zmbvf\",null,[355.4,-0.8699951],"
    "[1794603601],[1794617994],null,null,\"AAPL:NASDAQ\",0,null,null,"
    "null,0]]], sideChannel: {}});</script>"
)

GF_EMPTY_PAGE = (
    "<script>AF_initDataCallback({key: 'ds:2', hash: '1', data:"
    "[[[null,[5]]]], sideChannel: {}});</script>"
)


class TestGoogleFinanceParser:

    def test_parses_live_quote_payload(self):
        from market_tracker.providers.google_finance import (
            parse_quote_payload)
        p = parse_quote_payload(GF_PAGE)
        assert p is not None
        assert p["ticker"] == "AAPL"
        assert p["exchange"] == "NASDAQ"
        assert p["name"] == "Apple Inc"
        assert p["currency"] == "USD"
        assert p["last"] == 356.27
        assert p["change_pct"] == 0.39167723

    def test_error_page_returns_none(self):
        from market_tracker.providers.google_finance import (
            parse_quote_payload)
        assert parse_quote_payload(GF_EMPTY_PAGE) is None

    def test_missing_payload_returns_none(self):
        from market_tracker.providers.google_finance import (
            parse_quote_payload)
        assert parse_quote_payload("<html>nothing here</html>") is None

    async def test_provider_end_to_end(self, fake_http, health):
        from market_tracker.providers.google_finance import (
            GoogleFinanceProvider)
        fake_http.route("AAPL:NASDAQ", GF_PAGE)
        fake_http.route("MSFT:NASDAQ", GF_PAGE.replace("AAPL", "MSFT"))
        p = GoogleFinanceProvider(fake_http, health)
        quotes = await p.fetch_batch(["AAPL", "MSFT"])
        assert len(quotes) == 2
        by = {q.symbol: q for q in quotes}
        assert by["AAPL"].price == 356.27
        assert by["AAPL"].source == "google_finance"
        assert by["AAPL"].asset_class is AssetClass.STOCKS

    async def test_symbol_covers_all_venues(self):
        from market_tracker.providers.google_finance import (
            _YAHOO_TO_GOOGLE)
        assert len(_YAHOO_TO_GOOGLE) == 25
        assert _YAHOO_TO_GOOGLE["AAPL"] == "AAPL:NASDAQ"
        assert _YAHOO_TO_GOOGLE["0700"] == "0700:HKG"
        assert _YAHOO_TO_GOOGLE["7203"] == "7203:TYO"
        assert _YAHOO_TO_GOOGLE["005930"] == "005930:KRX"
        assert _YAHOO_TO_GOOGLE["ASML"] == "ASML:AMS"
        assert _YAHOO_TO_GOOGLE["SAP"] == "SAP:ETR"
        assert _YAHOO_TO_GOOGLE["SHEL"] == "SHEL:LON"
        assert _YAHOO_TO_GOOGLE["NESN"] == "NESN:SWX"
        assert _YAHOO_TO_GOOGLE["BRK-B"] == "BRK.B:NYSE"
        assert _YAHOO_TO_GOOGLE["JPM"] == "JPM:NYSE"


class TestParallelRefresh:

    async def test_stocks_poll_yahoo_and_google_together(self, fake_http,
                                                         health, cache, db):
        from market_tracker.services.stocks_service import StocksService
        fake_http.route("query1.finance.yahoo.com",
                        _stock_chart("AAPL", 230.5, 228.1))
        fake_http.route("query2.finance.yahoo.com", MalformedDataError("down"))
        fake_http.route("AAPL:NASDAQ", GF_PAGE)
        validation = ValidationService(cache)
        aggregator = PriceAggregator(cache, validation)
        svc = StocksService(fake_http, health, cache, db, validation,
                            aggregator)
        svc.tracked_yahoo_symbols = ["AAPL"]
        n = await svc.refresh()
        assert n >= 1
        urls = " ".join(fake_http.requests)
        assert "query1" in urls
        assert "google.com/finance" in urls

    async def test_forex_polls_all_sources(self, fake_http, health,
                                           cache, db):
        from market_tracker.services.forex_service import ForexService
        fake_http.route("finance.yahoo.com", MalformedDataError("down"))
        fake_http.route("er-api.com", {
            "result": "success",
            "time_last_update_unix": utcnow().timestamp() - 600,
            "rates": {"EUR": 0.92, "JPY": 150.0}})
        fake_http.route("frankfurter.app", {
            "date": utcnow().date().isoformat(),
            "rates": {"EUR": 0.92, "JPY": 150.0}})
        validation = ValidationService(cache)
        aggregator = PriceAggregator(cache, validation)
        svc = ForexService(fake_http, health, cache, db, validation,
                           aggregator)
        n = await svc.refresh()
        assert n >= 2
        urls = " ".join(fake_http.requests)
        assert "er-api" in urls and "frankfurter" in urls


class TestSymbolBackoff:

    async def test_coinbase_404_hard_backoff(self, fake_http, health):
        fake_http.route("coinbase.com", HttpError("client error 404", status=404))
        p = CoinbaseProvider(fake_http, health)
        quotes = await p.fetch_batch(["NOPE"])
        assert quotes == []
        assert p._sym_skip_cycles["NOPE"] == 40
        for _ in range(40):
            assert p.sym_skipped("NOPE") is True
        assert p.sym_skipped("NOPE") is False

    async def test_google_malformed_soft_backoff_after_three(self, fake_http,
                                                             health):
        from market_tracker.providers.google_finance import (
            GoogleFinanceProvider)
        fake_http.route("AAPL:NASDAQ", GF_EMPTY_PAGE)
        fake_http.route("MSFT:NASDAQ", GF_PAGE)
        p = GoogleFinanceProvider(fake_http, health)
        await p.fetch_batch(["AAPL", "MSFT"])
        assert "AAPL" not in p._sym_skip_cycles
        assert p._sym_fail_count["AAPL"] == 1
        await p.fetch_batch(["AAPL", "MSFT"])
        await p.fetch_batch(["AAPL", "MSFT"])
        assert p._sym_skip_cycles["AAPL"] == 12
        assert p.sym_skipped("MSFT") is False

    async def test_success_resets_backoff(self, fake_http, health):
        fake_http.route("coinbase.com", HttpError("client error 404", status=404))
        p = CoinbaseProvider(fake_http, health)
        await p.fetch_batch(["NOPE"])
        assert p._sym_skip_cycles["NOPE"] == 40
        p.sym_ok("NOPE")
        assert p._sym_skip_cycles.get("NOPE") is None

    async def test_skipped_symbol_makes_no_request(self, fake_http, health):
        fake_http.route("coinbase.com", HttpError("client error 404", status=404))
        p = CoinbaseProvider(fake_http, health)
        await p.fetch_batch(["NOPE"])
        n_before = len(fake_http.requests)
        await p.fetch_batch(["NOPE"])
        assert len(fake_http.requests) == n_before
