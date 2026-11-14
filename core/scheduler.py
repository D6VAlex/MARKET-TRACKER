from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from market_tracker.utils.logger import get_logger

log = get_logger("scheduler")

JobFn = Callable[[], Awaitable[Any]]


@dataclass
class JobStats:
    runs: int = 0
    skips: int = 0
    errors: int = 0
    last_duration: float = 0.0
    max_duration: float = 0.0
    last_error: str | None = None
    drift_ms_max: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "runs": self.runs, "skips": self.skips, "errors": self.errors,
            "last_ms": round(self.last_duration * 1000, 1),
            "max_ms": round(self.max_duration * 1000, 1),
            "drift_ms_max": round(self.drift_ms_max, 1),
            "last_error": self.last_error,
        }


@dataclass
class Job:
    name: str
    interval: float
    fn: JobFn
    stats: JobStats = field(default_factory=JobStats)
    next_due: float = 0.0
    running: bool = False
    last_ran: float = 0.0
    error_backoff: float = 0.0
    consecutive_errors: int = 0


class Scheduler:

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

    def add_job(self, name: str, interval: float, fn: JobFn,
                *, start_immediately: bool = True) -> Job:
        if name in self._jobs:
            raise ValueError(f"job {name!r} already exists")
        if interval <= 0:
            raise ValueError("interval must be > 0")
        job = Job(name=name, interval=interval, fn=fn)
        now = time.monotonic()
        job.next_due = now if start_immediately else now + interval
        self._jobs[name] = job
        return job

    def remove_job(self, name: str) -> None:
        self._jobs.pop(name, None)

    def jobs_snapshot(self) -> dict[str, dict[str, Any]]:
        return {n: j.stats.as_dict() for n, j in sorted(self._jobs.items())}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduler")
        log.info("scheduler started (%d jobs: %s)", len(self._jobs),
                 ", ".join(f"{j.name}@{j.interval:g}s"
                           for j in self._jobs.values()))

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            next_due: float | None = None
            for job in self._jobs.values():
                if job.next_due <= now:
                    self._spawn_run(job, now)
                    job.next_due += job.interval
                    if job.next_due <= now:
                        skipped = int((now - job.next_due) / job.interval) + 1
                        job.next_due += skipped * job.interval
                if next_due is None or job.next_due < next_due:
                    next_due = job.next_due

            if next_due is None:
                timeout = 1.0
            else:
                timeout = max(0.005, min(next_due - time.monotonic(), 1.0))

            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass

    def _spawn_run(self, job: Job, now: float) -> None:
        if job.running:
            job.stats.skips += 1
            return
        job.running = True
        task = asyncio.create_task(self._run_job(job))
        task.add_done_callback(lambda _t, j=job: setattr(j, "running", False))

    async def _run_job(self, job: Job) -> None:
        started = time.monotonic()
        try:
            await job.fn()
            duration = time.monotonic() - started
            job.stats.runs += 1
            job.stats.last_duration = duration
            job.stats.max_duration = max(job.stats.max_duration, duration)
            drift = max(0.0, (time.monotonic() - started) - job.interval)
            job.stats.drift_ms_max = max(job.stats.drift_ms_max,
                                        drift * 1000.0)
            if job.consecutive_errors:
                job.consecutive_errors = 0
                job.error_backoff = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.stats.errors += 1
            job.stats.last_error = f"{type(exc).__name__}: {exc}"
            job.consecutive_errors += 1
            job.error_backoff = min(job.error_backoff * 2 or job.interval,
                                    job.interval * 10)
            job.next_due = time.monotonic() + job.error_backoff
            log.error("job %s failed (%s) — backoff %.1fs", job.name,
                      job.stats.last_error, job.error_backoff,
                      extra={"category": "job_error"})
