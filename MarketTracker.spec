# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Market Tracker.
# Build:  pyinstaller --clean MarketTracker.spec   (from market_tracker/)
import sys
from pathlib import Path

block_cipher = None
here = Path(SPECPATH)

hidden = [
    "market_tracker",
    "market_tracker.core.application",
    "market_tracker.core.data_manager",
    "market_tracker.core.scheduler",
    "market_tracker.core.event_manager",
    "market_tracker.providers.base_provider",
    "market_tracker.providers.crypto_providers",
    "market_tracker.providers.forex_providers",
    "market_tracker.providers.discovery",
    "market_tracker.providers.websocket_manager",
    "market_tracker.infrastructure.cache",
    "market_tracker.infrastructure.database",
    "market_tracker.infrastructure.connection_manager",
    "market_tracker.infrastructure.retry_manager",
    "market_tracker.infrastructure.circuit_breaker",
    "market_tracker.infrastructure.health_monitor",
    "market_tracker.services.crypto_service",
    "market_tracker.services.forex_service",
    "market_tracker.services.stocks_service",
    "market_tracker.services.validation_service",
    "market_tracker.services.price_aggregator",
    "market_tracker.providers.stock_providers",
    "market_tracker.providers.google_finance",
    "market_tracker.ui.all_markets_page",
    "market_tracker.ui.market_window",
    "market_tracker.ui.main_window",
    "market_tracker.ui.market_page",
    "market_tracker.ui.components",
    "market_tracker.ui.theme",
    "market_tracker.utils.logger",
    "market_tracker.utils.exceptions",
    "market_tracker.utils.validators",
]

a = Analysis(
    [str(here / "main.py")],
    pathex=[str(here), str(here.parent)],
    binaries=[],
    datas=[(str(here / "assets"), "assets")],   # background image etc.
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # slim the bundle: unused Qt modules
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtSensors",
        "tkinter",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MarketTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed app; set True to see logs on stdout
    icon=str(here / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="MarketTracker",
)
