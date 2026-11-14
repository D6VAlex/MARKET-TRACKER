from __future__ import annotations

import time
from typing import Any, Iterable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QFrame,
                               QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QStyle, QStyleOptionViewItem,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from market_tracker.config import FOREX_PIP_DECIMALS
from market_tracker.models import (AggregatedQuote, AssetClass,
                                   DataFreshness, FRESHNESS_LABEL)
from market_tracker.ui.theme import (ACCENT, AMBER, GREEN,
                                     MONO_FAMILY, OFFLINE_COLOR, RED,
                                     STALE_COLOR, TEXT_MUTED, TEXT_PRIMARY,
                                     TEXT_SECONDARY)
from market_tracker.utils.validators import price_decimals

FRESHNESS_COLORS = {
    DataFreshness.LIVE: GREEN,
    DataFreshness.REALTIME: GREEN,
    DataFreshness.DELAYED: AMBER,
    DataFreshness.STALE: STALE_COLOR,
    DataFreshness.OFFLINE: OFFLINE_COLOR,
}


def add_text_shadow(widget: QWidget, blur: float = 6.0,
                    alpha: int = 170) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(1.0, 1.0)
    effect.setColor(QColor(18, 16, 12, alpha))
    widget.setGraphicsEffect(effect)


_BG_FALLBACK = "#10141c"
_MAX_BG_BYTES = 20 * 1024 * 1024


class BackgroundWidget(QWidget):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = None
        from PySide6.QtGui import QImage, QPixmap
        from market_tracker.config import ASSETS_DIR
        bg_path = ASSETS_DIR / "background.jpg"
        if bg_path.exists():
            if bg_path.stat().st_size > _MAX_BG_BYTES:
                print(f"[warn] background image ignored: > "
                      f"{_MAX_BG_BYTES // (1024 * 1024)} MiB")
            else:
                try:
                    img = QImage(str(bg_path))
                    if not img.isNull():
                        self._pixmap = QPixmap.fromImage(img)
                except Exception:
                    self._pixmap = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._pixmap is not None and not self._pixmap.isNull():
            pw, ph = self._pixmap.width(), self._pixmap.height()
            w, h = self.width(), self.height()
            scale = max(w / pw, h / ph)
            tw, th = int(pw * scale), int(ph * scale)
            x, y = (w - tw) // 2, (h - th) // 2
            painter.drawPixmap(x, y, tw, th, self._pixmap)
        else:
            painter.fillRect(self.rect(), QColor(_BG_FALLBACK))
        painter.end()


class GlassItemDelegate(QStyledItemDelegate):

    def paint(self, painter: QPainter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        QApplication.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter)
        if not text:
            return
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        color = fg.color() if isinstance(fg, QBrush) else QColor(TEXT_PRIMARY)
        rect = option.rect.adjusted(8, 0, -8, 0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(opt.font)
        shadow = QPen(QColor(18, 16, 12, 180))
        shadow.setWidthF(1.6)
        painter.setPen(shadow)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(rect.translated(dx, dy), opt.displayAlignment,
                             text)
        painter.setPen(QPen(color))
        painter.drawText(rect, opt.displayAlignment, text)
        painter.restore()


class DotIndicator(QLabel):

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        self.set_state(TEXT_MUTED, "—")

    def set_state(self, color: str, value: str) -> None:
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(
            f"<span style='color:{TEXT_MUTED};font-size:11.5px;"
            f"letter-spacing:0.5px'>"
            f"{self._label}&nbsp;&nbsp;</span>"
            f"<span style='color:{color};font-size:11.5px;"
            f"font-weight:600'>"
            f"{value}</span>")


class QuoteTableModel(QAbstractTableModel):

    def __init__(self, columns: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._columns = columns
        self._rows: list[AggregatedQuote] = []
        self._display_rows: list[AggregatedQuote] = []
        self._filter_text = ""

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._display_rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            if role == Qt.ItemDataRole.TextAlignmentRole and \
                    orientation == Qt.Orientation.Horizontal:
                align = self._columns[section].get("align", Qt.AlignmentFlag.AlignLeft)
                return int(align | Qt.AlignmentFlag.AlignVCenter)
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._columns[section]["title"]
        return section + 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._display_rows[index.row()]
        col = self._columns[index.column()]
        getter = col["getter"]

        if role == Qt.ItemDataRole.DisplayRole:
            return getter(row)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(col.get("align", Qt.AlignmentFlag.AlignLeft) |
                       Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and "color" in col:
            color = col["color"](row)
            if color:
                return QBrush(QColor(color))
        if role == Qt.ItemDataRole.FontRole and col.get("mono"):
            return QFont(MONO_FAMILY)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row)
        SORT_ROLE = Qt.ItemDataRole.UserRole + 1
        if role == SORT_ROLE:
            return col.get("sort_value", getter)(row)
        return None

    def set_rows(self, rows: list[AggregatedQuote]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self._apply_filter()
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        self.beginResetModel()
        self._filter_text = (text or "").strip().lower()
        self._apply_filter()
        self.endResetModel()

    def _apply_filter(self) -> None:
        if not self._filter_text:
            self._display_rows = self._rows
            return
        needle = self._filter_text
        self._display_rows = [
            r for r in self._rows
            if needle in r.symbol.lower() or needle in r.name.lower()
        ]

    def quote_at(self, row: int) -> AggregatedQuote | None:
        if 0 <= row < len(self._display_rows):
            return self._display_rows[row]
        return None

    def _tooltip(self, row: AggregatedQuote) -> str:
        ts = row.source_ts.strftime("%H:%M:%S UTC") if row.source_ts else "—"
        lines = [
            f"<b>{row.symbol}</b> — {FRESHNESS_LABEL[row.freshness]}",
            f"Source timestamp: {ts}",
            f"Providers: {', '.join(row.sources) or 'cache'}",
        ]
        if row.disagreement_pct is not None and row.disagreement_pct > 0.05:
            lines.append(f"Cross-source spread: {row.disagreement_pct:.2f}%")
        if row.last_update_ts:
            lines.append(f"Aggregated at: "
                         f"{row.last_update_ts.strftime('%H:%M:%S UTC')}")
        return "\n".join(lines)


def fmt_price(row: AggregatedQuote) -> str:
    if row.asset_class is AssetClass.FOREX:
        quote_ccy = row.symbol.split("/")[1]
        decimals = FOREX_PIP_DECIMALS.get(quote_ccy, 5)
        return f"{row.price:,.{decimals}f}"
    decimals = price_decimals(row.price)
    return f"${row.price:,.{decimals}f}"


def fmt_big_number(v: float | None, prefix: str = "$") -> str:
    if v is None:
        return "—"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{prefix}{v / div:,.2f}{suffix}"
    return f"{prefix}{v:,.0f}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def change_color(row: AggregatedQuote) -> str | None:
    if row.change_pct_24h is None:
        return TEXT_SECONDARY
    if row.change_pct_24h > 0:
        return GREEN
    if row.change_pct_24h < 0:
        return RED
    return TEXT_SECONDARY


def price_color(row: AggregatedQuote) -> str | None:
    if row.freshness in (DataFreshness.STALE, DataFreshness.OFFLINE):
        return STALE_COLOR
    return TEXT_PRIMARY


def freshness_badge_text(row: AggregatedQuote) -> str:
    label = FRESHNESS_LABEL[row.freshness]
    if row.freshness is DataFreshness.LIVE:
        return "live"
    if row.freshness is DataFreshness.REALTIME:
        return "realtime"
    if row.freshness is DataFreshness.DELAYED:
        return "delayed"
    if row.freshness is DataFreshness.STALE:
        return "stale"
    return "offline"


def freshness_color(row: AggregatedQuote) -> str:
    return FRESHNESS_COLORS.get(row.freshness, TEXT_SECONDARY)


class StatusPanel(QFrame):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusPanel")
        self.setFixedHeight(46)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(16)

        self.lbl_crypto_provider = DotIndicator("CRYPTO SRC")
        self.lbl_forex_provider = DotIndicator("FOREX SRC")
        self.lbl_stocks_provider = DotIndicator("STOCKS SRC")
        self.lbl_backups = DotIndicator("BACKUPS")
        self.lbl_net = DotIndicator("NETWORK")
        self.lbl_ws = DotIndicator("WEBSOCKET")
        self.lbl_sync = DotIndicator("LAST SYNC")
        self.lbl_failures = DotIndicator("FAILED REQ")

        for w in (self.lbl_crypto_provider, self.lbl_forex_provider,
                  self.lbl_stocks_provider, self.lbl_backups, self.lbl_net,
                  self.lbl_ws, self.lbl_sync, self.lbl_failures):
            lay.addWidget(w)
        lay.addStretch(1)

    def update_status(self, status) -> None:
        self.lbl_crypto_provider.set_state(
            GREEN if status.providers else TEXT_MUTED,
            status.active_provider_crypto)
        self.lbl_forex_provider.set_state(
            GREEN if status.providers else TEXT_MUTED,
            status.active_provider_forex)
        self.lbl_stocks_provider.set_state(
            GREEN if status.providers else TEXT_MUTED,
            status.active_provider_stocks)
        n_backups = (len(status.backup_available_crypto) +
                     len(status.backup_available_forex) +
                     len(status.backup_available_stocks))
        self.lbl_backups.set_state(
            GREEN if n_backups else AMBER,
            f"{n_backups} ready" if n_backups else "none")
        if status.internet_online is True:
            self.lbl_net.set_state(GREEN, "online")
        elif status.internet_online is False:
            self.lbl_net.set_state(RED, "offline")
        else:
            self.lbl_net.set_state(AMBER, "unknown")

        ws_state = " ".join(f"{k}:{v}" for k, v in
                            (status.ws_states or {}).items()) or "—"
        ws_color = GREEN if "open" in ws_state else (
            AMBER if ws_state in ("connecting", "reconnecting") else RED)
        self.lbl_ws.set_state(ws_color, ws_state)

        if status.last_sync is None:
            self.lbl_sync.set_state(AMBER, "never")
        else:
            self.lbl_sync.set_state(GREEN,
                                    status.last_sync.strftime("%H:%M:%S"))
        total = status.total_requests or 0
        failed = status.failed_requests or 0
        rate = (failed / total) if total else 0.0
        if failed == 0:
            fail_color = GREEN
        elif rate <= 0.02:
            fail_color = GREEN
        elif rate <= 0.10:
            fail_color = AMBER
        else:
            fail_color = RED
        self.lbl_failures.set_state(fail_color, f"{failed}/{total}")


class Toast(QLabel):

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade)

    def show_message(self, level: str, message: str, msecs: int = 4000) -> None:
        self.setText(message)
        self.setStyleSheet(f"""
            background-color: rgba(24, 20, 15, 185);
            border: 1px solid rgba(214, 188, 154, 60);
            border-radius: 3px;
            padding: 9px 16px;
            color: {TEXT_PRIMARY};
            font-size: 12.5px;
        """)
        self.adjustSize()
        if self.parentWidget():
            pw = self.parentWidget().width()
            self.move(pw - self.width() - 24, 70)
        self.setVisible(True)
        self.raise_()
        self._timer.start(msecs)

    def _fade(self) -> None:
        self.setVisible(False)
