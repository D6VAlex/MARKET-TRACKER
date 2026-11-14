from __future__ import annotations

import time
from collections import defaultdict
from typing import Iterable, Iterator

from market_tracker.models import (AssetClass, DataFreshness, Quote,
                                   utcnow)
from market_tracker.utils.logger import get_logger

log = get_logger("cache")


class _Entry:
    __slots__ = ("quote", "monotonic")

    def __init__(self, quote: Quote) -> None:
        self.quote = quote
        self.monotonic = time.monotonic()


class QuoteCache:
    def __init__(self, max_age_seconds: float = 3600.0) -> None:
        self._entries: dict[tuple[AssetClass, str], _Entry] = {}
        self._by_provider: dict[str, dict[tuple[AssetClass, str], _Entry]] = defaultdict(dict)
        self.max_age_seconds = max_age_seconds
        self.hits = 0
        self.misses = 0
        self.writes = 0

    def put(self, quote: Quote) -> None:
        key = quote.key()
        self._entries[key] = _Entry(quote)
        self._by_provider[quote.source][key] = _Entry(quote)
        self.writes += 1

    def put_many(self, quotes: Iterable[Quote]) -> int:
        n = 0
        for q in quotes:
            self.put(q)
            n += 1
        return n

    def get(self, asset_class: AssetClass, symbol: str) -> Quote | None:
        entry = self._entries.get((asset_class, symbol))
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        return entry.quote

    def get_all(self, asset_class: AssetClass) -> list[Quote]:
        merged: dict[tuple[str, str], Quote] = {}
        for prov, store in self._by_provider.items():
            for (ac, sym), entry in store.items():
                if ac is not asset_class:
                    continue
                key = (prov, sym)
                cur = merged.get(key)
                if cur is None or entry.quote.source_ts > cur.source_ts:
                    merged[key] = entry.quote
        return list(merged.values())

    def get_by_provider(self, provider: str) -> list[Quote]:
        return [e.quote for e in self._by_provider.get(provider, {}).values()]

    def get_by_symbol_and_class(self, quote: Quote) -> list[Quote]:
        key_cls = quote.asset_class
        out: list[Quote] = []
        for prov_store in self._by_provider.values():
            entry = prov_store.get((key_cls, quote.symbol))
            if entry is not None:
                out.append(entry.quote)
        out.sort(key=lambda q: q.source_ts, reverse=True)
        return out

    def symbols(self, asset_class: AssetClass) -> list[str]:
        return sorted(k[1] for k in self._entries if k[0] is asset_class)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: tuple[AssetClass, str]) -> bool:
        return key in self._entries

    def __iter__(self) -> Iterator[tuple[AssetClass, str]]:
        return iter(self._entries)

    def evict_expired(self) -> int:
        now = time.monotonic()
        dead = [k for k, e in self._entries.items()
                if now - e.monotonic > self.max_age_seconds]
        for k in dead:
            del self._entries[k]
            for prov_store in self._by_provider.values():
                prov_store.pop(k, None)
        if dead:
            log.info("evicted %d expired cache entries", len(dead),
                     extra={"category": "cache"})
        return len(dead)

    def snapshot_keys(self) -> list[tuple[AssetClass, str]]:
        return list(self._entries.keys())

    @property
    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "entries": len(self._entries),
            "writes": self.writes,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else 0.0,
        }
