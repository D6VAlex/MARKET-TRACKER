from __future__ import annotations

from pathlib import Path

BG_IMAGE = "assets/background.jpg"

GLASS_BG = "transparent"
GLASS_BG_SOFT = "transparent"
GLASS_ROW = "transparent"
GLASS_ROW_ALT = "transparent"
GLASS_HOVER = "rgba(255, 244, 227, 24)"
GLASS_HEADER = "transparent"

BORDER = "rgba(214, 188, 154, 55)"
BORDER_STRONG = "rgba(214, 188, 154, 90)"

TEXT_PRIMARY = "#f6eee0"
TEXT_SECONDARY = "#e3d5bf"
TEXT_MUTED = "#c9b898"

ACCENT = "#e8a860"
ACCENT_SOFT = "rgba(232, 168, 96, 45)"
GREEN = "#9fce8f"
RED = "#e08a7a"
AMBER = "#e8c878"
CYAN = "#e8a860"
FOREX_ACCENT = "#e8c878"
CRYPTO_ACCENT = "#e8a860"
STALE_COLOR = "#e8c878"
OFFLINE_COLOR = "#e08a7a"

DISPLAY_FAMILY = "'Segoe Script', 'Segoe Print', 'Comic Sans MS', 'Segoe UI', cursive"
FONT_FAMILY = "'Segoe UI', 'Noto Sans', sans-serif"
MONO_FAMILY = "'Cascadia Mono', 'Consolas', 'Courier New', 'DejaVu Sans Mono', monospace"

RULE = "rgba(214, 188, 154, 45)"


def build_stylesheet() -> str:
    return f"""
* {{
    font-family: {FONT_FAMILY};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_SOFT};
    selection-color: #ffffff;
}}

QMainWindow, QWidget#root {{
    background: transparent;
}}
QWidget#pageHost {{
    background: transparent;
}}

QFrame#line {{
    background: transparent;
    max-height: 1px;
    border: none;
    border-top: 1px solid {RULE};
}}

QLabel#brandMark {{
    font-family: {DISPLAY_FAMILY};
    font-size: 34px;
    color: {TEXT_PRIMARY};
    letter-spacing: 1px;
    padding: 2px 0px;
}}
QLabel#appSubtitle {{
    color: {TEXT_MUTED};
    font-size: 12.5px;
    letter-spacing: 0.3px;
}}
QLabel#clock {{
    color: {TEXT_SECONDARY};
    font-size: 12.5px;
    letter-spacing: 0.3px;
}}

QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 6px 14px;
    font-size: 13px;
    color: {TEXT_SECONDARY};
}}
QPushButton:hover {{
    color: {TEXT_PRIMARY};
    border-color: {BORDER};
}}
QPushButton:pressed {{ color: {ACCENT}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; }}

QPushButton#detachBtn {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 5px 10px;
    font-size: 12px;
    color: {TEXT_MUTED};
}}
QPushButton#detachBtn:hover {{
    color: {TEXT_PRIMARY};
    border-color: {BORDER_STRONG};
    background: {GLASS_HOVER};
}}

QFrame#navRow {{
    background: transparent;
    border: none;
}}

QPushButton#navCrypto, QPushButton#navForex, QPushButton#navStocks, QPushButton#navAll {{
    font-family: {DISPLAY_FAMILY};
    background: transparent;
    border: none;
    border-radius: 0px;
    padding: 6px 2px 8px 2px;
    color: {TEXT_MUTED};
}}
QPushButton#navCrypto:hover, QPushButton#navForex:hover, QPushButton#navStocks:hover, QPushButton#navAll:hover {{
    color: {TEXT_PRIMARY};
}}
QPushButton#navCrypto:checked, QPushButton#navForex:checked, QPushButton#navStocks:checked, QPushButton#navAll:checked {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}

QLineEdit {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {RULE};
    border-radius: 0px;
    padding: 6px 2px 6px 2px;
    font-size: 13px;
    color: {TEXT_PRIMARY};
}}
QLineEdit:hover {{ border-bottom: 1px solid {BORDER_STRONG}; }}
QLineEdit:focus {{
    border-bottom: 1px solid {ACCENT};
}}

QTableView {{
    background-color: transparent;
    alternate-background-color: transparent;
    font-size: 13.5px;
    border: none;
    border-radius: 0px;
    gridline-color: transparent;
    selection-background-color: rgba(255, 244, 227, 30);
    selection-color: {TEXT_PRIMARY};
    outline: none;
}}
QTableView::item {{
    padding: 8px 10px;
    color: {TEXT_PRIMARY};
    font-size: 13.5px;
    border: none;
    border-bottom: none;
}}
QTableView::item:hover {{
    background-color: {GLASS_HOVER};
}}
QTableView::item:selected {{
    background-color: rgba(255, 244, 227, 36);
}}
QHeaderView {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {RULE};
    border-radius: 0px;
}}
QHeaderView::section {{
    background-color: transparent;
    color: {TEXT_MUTED};
    padding: 6px 10px 7px 10px;
    border: none;
    border-right: none;
    border-bottom: 1px solid {RULE};
    font-size: 12px;
    letter-spacing: 1px;
}}
QHeaderView::section:hover {{
    color: {TEXT_PRIMARY};
}}
QTableCornerButton::section {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(214, 188, 154, 40); border-radius: 2px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(214, 188, 154, 85); }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 8px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: rgba(214, 188, 154, 40); border-radius: 2px; min-width: 30px;
}}

QLabel#sectionTitle {{
    font-family: {DISPLAY_FAMILY};
    font-size: 26px;
    color: {TEXT_PRIMARY};
    letter-spacing: 0.5px;
}}
QLabel#muted {{ color: {TEXT_MUTED}; font-size: 12.5px; }}
QLabel#tiny  {{ color: {TEXT_MUTED}; font-size: 12px; }}

QFrame#statusPanel {{
    background: transparent;
    border: none;
    border-top: 1px solid {RULE};
    border-radius: 0px;
}}

QProgressBar {{
    background-color: rgba(214, 188, 154, 25);
    border: none;
    border-radius: 2px;
    text-align: center;
    color: {TEXT_SECONDARY};
    max-height: 4px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 2px;
}}
"""
