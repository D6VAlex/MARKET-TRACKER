from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, Integer,
                        String, Text, create_engine, select)
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            sessionmaker)

from market_tracker.config import DB_PATH
from market_tracker.models import AssetClass, Quote
from market_tracker.utils.exceptions import DatabaseError
from market_tracker.utils.logger import get_logger

log = get_logger("db")


class Base(DeclarativeBase):
    pass


class QuoteRow(Base):
    __tablename__ = "quotes_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    asset_class: Mapped[str] = mapped_column(String(8), index=True)
    price: Mapped[float] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SnapshotRow(Base):
    __tablename__ = "snapshots"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetaRow(Base):
    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Database:

    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        self.healthy = True
        try:
            self.engine = create_engine(
                f"sqlite:///{self.path}",
                connect_args={"check_same_thread": False, "timeout": 15},
                future=True,
            )
            Base.metadata.create_all(self.engine)
            self._session_factory = sessionmaker(self.engine,
                                                 expire_on_commit=False)
            log.info("database ready at %s", self.path,
                     extra={"category": "database", "state": "ready"})
        except Exception as exc:
            self.healthy = False
            raise DatabaseError(f"cannot open database: {exc}") from exc

    def record_quotes(self, quotes: Iterable[Quote]) -> int:
        if not self.healthy:
            return 0
        rows = [QuoteRow(
            symbol=q.symbol, asset_class=q.asset_class.value, price=q.price,
            bid=q.bid, ask=q.ask, change_pct_24h=q.change_pct_24h,
            volume_24h=q.volume_24h, market_cap=q.market_cap, rank=q.rank,
            source=q.source, source_ts=q.source_ts, received_at=q.received_at,
        ) for q in quotes]
        if not rows:
            return 0
        try:
            with self._session_factory() as session:
                session.add_all(rows)
                session.commit()
            return len(rows)
        except Exception as exc:
            self.healthy = False
            log.error("db write failed: %s", exc,
                      extra={"category": "database", "state": "error"})
            try:
                self.engine.dispose()
                Base.metadata.create_all(self.engine)
                self.healthy = True
            except Exception:
                pass
            return 0

    def save_snapshot(self, asset_class: AssetClass, quotes: Iterable[Quote]) -> bool:
        if not self.healthy:
            return False
        payload = json.dumps([q.model_dump(mode="json") for q in quotes])
        try:
            with self._session_factory() as session:
                obj = session.get(SnapshotRow, asset_class.value)
                if obj is None:
                    obj = SnapshotRow(key=asset_class.value, payload=payload,
                                      updated_at=datetime.now(timezone.utc))
                    session.add(obj)
                else:
                    obj.payload = payload
                    obj.updated_at = datetime.now(timezone.utc)
                session.commit()
            return True
        except Exception as exc:
            log.warning("snapshot save failed: %s", exc,
                        extra={"category": "database"})
            return False

    def load_snapshot(self, asset_class: AssetClass) -> list[Quote]:
        if not self.healthy:
            return []
        try:
            with self._session_factory() as session:
                obj = session.get(SnapshotRow, asset_class.value)
                if obj is None:
                    return []
                data = json.loads(obj.payload)
                return [Quote.model_validate(d) for d in data]
        except Exception as exc:
            log.warning("snapshot load failed: %s", exc,
                        extra={"category": "database"})
            return []

    def last_price(self, asset_class: AssetClass, symbol: str) -> Quote | None:
        try:
            with self._session_factory() as session:
                stmt = (select(QuoteRow)
                        .where(QuoteRow.asset_class == asset_class.value,
                               QuoteRow.symbol == symbol)
                        .order_by(QuoteRow.received_at.desc())
                        .limit(1))
                row = session.scalars(stmt).first()
                if row is None:
                    return None
                return _row_to_quote(row)
        except Exception as exc:
            log.warning("db query failed: %s", exc,
                        extra={"category": "database"})
            return None

    def price_history(self, asset_class: AssetClass, symbol: str,
                      limit: int = 500) -> list[tuple[datetime, float]]:
        try:
            with self._session_factory() as session:
                stmt = (select(QuoteRow.source_ts, QuoteRow.price)
                        .where(QuoteRow.asset_class == asset_class.value,
                               QuoteRow.symbol == symbol)
                        .order_by(QuoteRow.received_at.desc())
                        .limit(limit))
                rows = session.execute(stmt).all()
                return [(r[0], r[1]) for r in reversed(rows)]
        except Exception as exc:
            log.warning("history query failed: %s", exc,
                        extra={"category": "database"})
            return []

    def prune_history(self, keep_days: int = 14) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
        try:
            with self._session_factory() as session:
                from sqlalchemy import delete
                stmt = delete(QuoteRow).where(
                    QuoteRow.received_at < datetime.fromtimestamp(cutoff, tz=timezone.utc))
                res = session.execute(stmt)
                session.commit()
                return res.rowcount or 0
        except Exception:
            return 0

    def close(self) -> None:
        try:
            self.engine.dispose()
        except Exception:
            pass


def _row_to_quote(row: QuoteRow) -> Quote:
    from market_tracker.models import DataFreshness
    return Quote(
        symbol=row.symbol,
        asset_class=AssetClass(row.asset_class),
        price=row.price, bid=row.bid, ask=row.ask,
        change_pct_24h=row.change_pct_24h, volume_24h=row.volume_24h,
        market_cap=row.market_cap, rank=row.rank,
        source=row.source, source_ts=row.source_ts,
        received_at=row.received_at,
        freshness=DataFreshness.STALE,
    )
