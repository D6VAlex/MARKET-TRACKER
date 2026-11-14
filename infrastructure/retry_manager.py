from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, TypeVar

from market_tracker.config import CONFIG
from market_tracker.utils.exceptions import (AuthError, CircuitOpenError,
                                             RateLimitError)
from market_tracker.utils.logger import get_logger

log = get_logger("retry")
T = TypeVar("T")

_NON_RETRYABLE = (AuthError, CircuitOpenError)


class RetryManager:

    def __init__(self, *, max_attempts: int | None = None,
                 base: float | None = None, cap: float | None = None,
                 jitter_ratio: float | None = None) -> None:
        net = CONFIG.network
        self.max_attempts = max_attempts or net.max_retries + 1
        self.base = base or net.backoff_base
        self.cap = cap or net.backoff_cap
        self.jitter_ratio = jitter_ratio if jitter_ratio is not None else net.jitter_ratio

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None and retry_after > 0:
            return min(retry_after, self.cap * 2)
        raw = min(self.base * (2 ** (attempt - 1)), self.cap)
        spread = raw * self.jitter_ratio
        return max(0.05, random.uniform(raw - spread, raw + spread))

    async def run(self, fn: Callable[[], Awaitable[T]], *, label: str,
                  on_attempt: Callable[[int, BaseException], float | None] | None = None,
                  suppress: bool = False) -> T | None:
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await fn()
            except _NON_RETRYABLE:
                raise
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:
                last_exc = exc
                retry_after = on_attempt(exc) if on_attempt else None
                if attempt >= self.max_attempts:
                    break
                delay = self._delay(attempt, retry_after)
                log.warning("attempt %d/%d for %s failed (%s: %s) — retrying in %.2fs",
                            attempt, self.max_attempts, label,
                            type(exc).__name__, exc, delay,
                            extra={"category": "retry", "attempt": attempt,
                                   "provider": label})
                await asyncio.sleep(delay)
        if last_exc is not None:
            if suppress:
                log.error("%s exhausted retries: %s", label, last_exc,
                          extra={"category": "exhausted", "provider": label})
                return None
            raise last_exc
        return None


_DEFAULT_MANAGER: RetryManager | None = None


def default_retry_manager() -> RetryManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = RetryManager()
    return _DEFAULT_MANAGER
