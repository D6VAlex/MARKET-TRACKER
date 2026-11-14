from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QVBoxLayout,
                               QWidget)

from market_tracker.ui.components import (BackgroundWidget,
                                          add_text_shadow)
from market_tracker.ui.market_page import MarketPage
from market_tracker.ui.theme import build_stylesheet


class MarketWindow(QMainWindow):

    def __init__(self, page: MarketPage, title: str, on_close=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page = page
        self._on_close_cb = on_close
        self.setWindowTitle(title)
        from PySide6.QtGui import QIcon
        from market_tracker.config import PACKAGE_DIR
        icon_path = PACKAGE_DIR / "assets" / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(980, 640)
        self.setMinimumSize(560, 380)
        self.setStyleSheet(build_stylesheet())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)

        self._bg = BackgroundWidget(self)
        self._bg.lower()

        central = QWidget()
        central.setObjectName("root")
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        central.setAutoFillBackground(False)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(24, 18, 24, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("sectionTitle")
        add_text_shadow(lbl, blur=7, alpha=180)
        header.addWidget(lbl)
        header.addStretch(1)

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("muted")
        add_text_shadow(self.status_lbl, blur=5, alpha=150)
        header.addWidget(self.status_lbl)
        root.addLayout(header)

        page.setParent(central)
        root.addWidget(page, 1)

    def show_status(self, text: str) -> None:
        self.status_lbl.setText(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._bg.setGeometry(self.rect())

    def closeEvent(self, event) -> None:
        if self._on_close_cb is not None:
            try:
                self._on_close_cb(self)
            except Exception:
                pass
        event.accept()
