from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Awaitable, Callable, TypeVar

from market_tracker.config import CONFIG
from market_tracker.utils.exceptions import CircuitOpenError
from market_tracker.utils.logger import get_logger

log = get_logger("breaker")
T = TypeVar("T")


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, name: str, *, failure_threshold: int | None = None,
                 recovery_timeout: float | None = None,
                 half_open_successes: int | None = None) -> None:
        cfg = CONFIG.breaker
        self.name = name
        self.failure_threshold = failure_threshold or cfg.failure_threshold
        self.recovery_timeout = recovery_timeout or cfg.recovery_timeout
        self.half_open_successes = half_open_successes or cfg.half_open_successes

        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()
        self.state_changed_at = time.monotonic()

    @property
    def state(self) -> BreakerState:
        if self._state is BreakerState.OPEN:
            if self._opened_at is not None and \
                    (time.monotonic() - self._opened_at) >= self.recovery_timeout:
                self._state = BreakerState.HALF_OPEN
                self._success_count = 0
                self.state_changed_at = time.monotonic()
                log.info("circuit half-open, probing", extra={
                    "provider": self.name, "state": "half_open"})
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def seconds_until_probe(self) -> float:
        if self._state is not BreakerState.OPEN or self._opened_at is None:
            return 0.0
        return max(0.0, self.recovery_timeout - (time.monotonic() - self._opened_at))

    def record_success(self) -> None:
        self._failure_count = 0
        if self._state is BreakerState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.half_open_successes:
                self._close()
        elif self._state is BreakerState.OPEN:
            self._close()

    def record_failure(self) -> None:
        self._success_count = 0
        if self._state is BreakerState.HALF_OPEN:
            self._trip()
            return
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold and \
                self._state is BreakerState.CLOSED:
            self._trip()

    def _trip(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        self.state_changed_at = time.monotonic()
        log.warning("circuit OPEN (%d consecutive failures) — calls suppressed for %.0fs",
                    self._failure_count, self.recovery_timeout,
                    extra={"provider": self.name, "state": "open"})

    def _close(self) -> None:
        prev = self._state
        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self.state_changed_at = time.monotonic()
        if prev is not BreakerState.CLOSED:
            log.info("circuit CLOSED — provider healthy again",
                     extra={"provider": self.name, "state": "closed"})

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        if self.state is BreakerState.OPEN:
            raise CircuitOpenError(
                f"circuit open for {self.name}; retry in "
                f"{self.seconds_until_probe:.0f}s", provider=self.name)
        try:
            result = await fn()
        except CircuitOpenError:
            raise
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self.record_failure()
            raise
        self.record_success()
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "seconds_until_probe": self.seconds_until_probe,
        }
