from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

import aiohttp

from market_tracker.config import CONFIG
from market_tracker.models import AssetClass, DataFreshness, Quote, utcnow
from market_tracker.utils.exceptions import MalformedDataError
from market_tracker.utils.logger import get_logger
from market_tracker.utils.validators import to_float

log = get_logger("ws")


class WebSocketManager:

    def __init__(self, health) -> None:
        self.health = health
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._streams: list[str] = []
        self._on_quote: Callable[[Quote], Awaitable[None]] | None = None
        self.state = "closed"
        self.last_message_at: float | None = None
        self.last_pong_at: float | None = None
        self.reconnect_count = 0
        self.messages_received = 0
        self.quotes_emitted = 0

    def set_quote_sink(self, sink: Callable[[Quote], Awaitable[None]]) -> None:
        self._on_quote = sink

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, symbols: list[str]) -> None:
        if self.is_running:
            await self.update_symbols(symbols)
            return
        self._streams = sorted({f"{s.lower()}usdt@miniTicker" for s in symbols})
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ws-binance")
        log.info("websocket manager started for %d symbols", len(symbols),
                 extra={"provider": "binance", "category": "websocket",
                        "state": "starting"})

    async def update_symbols(self, symbols: list[str]) -> None:
        wanted = sorted({f"{s.lower()}usdt@miniTicker" for s in symbols})
        if wanted == self._streams:
            return
        self._streams = wanted
        log.info("websocket symbol set changed — reconnecting",
                 extra={"provider": "binance", "category": "websocket"})
        await self.restart()

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self.state = "closed"
        self.health.set_ws_state("binance", "closed")

    async def restart(self) -> None:
        await self.stop()
        if self._streams:
            symbols = [s.split("@")[0].upper().replace("USDT", "")
                       for s in self._streams]
            await self.start(symbols)

    async def _run(self) -> None:
        base_delay = 1.0
        max_delay = 60.0
        delay = base_delay
        net = CONFIG.network

        while not self._stop.is_set():
            url = ("wss://stream.binance.com:9443/stream?streams="
                   + "/".join(self._streams))
            try:
                self.state = "connecting"
                self.health.set_ws_state("binance", "connecting")
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=None,
                                                    connect=net.connect_timeout)
                    self._session = aiohttp.ClientSession(timeout=timeout)
                async with self._session.ws_connect(
                        url,
                        heartbeat=net.ws_ping_interval,
                        receive_timeout=90,
                        autoping=True) as ws:
                    self.state = "open"
                    self.health.set_ws_state("binance", "open")
                    self.last_message_at = time.monotonic()
                    delay = base_delay
                    log.info("websocket OPEN (%d streams)", len(self._streams),
                             extra={"provider": "binance",
                                    "category": "websocket", "state": "open"})
                    while not self._stop.is_set():
                        try:
                            msg = await ws.receive(timeout=net.ws_ping_interval +
                                                   net.ws_ping_timeout)
                        except asyncio.TimeoutError:
                            if self.last_message_at is None or \
                                    time.monotonic() - self.last_message_at > 120:
                                raise ConnectionError("idle timeout: no data")
                            continue
                        if msg.type is aiohttp.WSMsgType.CLOSED:
                            raise ConnectionError("websocket closed by server")
                        if msg.type in (aiohttp.WSMsgType.ERROR,
                                        aiohttp.WSMsgType.CLOSE):
                            raise ConnectionError(f"ws error frame: {msg.type}")
                        if msg.type is not aiohttp.WSMsgType.TEXT and \
                                msg.type is not aiohttp.WSMsgType.BINARY:
                            continue
                        if msg.data:
                            await self._handle_message(msg.data)
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError, aiohttp.ClientError,
                    asyncio.TimeoutError) as exc:
                self.state = "error"
                self.health.set_ws_state("binance", "error")
                self.reconnect_count += 1
                jitter = random.uniform(0.8, 1.2)
                sleep_for = min(delay * jitter, max_delay)
                log.warning("websocket dropped (%s: %s) — reconnect %d in %.1fs",
                            type(exc).__name__, exc, self.reconnect_count,
                            sleep_for,
                            extra={"provider": "binance",
                                   "category": "websocket",
                                   "state": "reconnecting",
                                   "attempt": self.reconnect_count})
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, max_delay)
            except Exception as exc:
                log.exception("websocket unexpected error: %s", exc,
                              extra={"provider": "binance",
                                     "category": "websocket",
                                     "state": "error"})
                raise
        self.state = "closed"
        self.health.set_ws_state("binance", "closed")

    async def _handle_message(self, raw: str | bytes) -> None:
        self.messages_received += 1
        self.last_message_at = time.monotonic()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("ws: non-JSON frame dropped",
                        extra={"provider": "binance", "category": "malformed"})
            return
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return
        event = data.get("e")
        if event == "24hrMiniTicker":
            try:
                symbol = str(data["s"])
                if not symbol.endswith("USDT"):
                    return
                base = symbol[:-4]
                close = to_float(data["c"], field="c", provider="binance")
                open_ = to_float(data["o"], field="o", provider="binance")
                volume = to_float(data["q"], field="q", provider="binance")
                change = ((close - open_) / open_ * 100.0) if open_ > 0 else None
                event_ts_ms = data.get("E")
                source_ts = utcnow()
                if event_ts_ms:
                    source_ts = datetime_from_ms(event_ts_ms)
                quote = Quote(
                    symbol=base, asset_class=AssetClass.CRYPTO,
                    price=close, change_pct_24h=change, volume_24h=volume,
                    source="binance", source_ts=source_ts,
                    freshness=DataFreshness.LIVE,
                )
            except (KeyError, MalformedDataError) as exc:
                log.debug("ws miniTicker malformed: %s", exc)
                return
            self.quotes_emitted += 1
            if self._on_quote is not None:
                try:
                    await self._on_quote(quote)
                except Exception:
                    log.exception("quote sink error (ignored)")

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reconnects": self.reconnect_count,
            "messages": self.messages_received,
            "quotes": self.quotes_emitted,
            "streams": len(self._streams),
        }


def datetime_from_ms(ms: float) -> datetime:
    from datetime import timezone
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
