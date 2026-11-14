from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from market_tracker.models import (AggregatedQuote, AssetClass,
                                   ProviderHealth, SystemStatus)
from market_tracker.utils.logger import get_logger

log = get_logger("events")


class _EventSignals(QObject):
    quotes_crypto = Signal(list)
    quotes_forex = Signal(list)
    quotes_stocks = Signal(list)
    status = Signal(object)
    toast = Signal(str, str)
    providers = Signal(list)


class EventManager:

    def __init__(self) -> None:
        self.signals = _EventSignals()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._subscribers: dict[str, list[Callable]] = {}

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def run_coroutine_threadsafe(self, coro) -> asyncio.Future:
        if self._loop is None or self._loop.is_closed():
            log.warning("pipeline loop not available — coroutine dropped",
                        extra={"category": "loop"})
            fut: asyncio.Future = asyncio.get_event_loop_policy(
            ).new_event_loop().create_future()
            fut.set_exception(RuntimeError("pipeline loop closed"))
            return fut
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def publish_quotes(self, asset_class: AssetClass,
                       quotes: list[AggregatedQuote]) -> None:
        if asset_class is AssetClass.CRYPTO:
            self.signals.quotes_crypto.emit(quotes)
        elif asset_class is AssetClass.STOCKS:
            self.signals.quotes_stocks.emit(quotes)
        else:
            self.signals.quotes_forex.emit(quotes)

    def publish_status(self, status: SystemStatus) -> None:
        self.signals.status.emit(status)

    def publish_providers(self, providers: list[ProviderHealth]) -> None:
        self.signals.providers.emit(providers)

    def publish_toast(self, level: str, message: str) -> None:
        self.signals.toast.emit(level, message)
