#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
DATA_DIR = Path(os.environ.get("MT_SOAK_DIR", "/tmp/mt_soak"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ["MT_DATA_DIR"] = str(DATA_DIR)
os.environ["MT_DB_PATH"] = str(DATA_DIR / "soak.db")
os.environ["MT_LOG_LEVEL"] = "WARNING"

import market_tracker.config as cfg
cfg.DATA_DIR = DATA_DIR
cfg.DB_PATH = DATA_DIR / "soak.db"

from market_tracker.core.data_manager import DataManager
from market_tracker.core.event_manager import EventManager
from market_tracker.models import AssetClass


class Recorder:
    def __init__(self) -> None:
        self.crypto_quotes: list = []
        self.forex_quotes: list = []
        self.statuses: list = []
        self.toasts: list = []
        self.agg_stamps: list[float] = []
        self.error_toasts = 0

    def attach(self, events: EventManager) -> None:
        s = events.signals
        s.quotes_crypto.connect(self._crypto)
        s.quotes_forex.connect(self._forex)
        s.status.connect(self._status)
        s.toast.connect(self._toast)
        orig = events.publish_quotes

        def patched(ac, quotes):
            self.agg_stamps.append(time.monotonic())
            orig(ac, quotes)
        events.publish_quotes = patched

    def _crypto(self, quotes):
        self.crypto_quotes.append((time.monotonic(), quotes))

    def _forex(self, quotes):
        self.forex_quotes.append((time.monotonic(), quotes))

    def _status(self, status):
        self.statuses.append(status)

    def _toast(self, level, message):
        self.toasts.append((level, message))
        if level == "error":
            self.error_toasts += 1


async def main() -> int:
    events = EventManager()
    recorder = Recorder()
    dm = DataManager(events)

    orig_agg = dm._job_aggregate
    agg_stamps: list[float] = []

    async def instrumented():
        agg_stamps.append(time.monotonic())
        await orig_agg()
    dm._job_aggregate = instrumented

    await dm.start()
    print(f"soak: pipeline started, running {SECONDS:.0f}s ...", flush=True)
    t0 = time.monotonic()

    try:
        await asyncio.sleep(SECONDS)
    finally:
        await dm.stop()

    elapsed = time.monotonic() - t0
    print(f"soak: ran {elapsed:.1f}s")

    gaps = [b - a for a, b in zip(agg_stamps, agg_stamps[1:])]
    print(f"aggregator cycles: {len(agg_stamps)}")
    if gaps:
        avg = sum(gaps) / len(gaps)
        mx = max(gaps)
        drift = abs(avg - 1.0)
        print(f"cycle gaps: avg={avg*1000:.1f}ms max={mx*1000:.1f}ms "
              f"drift={drift*1000:.1f}ms/cycle")
        assert 0.9 <= avg <= 1.1, "cadence broken"
        assert mx < 3.0, f"max gap {mx:.2f}s — stalls detected"
    assert len(agg_stamps) >= SECONDS * 0.85, "aggregator skipped cycles"

    if recorder_crypto := dm.crypto.aggregate():
        fresh = Counter(q.freshness.value for q in recorder_crypto)
        print(f"crypto rows: {len(recorder_crypto)} freshness: {dict(fresh)}")
    forex_rows = dm.forex.aggregate()
    if forex_rows:
        ffresh = Counter(q.freshness.value for q in forex_rows)
        print(f"forex rows: {len(forex_rows)} freshness: {dict(ffresh)}")

    print(f"websocket: state={dm.ws.state} reconnects={dm.ws.reconnect_count} "
          f"messages={dm.ws.messages_received} quotes={dm.ws.quotes_emitted}")

    hist = dm.db.price_history(AssetClass.CRYPTO, "BTC", limit=10000)
    print(f"db BTC history rows: {len(hist)}")
    snap = dm.db.load_snapshot(AssetClass.CRYPTO)
    print(f"db crypto snapshot rows: {len(snap)}")

    total, failed = dm.health.totals()
    print(f"provider requests: total={total} failed={failed} "
          f"({(failed / total * 100 if total else 0):.1f}% fail)")
    for p in dm.health.snapshot():
        print(f"  {p.provider_id:12s} state={p.state:8s} "
              f"breaker={p.breaker_state:9s} reliability={p.reliability:.2f}")

    print("SOAK OK" if len(agg_stamps) >= SECONDS * 0.85 else "SOAK DEGRADED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
