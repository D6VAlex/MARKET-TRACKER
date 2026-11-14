from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QBrush, QColor
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget,
                               QGraphicsDropShadowEffect)

from market_tracker.config import APP_NAME, APP_VERSION
from market_tracker.core.event_manager import EventManager
from market_tracker.models import (AssetClass, SystemStatus)
from market_tracker.ui.all_markets_page import AllMarketsPage
from market_tracker.ui.components import (BackgroundWidget, StatusPanel,
                                          Toast, add_text_shadow)
from market_tracker.ui.market_page import MarketPage
from market_tracker.ui.market_window import MarketWindow
from market_tracker.ui.theme import build_stylesheet


def _nav_font(btn: QPushButton) -> None:
    from PySide6.QtGui import QFont
    from market_tracker.ui.theme import DISPLAY_FAMILY
    f = QFont(DISPLAY_FAMILY)
    f.setPixelSize(22)
    f.setBold(True)
    btn.setFont(f)
    btn.setMinimumHeight(40)


class MainWindow(QMainWindow):
    def __init__(self, events: EventManager, on_close=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.events = events
        self._on_close_cb = on_close
        self._quotes_crypto: list = []
        self._quotes_forex: list = []
        self._quotes_stocks: list = []
        self._last_status: SystemStatus | None = None
        self._market_windows: dict[AssetClass, MarketWindow] = {}

        self.setWindowTitle(APP_NAME)
        self.resize(1240, 800)
        self.setMinimumSize(960, 600)

        self._build_ui()
        self._wire_events()
        self._start_clock()

    def _build_ui(self) -> None:
        self._bg = BackgroundWidget(self)
        self._bg.lower()

        central = QWidget()
        central.setObjectName("root")
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        central.setAutoFillBackground(False)
        self.setCentralWidget(central)
        self.setStyleSheet(build_stylesheet())

        root = QVBoxLayout(central)
        root.setContentsMargins(28, 22, 28, 16)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(12)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel(APP_NAME)
        title.setObjectName("brandMark")
        subtitle = QLabel(f"crypto · forex · global equities   |   v{APP_VERSION}")
        subtitle.setObjectName("appSubtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        add_text_shadow(title, blur=7, alpha=180)
        add_text_shadow(subtitle, blur=5, alpha=150)
        top.addLayout(brand)
        top.addStretch(1)

        self.lbl_clock = QLabel("—")
        self.lbl_clock.setObjectName("clock")
        add_text_shadow(self.lbl_clock, blur=5, alpha=150)
        top.addWidget(self.lbl_clock)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setToolTip("Force an immediate data refresh (F5)")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._manual_refresh)
        add_text_shadow(self.btn_refresh, blur=5, alpha=150)
        top.addSpacing(10)
        top.addWidget(self.btn_refresh)

        root.addLayout(top)

        nav_row = QFrame()
        nav_row.setObjectName("navRow")
        nav = QHBoxLayout(nav_row)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(36)

        self.btn_crypto = QPushButton("Crypto")
        self.btn_crypto.setObjectName("navCrypto")
        self.btn_crypto.setCheckable(True)
        self.btn_crypto.setChecked(True)
        self.btn_crypto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_crypto.setShortcut("Ctrl+1")
        _nav_font(self.btn_crypto)
        add_text_shadow(self.btn_crypto, blur=6, alpha=170)

        self.btn_forex = QPushButton("Forex")
        self.btn_forex.setObjectName("navForex")
        self.btn_forex.setCheckable(True)
        self.btn_forex.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_forex.setShortcut("Ctrl+2")
        _nav_font(self.btn_forex)
        add_text_shadow(self.btn_forex, blur=6, alpha=170)

        self.btn_stocks = QPushButton("Stocks")
        self.btn_stocks.setObjectName("navStocks")
        self.btn_stocks.setCheckable(True)
        self.btn_stocks.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stocks.setShortcut("Ctrl+3")
        _nav_font(self.btn_stocks)
        add_text_shadow(self.btn_stocks, blur=6, alpha=170)

        self.btn_all = QPushButton("All Markets")
        self.btn_all.setObjectName("navAll")
        self.btn_all.setCheckable(True)
        self.btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_all.setShortcut("Ctrl+4")
        _nav_font(self.btn_all)
        add_text_shadow(self.btn_all, blur=6, alpha=170)

        self.btn_detach_crypto = QPushButton("Open Crypto window")
        self.btn_detach_crypto.setObjectName("detachBtn")
        self.btn_detach_crypto.setToolTip(
            "Open cryptocurrencies in a separate window")
        self.btn_detach_crypto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detach_crypto.clicked.connect(
            lambda: self._open_market_window(AssetClass.CRYPTO))

        self.btn_detach_forex = QPushButton("Open Forex window")
        self.btn_detach_forex.setObjectName("detachBtn")
        self.btn_detach_forex.setToolTip(
            "Open foreign exchange in a separate window")
        self.btn_detach_forex.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detach_forex.clicked.connect(
            lambda: self._open_market_window(AssetClass.FOREX))

        self.btn_detach_stocks = QPushButton("Open Stocks window")
        self.btn_detach_stocks.setObjectName("detachBtn")
        self.btn_detach_stocks.setToolTip(
            "Open global equities in a separate window")
        self.btn_detach_stocks.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detach_stocks.clicked.connect(
            lambda: self._open_market_window(AssetClass.STOCKS))

        self.btn_crypto.clicked.connect(lambda: self._switch(AssetClass.CRYPTO))
        self.btn_forex.clicked.connect(lambda: self._switch(AssetClass.FOREX))
        self.btn_stocks.clicked.connect(lambda: self._switch(AssetClass.STOCKS))
        self.btn_all.clicked.connect(self._switch_all)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_group.addButton(self.btn_crypto)
        self._nav_group.addButton(self.btn_forex)
        self._nav_group.addButton(self.btn_stocks)
        self._nav_group.addButton(self.btn_all)

        nav.addWidget(self.btn_crypto)
        nav.addWidget(self.btn_forex)
        nav.addWidget(self.btn_stocks)
        nav.addWidget(self.btn_all)
        nav.addStretch(1)
        nav.addWidget(self.btn_detach_crypto)
        nav.addWidget(self.btn_detach_forex)
        nav.addWidget(self.btn_detach_stocks)
        root.addWidget(nav_row)

        rule = QFrame()
        rule.setObjectName("line")
        root.addWidget(rule)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pageHost")
        self.pages.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.pages.setAutoFillBackground(False)
        self._page_specs = {
            AssetClass.CRYPTO: (
                "Cryptocurrencies",
                "dynamically ranked by market capitalization · live USD "
                "prices · Binance websocket stream"),
            AssetClass.FOREX: (
                "Foreign Exchange",
                "majors, crosses & global currencies · bid/ask where "
                "available · delayed sources clearly marked"),
            AssetClass.STOCKS: (
                "Global Equities",
                "curated blue chips · US, Hong Kong, Tokyo, Seoul, Europe · "
                "prices in listing currency · closed venues shown as delayed"),
        }

        def _make_page(ac: AssetClass) -> MarketPage:
            title, subtitle = self._page_specs[ac]
            page = MarketPage(ac, title, subtitle)
            page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
            page.setAutoFillBackground(False)
            return page

        self.page_crypto = _make_page(AssetClass.CRYPTO)
        self.page_forex = _make_page(AssetClass.FOREX)
        self.page_stocks = _make_page(AssetClass.STOCKS)
        self.pages.addWidget(self.page_crypto)
        self.pages.addWidget(self.page_forex)
        self.pages.addWidget(self.page_stocks)

        self.page_all = AllMarketsPage({
            AssetClass.CRYPTO: _make_page(AssetClass.CRYPTO),
            AssetClass.FOREX: _make_page(AssetClass.FOREX),
            AssetClass.STOCKS: _make_page(AssetClass.STOCKS),
        })
        self.page_all.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,
                                   False)
        self.page_all.setAutoFillBackground(False)
        self.pages.addWidget(self.page_all)
        root.addWidget(self.pages, 1)

        from market_tracker.config import STOCKS_BOARD
        self._ccy_map = {s["symbol"].split(".")[0]: s["currency"]
                         for s in STOCKS_BOARD}
        self.page_stocks.set_currency_map(self._ccy_map)
        for p in self.page_all._pages.values():
            p.set_currency_map(self._ccy_map)

        self.status_panel = StatusPanel()
        for child in self.status_panel.findChildren(QLabel):
            add_text_shadow(child, blur=4, alpha=140)
        root.addWidget(self.status_panel)

        self.toast = Toast(self)

        QShortcut(QKeySequence("F5"), self, self._manual_refresh)
        QShortcut(QKeySequence("Ctrl+F"), self, self._focus_search)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)

    def _wire_events(self) -> None:
        s = self.events.signals
        s.quotes_crypto.connect(self._on_quotes_crypto)
        s.quotes_forex.connect(self._on_quotes_forex)
        s.quotes_stocks.connect(self._on_quotes_stocks)
        s.status.connect(self._on_status)
        s.providers.connect(self._on_providers)
        s.toast.connect(self._on_toast)

    def _start_clock(self) -> None:
        self._t0 = time.monotonic()
        self._clock = QTimer(self)
        self._clock.setTimerType(Qt.TimerType.PreciseTimer)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(1000)

    def _tick_clock(self) -> None:
        now = datetime.now(timezone.utc)
        self.lbl_clock.setText(
            f"{now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    def _switch(self, asset_class: AssetClass) -> None:
        idx = {AssetClass.CRYPTO: 0, AssetClass.FOREX: 1,
               AssetClass.STOCKS: 2}[asset_class]
        self.pages.setCurrentIndex(idx)
        page, buffered = {
            AssetClass.CRYPTO: (self.page_crypto, self._quotes_crypto),
            AssetClass.FOREX: (self.page_forex, self._quotes_forex),
            AssetClass.STOCKS: (self.page_stocks, self._quotes_stocks),
        }[asset_class]
        if buffered:
            page.update_quotes(buffered)

    def _switch_all(self) -> None:
        self.pages.setCurrentIndex(3)
        self.page_all.update_quotes(AssetClass.CRYPTO, self._quotes_crypto)
        self.page_all.update_quotes(AssetClass.FOREX, self._quotes_forex)
        self.page_all.update_quotes(AssetClass.STOCKS, self._quotes_stocks)

    def _open_market_window(self, asset_class: AssetClass) -> None:
        existing = self._market_windows.get(asset_class)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        title, _ = self._page_specs[asset_class]
        buffered = {
            AssetClass.CRYPTO: self._quotes_crypto,
            AssetClass.FOREX: self._quotes_forex,
            AssetClass.STOCKS: self._quotes_stocks,
        }[asset_class]
        page = MarketPage(asset_class, title, "")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        page.setAutoFillBackground(False)
        if asset_class is AssetClass.STOCKS:
            page.set_currency_map(self._ccy_map)
        win = MarketWindow(
            page, title,
            on_close=lambda w, ac=asset_class: self._on_market_window_closed(ac))
        self._market_windows[asset_class] = win
        if buffered:
            page.update_quotes(buffered)
        win.show()

    def _on_market_window_closed(self, asset_class: AssetClass) -> None:
        self._market_windows.pop(asset_class, None)

    def _manual_refresh(self) -> None:
        self.btn_refresh.setEnabled(False)
        self.toast.show_message("info", "Refreshing market data", 1500)
        dm = getattr(self.events, "data_manager", None)
        if dm is not None:
            try:
                dm.manual_refresh()
            except Exception:
                self.toast.show_message("error", "Refresh failed", 2000)
        QTimer.singleShot(2000, lambda: self.btn_refresh.setEnabled(True))

    def _focus_search(self) -> None:
        page = self.pages.currentWidget()
        if isinstance(page, MarketPage):
            page.search.setFocus()
            page.search.selectAll()

    def _on_quotes_crypto(self, quotes: list) -> None:
        self._quotes_crypto = quotes
        if self.pages.currentIndex() == 0:
            self.page_crypto.update_quotes(quotes)
        if self.pages.currentIndex() == 3:
            self.page_all.update_quotes(AssetClass.CRYPTO, quotes)
        win = self._market_windows.get(AssetClass.CRYPTO)
        if win is not None and win.isVisible():
            win.page.update_quotes(quotes)

    def _on_quotes_forex(self, quotes: list) -> None:
        self._quotes_forex = quotes
        if self.pages.currentIndex() == 1:
            self.page_forex.update_quotes(quotes)
        if self.pages.currentIndex() == 3:
            self.page_all.update_quotes(AssetClass.FOREX, quotes)
        win = self._market_windows.get(AssetClass.FOREX)
        if win is not None and win.isVisible():
            win.page.update_quotes(quotes)

    def _on_quotes_stocks(self, quotes: list) -> None:
        self._quotes_stocks = quotes
        if self.pages.currentIndex() == 2:
            self.page_stocks.update_quotes(quotes)
        if self.pages.currentIndex() == 3:
            self.page_all.update_quotes(AssetClass.STOCKS, quotes)
        win = self._market_windows.get(AssetClass.STOCKS)
        if win is not None and win.isVisible():
            win.page.update_quotes(quotes)

    def _on_status(self, status: SystemStatus) -> None:
        self._last_status = status
        self.status_panel.update_status(status)

    def _on_providers(self, providers: list) -> None:
        pass

    def _on_toast(self, level: str, message: str) -> None:
        self.toast.show_message(level, message)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._bg.setGeometry(self.rect())
        if self.toast.isVisible():
            pw = self.width()
            self.toast.move(pw - self.toast.width() - 24, 76)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._on_close_cb:
            self._on_close_cb()
        event.accept()


if __name__ == "__main__":
    from market_tracker.core.application import main
    sys.exit(main())
