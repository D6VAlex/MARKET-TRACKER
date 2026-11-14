from __future__ import annotations

import asyncio
import time
from collections import deque

import pytest

from market_tracker.core.scheduler import Scheduler


def _record(sink):
    async def job():
        sink.append(time.monotonic())
    return job


@pytest.fixture
def fast_scheduler():
    return Scheduler()


class TestBasic:
    async def test_job_runs_on_interval(self, fast_scheduler):
        runs = []
        fast_scheduler.add_job(
            "tick", 0.05, _record(runs),
            start_immediately=True)
        await fast_scheduler.start()
        await asyncio.sleep(0.32)
        await fast_scheduler.stop()
        assert len(runs) >= 4

    async def test_remove_job(self, fast_scheduler):
        runs = []
        fast_scheduler.add_job("tick", 0.05,
                               lambda: runs.append(1))
        await fast_scheduler.start()
        await asyncio.sleep(0.15)
        fast_scheduler.remove_job("tick")
        baseline = len(runs)
        await asyncio.sleep(0.15)
        await fast_scheduler.stop()
        assert len(runs) == baseline

    async def test_duplicate_name_rejected(self, fast_scheduler):
        fast_scheduler.add_job("x", 1.0, _noop)
        with pytest.raises(ValueError):
            fast_scheduler.add_job("x", 1.0, _noop)

    async def test_bad_interval_rejected(self, fast_scheduler):
        with pytest.raises(ValueError):
            fast_scheduler.add_job("x", 0, _noop)


async def _noop() -> None:
    return None


class TestOneSecondAccuracy:
    async def test_one_hz_cadence(self, fast_scheduler):
        stamps: list[float] = []

        async def tick():
            stamps.append(time.monotonic())

        fast_scheduler.add_job("hz1", 1.0, tick, start_immediately=False)
        t0 = time.monotonic()
        await fast_scheduler.start()
        await asyncio.sleep(4.6)
        await fast_scheduler.stop()
        assert len(stamps) >= 4
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert all(0.95 <= g <= 1.15 for g in gaps), gaps

    async def test_no_drift_accumulation(self, fast_scheduler):
        stamps: list[float] = []

        async def slow_tick():
            stamps.append(time.monotonic())
            await asyncio.sleep(0.4)

        fast_scheduler.add_job("drift", 1.0, slow_tick,
                               start_immediately=False)
        await fast_scheduler.start()
        await asyncio.sleep(5.5)
        await fast_scheduler.stop()
        assert len(stamps) >= 4
        for i, ts in enumerate(stamps[1:], start=1):
            phase = (ts - stamps[0]) % 1.0
            assert phase < 0.15 or phase > 0.85, \
                f"tick {i} drifted: phase={phase:.3f}"


class TestOverlapProtection:
    async def test_slow_job_not_reentered(self, fast_scheduler):
        concurrent = {"now": 0, "max": 0, "runs": 0}

        async def slow():
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"],
                                    concurrent["now"])
            concurrent["runs"] += 1
            await asyncio.sleep(0.3)
            concurrent["now"] -= 1

        fast_scheduler.add_job("slow", 0.05, slow)
        await fast_scheduler.start()
        await asyncio.sleep(1.0)
        await fast_scheduler.stop()
        assert concurrent["max"] == 1
        assert concurrent["runs"] < 10
        job = fast_scheduler._jobs["slow"]
        assert job.stats.skips > 0


class TestErrorHandling:
    async def test_job_exception_does_not_kill_loop(self, fast_scheduler):
        ok_runs = []

        async def boom():
            raise RuntimeError("job exploded")

        async def fine():
            ok_runs.append(1)

        fast_scheduler.add_job("boom", 0.05, boom)
        fast_scheduler.add_job("fine", 0.05, fine)
        await fast_scheduler.start()
        await asyncio.sleep(0.4)
        await fast_scheduler.stop()
        assert len(ok_runs) >= 4
        stats = fast_scheduler.jobs_snapshot()
        assert stats["boom"]["errors"] >= 2

    async def test_error_backoff_grows(self, fast_scheduler):
        async def boom():
            raise ValueError("always")
        fast_scheduler.add_job("b", 0.02, boom)
        await fast_scheduler.start()
        await asyncio.sleep(0.5)
        await fast_scheduler.stop()
        job = fast_scheduler._jobs["b"]
        assert job.error_backoff >= 0.02 * 2


class TestResync:
    async def test_fell_behind_resyncs_without_burst(self, fast_scheduler):
        runs = []

        async def tick():
            runs.append(time.monotonic())

        job = fast_scheduler.add_job("t", 0.1, tick)
        await fast_scheduler.start()
        await asyncio.sleep(0.25)
        job.next_due -= 2.0
        before = len(runs)
        await asyncio.sleep(0.3)
        await fast_scheduler.stop()
        assert len(runs) - before <= 3
