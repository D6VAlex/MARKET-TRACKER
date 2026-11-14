from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from market_tracker.config import CONFIG
from market_tracker.core.event_manager import EventManager
from market_tracker.core.scheduler import Scheduler
from market_tracker.infrastructure.cache import QuoteCache
from market_tracker.infrastructure.database import Database
from market_tracker.infrastructure.health_monitor import ProviderHealthMonitor
from market_tracker.models import (AssetClass, DataFreshness, Quote,
                                   SystemStatus, utcnow)
from market_tracker.providers.websocket_manager import WebSocketManager
from market_tracker.services.crypto_service import CryptoService
from market_tracker.services.forex_service import ForexService
from market_tracker.services.validation_service import ValidationService
from market_tracker.utils.exceptions import (AllProvidersFailedError,
                                             categorize_exception)
from market_tracker.utils.logger import (ConnectionTracker, get_logger,
                                         setup_logging)

log = get_logger("data_manager")


class DataManager:
    def __init__(self, events: EventManager, db_path=None) -> None:
        setup_logging()
        self.events = events
        self.cache = QuoteCache()
        self.db = Database(db_path) if db_path else Database()
        self.health = ProviderHealthMonitor()
        self.validation = ValidationService(self.cache)
        self.scheduler = Scheduler()
        self.conn_tracker = ConnectionTracker()
        self.ws = WebSocketManager(self.health)

        from market_tracker.infrastructure.connection_manager import HttpClient
        self.http = HttpClient()

        from market_tracker.services.price_aggregator import PriceAggregator
        self.aggregator = PriceAggregator(self.cache, self.validation)

        from market_tracker.services.stocks_service import StocksService
        self.crypto = CryptoService(self.http, self.health, self.cache,
                                    self.db, self.validation, self.aggregator)
        self.forex = ForexService(self.http, self.health, self.cache,
                                  self.db, self.validation, self.aggregator)
        self.stocks = StocksService(self.http, self.health, self.cache,
                                    self.db, self.validation, self.aggregator)

        self.last_sync: datetime | None = None
        self.last_sync_crypto: datetime | None = None
        self.last_sync_forex: datetime | None = None
        self.last_sync_stocks: datetime | None = None
        self.consecutive_total_failures = 0
        self._ws_started = False
        self._stop = asyncio.Event()

        async def _ws_sink(quote: Quote) -> None:
            self.crypto.ingest_ws_quote(quote)
        self.ws.set_quote_sink(_ws_sink)

    async def start(self) -> None:
        self.events.publish_toast("info", "Starting market data pipeline…")

        try:
            symbols = await self.crypto.refresh_discovery()
            self.events.publish_toast(
                "success", f"Crypto universe loaded: {len(symbols)} assets")
        except Exception as exc:
            self.events.publish_toast(
                "warn", f"Discovery failed ({categorize_exception(exc)}); "
                        f"using cached universe")

        for ac, svc in ((AssetClass.CRYPTO, self.crypto),
                        (AssetClass.FOREX, self.forex),
                        (AssetClass.STOCKS, self.stocks)):
            restored = self.db.load_snapshot(ac)
            if restored:
                for q in restored:
                    self.cache.put(q)
                log.info("restored %d %s quotes from snapshot", len(restored),
                         ac.value, extra={"category": "recovery"})

        await self._safe_refresh(self.crypto.refresh_quotes,
                                 AssetClass.CRYPTO)
        await self._safe_refresh(self.forex.refresh, AssetClass.FOREX)
        await self._safe_refresh(self.stocks.refresh, AssetClass.STOCKS)

        await self._start_ws()

        s = CONFIG.scheduler
        self.scheduler.add_job("crypto_rest", s.crypto_rest_interval,
                               self._job_crypto_rest)
        self.scheduler.add_job("forex_rest", s.forex_rest_interval,
                               self._job_forex_rest)
        self.scheduler.add_job("stocks_rest", s.stocks_rest_interval,
                               self._job_stocks_rest)
        self.scheduler.add_job("discovery", 300.0, self._job_discovery)
        self.scheduler.add_job("aggregator", s.aggregator_interval,
                               self._job_aggregate)
        self.scheduler.add_job("housekeeping", 60.0, self._job_housekeeping)
        await self.scheduler.start()
        self.events.publish_toast("success", "Live data pipeline active")

    async def stop(self) -> None:
        self._stop.set()
        await self.scheduler.stop()
        await self.ws.stop()
        self._persist_snapshots()
        await self.http.close()
        self.db.close()

    async def _job_crypto_rest(self) -> None:
        await self._safe_refresh(self.crypto.refresh_quotes,
                                 AssetClass.CRYPTO)

    async def _job_forex_rest(self) -> None:
        await self._safe_refresh(self.forex.refresh, AssetClass.FOREX)

    async def _job_stocks_rest(self) -> None:
        await self._safe_refresh(self.stocks.refresh, AssetClass.STOCKS)

    async def _job_discovery(self) -> None:
        try:
            symbols = await self.crypto.refresh_discovery()
            await self.ws.update_symbols(symbols)
        except Exception as exc:
            log.warning("discovery job failed: %s", exc,
                        extra={"category": categorize_exception(exc)})

    async def _job_aggregate(self) -> None:
        crypto_aggs = self.crypto.aggregate()
        forex_aggs = self.forex.aggregate()
        stocks_aggs = self.stocks.aggregate()

        self.events.publish_quotes(AssetClass.CRYPTO, crypto_aggs)
        self.events.publish_quotes(AssetClass.FOREX, forex_aggs)
        self.events.publish_quotes(AssetClass.STOCKS, stocks_aggs)

        if self.db.healthy:
            all_quotes = [self.cache.get(AssetClass.CRYPTO, a.symbol)
                          for a in crypto_aggs]
            all_quotes += [self.cache.get(AssetClass.FOREX, a.symbol)
                           for a in forex_aggs]
            all_quotes += [self.cache.get(AssetClass.STOCKS, a.symbol)
                           for a in stocks_aggs]
            all_quotes = [q for q in all_quotes if q is not None]
            if all_quotes:
                self.db.record_quotes(all_quotes)

        self.events.publish_status(self.build_status())
        self.events.publish_providers(self.health.snapshot())

    async def _job_housekeeping(self) -> None:
        self.cache.evict_expired()
        pruned = self.db.prune_history(keep_days=7)
        if pruned:
            log.info("pruned %d old quote rows", pruned)

        if self.consecutive_total_failures >= 3:
            self.events.publish_toast(
                "error", "All data providers unreachable — showing last "
                         "known good data")
            self.consecutive_total_failures = 0

    async def _safe_refresh(self, fn, asset_class: AssetClass) -> None:
        try:
            n = await fn()
            self.conn_tracker.record_success()
            self.last_sync = utcnow()
            if asset_class is AssetClass.CRYPTO:
                self.last_sync_crypto = utcnow()
            elif asset_class is AssetClass.FOREX:
                self.last_sync_forex = utcnow()
            else:
                self.last_sync_stocks = utcnow()
            self.consecutive_total_failures = 0
        except AllProvidersFailedError as exc:
            self.conn_tracker.record_failure()
            self.consecutive_total_failures += 1
            log.error("%s refresh failed completely: %s",
                      asset_class.value, exc,
                      extra={"category": "all_providers_failed"})
        except Exception as exc:
            self.conn_tracker.record_failure()
            log.exception("unexpected refresh error (%s)",
                          categorize_exception(exc))

    async def _start_ws(self) -> None:
        try:
            await self.ws.start(self.crypto.tracked_symbols)
            self._ws_started = True
        except Exception as exc:
            log.warning("websocket start failed: %s", exc,
                        extra={"category": "websocket"})

    def _persist_snapshots(self) -> None:
        for ac in (AssetClass.CRYPTO, AssetClass.FOREX, AssetClass.STOCKS):
            quotes = self.cache.get_all(ac)
            if quotes:
                self.db.save_snapshot(ac, quotes)

    def manual_refresh(self) -> None:
        if self.scheduler is None:
            return
        now = time.monotonic()
        for name in ("crypto_rest", "forex_rest", "stocks_rest"):
            job = self.scheduler._jobs.get(name)
            if job is not None:
                job.next_due = now
        self.events.run_coroutine_threadsafe(self._wake_scheduler())

    async def _wake_scheduler(self) -> None:
        self.scheduler.wake()

    def build_status(self) -> SystemStatus:
        total, failed = self.health.totals()
        primaries_crypto = self.health.primaries(AssetClass.CRYPTO)
        primaries_forex = self.health.primaries(AssetClass.FOREX)

        active_crypto = self._active_provider_name(AssetClass.CRYPTO,
                                                   primaries_crypto)
        active_forex = self._active_provider_name(AssetClass.FOREX,
                                                  primaries_forex)

        crypto_aggs = self.crypto.aggregate()
        stale_count = sum(1 for a in crypto_aggs if a.stale)

        ws_states = {"binance": self.ws.state}

        primaries_stocks = self.health.primaries(AssetClass.STOCKS)
        active_stocks = self._active_provider_name(AssetClass.STOCKS,
                                                   primaries_stocks)

        return SystemStatus(
            active_provider_crypto=active_crypto,
            active_provider_forex=active_forex,
            active_provider_stocks=active_stocks,
            backup_available_crypto=[
                p for p in self.health.available_providers(AssetClass.CRYPTO)
                if p not in primaries_crypto],
            backup_available_forex=[
                p for p in self.health.available_providers(AssetClass.FOREX)
                if p not in primaries_forex],
            backup_available_stocks=[
                p for p in self.health.available_providers(AssetClass.STOCKS)
                if p not in primaries_stocks],
            internet_online=self.conn_tracker.online,
            internet_state_text=self.conn_tracker.state_text,
            last_sync=self.last_sync,
            failed_requests=failed,
            total_requests=total,
            ws_states=ws_states,
            providers=self.health.snapshot(),
            db_ok=self.db.healthy,
            cache_entries=len(self.cache),
            stale_count=stale_count,
        )

    @staticmethod
    def _active_provider_name(asset_class, primaries: set[str]) -> str:
        if primaries:
            return "+".join(sorted(primaries))
        return "—"
