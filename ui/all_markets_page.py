from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                               QVBoxLayout, QWidget)

from market_tracker.models import AggregatedQuote, AssetClass
from market_tracker.ui.components import add_text_shadow
from market_tracker.ui.market_page import MarketPage

_SECTION_SUBTITLES = {
    AssetClass.CRYPTO: "Cryptocurrencies",
    AssetClass.FOREX: "Foreign Exchange",
    AssetClass.STOCKS: "Global Equities",
}


class AllMarketsPage(QWidget):

    def __init__(self, pages: dict[AssetClass, MarketPage],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pages = pages
        self._quotes: dict[AssetClass, list[AggregatedQuote]] = {
            ac: [] for ac in pages}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(10)

        header = QHBoxLayout()
        lbl_title = QLabel("All Markets")
        lbl_title.setObjectName("sectionTitle")
        self.lbl_subtitle = QLabel("crypto · forex · global equities — "
                                   "all live side by side")
        self.lbl_subtitle.setObjectName("muted")
        box = QVBoxLayout()
        box.setSpacing(1)
        box.addWidget(lbl_title)
        box.addWidget(self.lbl_subtitle)
        add_text_shadow(lbl_title, blur=7, alpha=180)
        add_text_shadow(self.lbl_subtitle, blur=5, alpha=150)
        header.addLayout(box)
        header.addStretch(1)
        outer.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,
                                 False)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollArea > QWidget { background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")

        host = QWidget()
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        host.setAutoFillBackground(False)
        self.scroll.setWidget(host)

        cols = QVBoxLayout(host)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(14)

        self._sections: dict[AssetClass, tuple[QLabel, MarketPage]] = {}
        for ac in (AssetClass.CRYPTO, AssetClass.FOREX, AssetClass.STOCKS):
            page = pages.get(ac)
            if page is None:
                continue
            sec_lbl = QLabel(_SECTION_SUBTITLES[ac])
            sec_lbl.setObjectName("sectionTitle")
            add_text_shadow(sec_lbl, blur=6, alpha=170)
            cols.addWidget(sec_lbl)
            page.setParent(host)
            page.setMinimumHeight(360)
            cols.addWidget(page)
            self._sections[ac] = (sec_lbl, page)

        outer.addWidget(self.scroll, 1)

    def update_quotes(self, asset_class: AssetClass,
                      quotes: list[AggregatedQuote]) -> None:
        self._quotes[asset_class] = quotes
        page = self._pages.get(asset_class)
        if page is not None:
            page.update_quotes(quotes)
        live = sum(1 for ac, qs in self._quotes.items() if qs)
        self.lbl_subtitle.setText(
            f"crypto · forex · global equities — {live} of 3 markets live")
