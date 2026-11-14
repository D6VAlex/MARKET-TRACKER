from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from market_tracker.infrastructure.circuit_breaker import CircuitBreaker
from market_tracker.models import AssetClass, ProviderHealth
from market_tracker.utils.logger import get_logger

log = get_logger("health")


class ProviderHealthMonitor:

    def __init__(self) -> None:
        self._stats: dict[str, dict] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._aliases: dict[str, str] = {}
        self._ws_states: dict[str, str] = {}
        self._primary: dict[AssetClass, set[str]] = {
            AssetClass.CRYPTO: set(),
            AssetClass.FOREX: set(),
            AssetClass.STOCKS: set(),
        }

    def register(self, provider_id: str, display_name: str,
                 asset_class: AssetClass | None = None,
                 is_primary: bool = False) -> CircuitBreaker:
        if provider_id in self._stats:
            return self._breakers[provider_id]
        self._stats[provider_id] = {
            "display_name": display_name,
            "asset_class": asset_class,
            "total_requests": 0,
            "total_failures": 0,
            "consecutive_failures": 0,
            "last_success": None,
            "last_failure": None,
            "last_error": None,
            "last_error_category": None,
            "latency_ms": None,
            "reliability": 1.0,
            "is_primary": is_primary,
        }
        self._aliases[provider_id] = display_name
        self._breakers[provider_id] = CircuitBreaker(provider_id)
        if asset_class is not None and is_primary:
            self._primary[asset_class].add(provider_id)
        return self._breakers[provider_id]

    def breaker(self, provider_id: str) -> CircuitBreaker | None:
        return self._breakers.get(provider_id)

    def mark_primary(self, provider_id: str, asset_class: AssetClass) -> None:
        self._primary[asset_class] = {provider_id}
        stats = self._stats.get(provider_id)
        if stats:
            for pid, s in self._stats.items():
                if s["asset_class"] is asset_class:
                    s["is_primary"] = pid == provider_id

    def primaries(self, asset_class: AssetClass) -> set[str]:
        return set(self._primary.get(asset_class, set()))

    def record_request(self, provider_id: str) -> None:
        stats = self._stats.get(provider_id)
        if stats:
            stats["total_requests"] += 1

    def record_success(self, provider_id: str, latency_ms: float | None = None,
                       ws: bool = False) -> None:
        stats = self._stats.get(provider_id)
        if stats is None:
            return
        now = datetime.now(timezone.utc)
        stats["total_requests"] += 1 if not ws else 0
        stats["consecutive_failures"] = 0
        stats["last_success"] = now
        if latency_ms is not None:
            stats["latency_ms"] = latency_ms
        stats["reliability"] = 0.9 * stats["reliability"] + 0.1
        br = self._breakers.get(provider_id)
        if br:
            br.record_success()

    def record_failure(self, provider_id: str, error: BaseException,
                       ws: bool = False) -> None:
        stats = self._stats.get(provider_id)
        if stats is None:
            return
        from market_tracker.utils.exceptions import categorize_exception
        now = datetime.now(timezone.utc)
        stats["total_requests"] += 1 if not ws else 0
        stats["total_failures"] += 1
        stats["consecutive_failures"] += 1
        stats["last_failure"] = now
        stats["last_error"] = str(error)[:160]
        stats["last_error_category"] = categorize_exception(error)
        stats["reliability"] = 0.9 * stats["reliability"]
        br = self._breakers.get(provider_id)
        if br:
            br.record_failure()
        log.warning("provider failure: %s: %s", provider_id, error,
                    extra={"category": stats["last_error_category"],
                           "provider": provider_id,
                           "state": "failed"})

    def set_ws_state(self, provider_id: str, state: str) -> None:
        prev = self._ws_states.get(provider_id)
        if prev != state:
            log.info("websocket %s -> %s", provider_id, state,
                     extra={"provider": provider_id, "state": state,
                            "category": "websocket"})
        self._ws_states[provider_id] = state

    def ws_state(self, provider_id: str) -> str:
        return self._ws_states.get(provider_id, "n/a")

    def provider_state(self, provider_id: str) -> str:
        stats = self._stats.get(provider_id)
        if stats is None:
            return "unknown"
        br = self._breakers.get(provider_id)
        if br and br.state.value == "open":
            return "open"
        if stats["consecutive_failures"] == 0 and stats["last_success"]:
            return "healthy" if stats["reliability"] > 0.75 else "degraded"
        if stats["consecutive_failures"] >= 3:
            return "down"
        if stats["reliability"] < 0.5:
            return "degraded"
        if stats["last_success"]:
            return "degraded"
        return "unknown"

    def snapshot(self) -> list[ProviderHealth]:
        out: list[ProviderHealth] = []
        for pid, s in sorted(self._stats.items()):
            br = self._breakers.get(pid)
            lat = s["latency_ms"]
            out.append(ProviderHealth(
                provider_id=pid,
                display_name=s["display_name"],
                asset_class=s["asset_class"],
                state=self.provider_state(pid),
                breaker_state=br.state.value if br else "closed",
                last_success=s["last_success"],
                last_failure=s["last_failure"],
                last_error=s["last_error"],
                last_error_category=s["last_error_category"],
                consecutive_failures=s["consecutive_failures"],
                total_requests=s["total_requests"],
                total_failures=s["total_failures"],
                latency_ms=lat,
                avg_latency_ms=lat,
                reliability=round(s["reliability"], 3),
                is_primary=s["is_primary"],
                supports_ws=pid in ("binance",),
                ws_state=self._ws_states.get(pid, "n/a"),
            ))
        return out

    def available_providers(self, asset_class: AssetClass) -> list[str]:
        return [pid for pid, s in self._stats.items()
                if s["asset_class"] is asset_class
                and self.provider_state(pid) not in ("down", "open")]

    def totals(self) -> tuple[int, int]:
        total = sum(s["total_requests"] for s in self._stats.values())
        failed = sum(s["total_failures"] for s in self._stats.values())
        return total, failed
