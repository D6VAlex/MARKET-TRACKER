from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from market_tracker.config import CONFIG
from market_tracker.utils.exceptions import (AuthError, ConnectionLostError,
                                             DnsError, HttpError,
                                             MalformedDataError, NetworkError,
                                             RateLimitError, TimeoutError_)
from market_tracker.utils.logger import get_logger

log = get_logger("http")

_USER_AGENT = ("MarketTracker/1.0 (+https://localhost; PySide6 desktop "
               "market monitor)")

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class HttpClient:

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._latencies: list[float] = []
        self.total_requests = 0
        self.total_failures = 0

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    connector = aiohttp.TCPConnector(
                        limit=CONFIG.network.pool_size,
                        limit_per_host=CONFIG.network.pool_size // 2,
                        ttl_dns_cache=300,
                        enable_cleanup_closed=True,
                    )
                    timeout = aiohttp.ClientTimeout(
                        total=CONFIG.network.request_timeout,
                        connect=CONFIG.network.connect_timeout,
                    )
                    self._session = aiohttp.ClientSession(
                        connector=connector, timeout=timeout,
                        headers={"User-Agent": _USER_AGENT,
                                 "Accept": "application/json"})
                    log.info("HTTP session created (pool=%d)",
                             CONFIG.network.pool_size,
                             extra={"state": "session_created"})
        return self._session

    async def get_json(self, url: str, *, params: dict[str, Any] | None = None,
                       headers: dict[str, str] | None = None,
                       provider: str = "", raw_text: bool = False) -> Any:
        session = await self._ensure_session()
        self.total_requests += 1
        started = time.monotonic()
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                clen = resp.headers.get("Content-Length")
                if clen and clen.isdigit() and int(clen) > MAX_RESPONSE_BYTES:
                    self.total_failures += 1
                    raise MalformedDataError(
                        f"response too large: {clen} bytes "
                        f"(limit {MAX_RESPONSE_BYTES})", provider=provider)
                body_bytes = b""
                while True:
                    chunk = await resp.content.read(65536)
                    if not chunk:
                        break
                    body_bytes += chunk
                    if len(body_bytes) > MAX_RESPONSE_BYTES:
                        self.total_failures += 1
                        raise MalformedDataError(
                            f"response exceeded "
                            f"{MAX_RESPONSE_BYTES} bytes", provider=provider)
                body = body_bytes.decode("utf-8", errors="replace")
                elapsed_ms = (time.monotonic() - started) * 1000.0
                self._record_latency(elapsed_ms)
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        ra_s = float(retry_after) if retry_after else None
                    except ValueError:
                        ra_s = None
                    raise RateLimitError(provider=provider,
                                          detail={"retry_after": ra_s})
                if resp.status in (401, 403):
                    raise AuthError(status=resp.status, provider=provider,
                                    detail=body[:200])
                if resp.status >= 500:
                    raise HttpError(f"server error {resp.status}",
                                    status=resp.status, provider=provider)
                if resp.status >= 400:
                    raise HttpError(f"client error {resp.status}",
                                    status=resp.status, provider=provider)
                if not body.strip():
                    raise MalformedDataError("empty response body",
                                             provider=provider)
                if raw_text:
                    return body
                try:
                    return _parse_json(body)
                except MalformedDataError:
                    self.total_failures += 1
                    raise
        except (RateLimitError, AuthError, HttpError, MalformedDataError):
            self.total_failures += 1
            raise
        except asyncio.TimeoutError:
            self.total_failures += 1
            raise TimeoutError_(f"request timed out after "
                                f"{CONFIG.network.request_timeout}s",
                                provider=provider) from None
        except aiohttp.ClientConnectorError as exc:
            self.total_failures += 1
            if isinstance(exc.os_error, asyncio.TimeoutError) or \
                    "name" in str(getattr(exc, "os_error", "")).lower():
                raise DnsError(f"DNS/connect failure: {exc}", provider=provider) from exc
            raise NetworkError(f"connection failed: {exc}", provider=provider) from exc
        except aiohttp.ClientConnectionError as exc:
            self.total_failures += 1
            raise ConnectionLostError(str(exc), provider=provider) from exc
        except aiohttp.ClientError as exc:
            self.total_failures += 1
            raise NetworkError(f"aiohttp error: {exc}", provider=provider) from exc

    async def get_text(self, url: str, *, params: dict[str, Any] | None = None,
                       headers: dict[str, str] | None = None,
                       provider: str = "") -> str:
        body = await self.get_json(url, params=params, headers=headers,
                                   provider=provider, raw_text=True)
        if not isinstance(body, str):
            self.total_failures += 1
            raise MalformedDataError("expected text payload",
                                     provider=provider)
        return body

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _record_latency(self, ms: float) -> None:
        self._latencies.append(ms)
        if len(self._latencies) > 100:
            del self._latencies[:50]

    @property
    def avg_latency_ms(self) -> float | None:
        if not self._latencies:
            return None
        return sum(self._latencies) / len(self._latencies)


def _parse_json(body: str) -> Any:
    import json as _json
    try:
        return _json.loads(body)
    except _json.JSONDecodeError as exc:
        raise MalformedDataError(f"invalid JSON: {exc}") from exc
