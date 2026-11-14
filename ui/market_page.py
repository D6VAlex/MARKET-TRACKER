from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QTableView, QVBoxLayout,
                               QWidget)

from market_tracker.config import FOREX_PIP_DECIMALS
from market_tracker.models import AggregatedQuote, AssetClass
from market_tracker.ui.components import (GlassItemDelegate, QuoteTableModel,
                                          change_color,
                                          fmt_big_number, fmt_pct, fmt_price,
                                          freshness_badge_text,
                                          freshness_color, price_color)
from market_tracker.ui.theme import (MONO_FAMILY, TEXT_MUTED, TEXT_SECONDARY)
from market_tracker.utils.validators import price_decimals

SEARCH_HINT = "search"


class MarketPage(QWidget):

    manual_refresh_requested = Signal()

    def __init__(self, asset_class: AssetClass, title: str, subtitle: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.asset_class = asset_class
        self._quotes: list[AggregatedQuote] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 0)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(16)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        lbl_title = QLabel(title)
        lbl_title.setObjectName("sectionTitle")
        self.lbl_subtitle = QLabel(subtitle)
        self.lbl_subtitle.setObjectName("muted")
        title_box.addWidget(lbl_title)
        title_box.addWidget(self.lbl_subtitle)
        from market_tracker.ui.components import add_text_shadow
        add_text_shadow(lbl_title, blur=7, alpha=180)
        add_text_shadow(self.lbl_subtitle, blur=5, alpha=150)
        header.addLayout(title_box)
        header.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText(SEARCH_HINT)
        self.search.setFixedWidth(210)
        self.search.textChanged.connect(self._on_search)
        from market_tracker.ui.components import add_text_shadow as _ats
        _ats(self.search, blur=4, alpha=130)
        header.addWidget(self.search)

        root.addLayout(header)

        self.model = QuoteTableModel(self._columns())
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole + 1)
        self.proxy.setDynamicSortFilter(True)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setItemDelegate(GlassItemDelegate(self.table))
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.setWordWrap(False)
        self.table.viewport().setAutoFillBackground(False)
        self.table.setAutoFillBackground(False)

        root.addWidget(self.table, 1)

        if asset_class is AssetClass.CRYPTO:
            self.proxy.sort(5, Qt.SortOrder.DescendingOrder)
        elif asset_class is AssetClass.STOCKS:
            self.proxy.sort(0, Qt.SortOrder.AscendingOrder)

    def _columns(self) -> list[dict]:
        if self.asset_class is AssetClass.CRYPTO:
            return self._crypto_columns()
        if self.asset_class is AssetClass.FOREX:
            return self._forex_columns()
        return self._stock_columns()

    def _crypto_columns(self) -> list[dict]:
        return [
            dict(title="No.", getter=lambda r: f"{r.rank:02d}" if r.rank else "—",
                 sort_value=lambda r: r.rank if r.rank is not None else 99999,
                 align=Qt.AlignmentFlag.AlignCenter, mono=True),
            dict(title="Asset", getter=lambda r: f"{r.name}  ({r.symbol})",
                 sort_value=lambda r: r.symbol, mono=False),
            dict(title="Price", getter=fmt_price,
                 sort_value=lambda r: r.price,
                 color=price_color, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="24h %", getter=lambda r: fmt_pct(r.change_pct_24h),
                 sort_value=lambda r: r.change_pct_24h or 0.0,
                 color=change_color, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="24h volume", getter=lambda r: fmt_big_number(r.volume_24h),
                 sort_value=lambda r: r.volume_24h or 0.0, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Market cap", getter=lambda r: fmt_big_number(r.market_cap),
                 sort_value=lambda r: r.market_cap or 0.0, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Sources", getter=lambda r: str(r.source_count),
                 sort_value=lambda r: r.source_count,
                 align=Qt.AlignmentFlag.AlignCenter, mono=True),
            dict(title="Status", getter=freshness_badge_text,
                 sort_value=lambda r: r.freshness.value,
                 color=freshness_color,
                 align=Qt.AlignmentFlag.AlignCenter),
        ]

    def _forex_columns(self) -> list[dict]:
        return [
            dict(title="Pair", getter=lambda r: r.symbol,
                 sort_value=lambda r: r.symbol, mono=False),
            dict(title="Currency", getter=lambda r: r.name,
                 sort_value=lambda r: r.name, mono=False),
            dict(title="Rate", getter=fmt_price,
                 sort_value=lambda r: r.price,
                 color=price_color, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Bid", getter=lambda r: self._fmt_fx(r.bid),
                 sort_value=lambda r: r.bid or 0.0, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Ask", getter=lambda r: self._fmt_fx(r.ask),
                 sort_value=lambda r: r.ask or 0.0, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Day %", getter=lambda r: fmt_pct(r.change_pct_24h),
                 sort_value=lambda r: r.change_pct_24h or 0.0,
                 color=change_color, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Sources", getter=lambda r: str(r.source_count),
                 sort_value=lambda r: r.source_count,
                 align=Qt.AlignmentFlag.AlignCenter, mono=True),
            dict(title="Status", getter=freshness_badge_text,
                 sort_value=lambda r: r.freshness.value,
                 color=freshness_color,
                 align=Qt.AlignmentFlag.AlignCenter),
        ]

    def _stock_columns(self) -> list[dict]:
        return [
            dict(title="Ticker", getter=lambda r: r.symbol,
                 sort_value=lambda r: r.symbol, mono=True),
            dict(title="Company", getter=lambda r: r.name,
                 sort_value=lambda r: r.name, mono=False),
            dict(title="Last", getter=lambda r: self._fmt_stock(r),
                 sort_value=lambda r: r.price,
                 color=price_color, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Day %", getter=lambda r: fmt_pct(r.change_pct_24h),
                 sort_value=lambda r: r.change_pct_24h or 0.0,
                 color=change_color, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Volume", getter=lambda r: fmt_big_number(r.volume_24h, ""),
                 sort_value=lambda r: r.volume_24h or 0.0, mono=True,
                 align=Qt.AlignmentFlag.AlignRight),
            dict(title="Ccy", getter=lambda r: self._stock_ccy(r),
                 sort_value=lambda r: self._stock_ccy(r),
                 align=Qt.AlignmentFlag.AlignCenter, mono=True),
            dict(title="Sources", getter=lambda r: str(r.source_count),
                 sort_value=lambda r: r.source_count,
                 align=Qt.AlignmentFlag.AlignCenter, mono=True),
            dict(title="Status", getter=freshness_badge_text,
                 sort_value=lambda r: r.freshness.value,
                 color=freshness_color,
                 align=Qt.AlignmentFlag.AlignCenter),
        ]

    @staticmethod
    def _fmt_fx(v: float | None) -> str:
        if v is None:
            return "—"
        return f"{v:,.5f}".rstrip("0").rstrip(".") if v < 10 else f"{v:,.4f}"

    def _fmt_stock(self, r: AggregatedQuote) -> str:
        return f"{r.price:,.2f}"

    def _stock_ccy(self, r: AggregatedQuote) -> str:
        ccy = getattr(self, "_ccy_map", {}).get(r.symbol)
        return ccy or "USD"

    def set_currency_map(self, m: dict[str, str]) -> None:
        self._ccy_map = m

    def update_quotes(self, quotes: list[AggregatedQuote]) -> None:
        self._quotes = quotes
        self.model.set_rows(quotes)
        stale = sum(1 for q in quotes if q.stale)
        if quotes:
            if stale:
                self.lbl_subtitle.setText(
                    f"{len(quotes)} instruments · {stale} showing stale data")
            else:
                self.lbl_subtitle.setText(
                    f"{len(quotes)} instruments · live feeds")
        else:
            self.lbl_subtitle.setText("waiting for data — check your connection")

    def _on_search(self, text: str) -> None:
        self.model.set_filter(text)
