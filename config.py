from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Market Tracker"
APP_VERSION = "1.0.0"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PACKAGE_DIR = _base_dir()
ASSETS_DIR = PACKAGE_DIR / "assets"


def _data_dir() -> Path:
    env = os.environ.get("MT_DATA_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "MarketTracker" / "data"
        return Path.home() / ".local" / "share" / "MarketTracker"
    return PACKAGE_DIR / "data"


DATA_DIR = _data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("MT_DB_PATH", DATA_DIR / "market_tracker.db"))

CRYPTO_TOP_N = int(os.environ.get("MT_CRYPTO_TOP_N", "25"))

CRYPTO_FALLBACK_IDS = [
    "bitcoin", "ethereum", "tether", "bnb", "solana", "xrp",
    "usd-coin", "dogecoin", "cardano", "tron",
]


@dataclass
class ProviderKeys:
    coingecko_api_key: str = os.environ.get("MT_COINGECKO_KEY", "")
    twelvedata_api_key: str = os.environ.get("MT_TWELVEDATA_KEY", "")


@dataclass
class NetworkConfig:
    request_timeout: float = 8.0
    connect_timeout: float = 5.0
    ws_ping_interval: float = 20.0
    ws_ping_timeout: float = 10.0
    pool_size: int = 20
    max_retries: int = 3
    backoff_base: float = 0.4
    backoff_cap: float = 6.0
    jitter_ratio: float = 0.3


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 4
    recovery_timeout: float = 30.0
    half_open_successes: int = 2


@dataclass
class ValidationConfig:
    max_price_age_seconds: float = 120.0
    outlier_pct_change: float = 25.0
    cross_source_tolerance_pct: float = 2.0
    max_bid_ask_spread_pct: float = 5.0
    min_price: float = 1e-12
    max_price: float = 1e15


@dataclass
class SchedulerConfig:
    ui_refresh_interval: float = 1.0
    crypto_rest_interval: float = 30.0
    forex_rest_interval: float = 30.0
    stocks_rest_interval: float = 20.0
    aggregator_interval: float = 1.0
    stale_after_seconds: float = 45.0
    dead_after_seconds: float = 180.0


FOREX_PAIRS: list[tuple[str, str]] = [
    ("EUR", "USD"), ("GBP", "USD"), ("USD", "JPY"), ("USD", "CHF"),
    ("USD", "CAD"), ("AUD", "USD"), ("NZD", "USD"), ("USD", "CNY"),
    ("USD", "JOD"),
    ("EUR", "GBP"), ("EUR", "JPY"), ("EUR", "CHF"), ("GBP", "JPY"),
    ("AUD", "JPY"), ("CHF", "JPY"), ("CAD", "JPY"), ("EUR", "AUD"),
    ("USD", "SGD"), ("USD", "HKD"), ("USD", "TRY"), ("USD", "MXN"),
    ("USD", "SAR"), ("USD", "AED"), ("USD", "EGP"), ("USD", "KRW"),
    ("USD", "INR"), ("USD", "RUB"), ("USD", "BRL"), ("USD", "ZAR"),
    ("USD", "SEK"), ("USD", "NOK"), ("USD", "DKK"), ("USD", "PLN"),
    ("USD", "CZK"), ("USD", "HUF"), ("EUR", "TRY"), ("EUR", "SEK"),
]

FOREX_PIP_DECIMALS = {"JPY": 3, "CNY": 4, "JOD": 5, "KRW": 2, "HUF": 2,
                      "CZK": 3, "RUB": 3, "EGP": 3, "INR": 3, "TRY": 3}

CURRENCY_NAMES: dict[str, str] = {
    "USD": "US Dollar", "EUR": "Euro", "GBP": "British Pound",
    "JPY": "Japanese Yen", "CHF": "Swiss Franc", "CAD": "Canadian Dollar",
    "AUD": "Australian Dollar", "NZD": "New Zealand Dollar",
    "CNY": "Chinese Yuan", "JOD": "Jordanian Dinar", "SGD": "Singapore Dollar",
    "HKD": "Hong Kong Dollar", "TRY": "Turkish Lira", "MXN": "Mexican Peso",
    "SAR": "Saudi Riyal", "AED": "UAE Dirham", "EGP": "Egyptian Pound",
    "KRW": "South Korean Won", "INR": "Indian Rupee", "RUB": "Russian Ruble",
    "BRL": "Brazilian Real", "ZAR": "South African Rand", "SEK": "Swedish Krona",
    "NOK": "Norwegian Krone", "DKK": "Danish Krone", "PLN": "Polish Zloty",
    "CZK": "Czech Koruna", "HUF": "Hungarian Forint",
}

STOCKS_BOARD: list[dict] = [
    dict(symbol="AAPL", name="Apple Inc.", exchange="NMS",
         currency="USD", sector="Technology"),
    dict(symbol="MSFT", name="Microsoft Corporation", exchange="NMS",
         currency="USD", sector="Technology"),
    dict(symbol="GOOGL", name="Alphabet Inc.", exchange="NMS",
         currency="USD", sector="Technology"),
    dict(symbol="AMZN", name="Amazon.com, Inc.", exchange="NMS",
         currency="USD", sector="Consumer"),
    dict(symbol="NVDA", name="NVIDIA Corporation", exchange="NMS",
         currency="USD", sector="Semiconductors"),
    dict(symbol="META", name="Meta Platforms, Inc.", exchange="NMS",
         currency="USD", sector="Technology"),
    dict(symbol="TSLA", name="Tesla, Inc.", exchange="NMS",
         currency="USD", sector="Automotive"),
    dict(symbol="BRK-B", name="Berkshire Hathaway", exchange="NYQ",
         currency="USD", sector="Financials"),
    dict(symbol="JPM", name="JPMorgan Chase & Co.", exchange="NYQ",
         currency="USD", sector="Financials"),
    dict(symbol="V", name="Visa Inc.", exchange="NYQ",
         currency="USD", sector="Financials"),
    dict(symbol="XOM", name="Exxon Mobil Corporation", exchange="NYQ",
         currency="USD", sector="Energy"),
    dict(symbol="KO", name="The Coca-Cola Company", exchange="NYQ",
         currency="USD", sector="Consumer"),
    dict(symbol="WMT", name="Walmart Inc.", exchange="NYQ",
         currency="USD", sector="Consumer"),
    dict(symbol="JNJ", name="Johnson & Johnson", exchange="NYQ",
         currency="USD", sector="Healthcare"),
    dict(symbol="DIS", name="The Walt Disney Company", exchange="NYQ",
         currency="USD", sector="Media"),
    dict(symbol="0700.HK", name="Tencent Holdings", exchange="HKG",
         currency="HKD", sector="Technology"),
    dict(symbol="9988.HK", name="Alibaba Group", exchange="HKG",
         currency="HKD", sector="Consumer"),
    dict(symbol="7203.T", name="Toyota Motor Corp.", exchange="JPX",
         currency="JPY", sector="Automotive"),
    dict(symbol="6758.T", name="Sony Group Corp.", exchange="JPX",
         currency="JPY", sector="Consumer"),
    dict(symbol="005930.KS", name="Samsung Electronics", exchange="KSC",
         currency="KRW", sector="Technology"),
    dict(symbol="ASML.AS", name="ASML Holding", exchange="AMS",
         currency="EUR", sector="Semiconductors"),
    dict(symbol="SAP.DE", name="SAP SE", exchange="GER",
         currency="EUR", sector="Technology"),
    dict(symbol="SIE.DE", name="Siemens AG", exchange="GER",
         currency="EUR", sector="Industrials"),
    dict(symbol="SHEL.L", name="Shell plc", exchange="LSE",
         currency="GBP", sector="Energy"),
    dict(symbol="NESN.SW", name="Nestlé S.A.", exchange="SWX",
         currency="CHF", sector="Consumer"),
]


@dataclass
class AppConfig:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    keys: ProviderKeys = field(default_factory=ProviderKeys)

    log_level: str = os.environ.get("MT_LOG_LEVEL", "INFO")
    log_file: Path = field(default_factory=lambda: DATA_DIR / "market_tracker.log")

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        override = Path(os.environ.get("MT_CONFIG", PACKAGE_DIR / "config.json"))
        if override.exists():
            try:
                data = json.loads(override.read_text())
                for section_name in ("network", "breaker", "validation", "scheduler"):
                    section = data.get(section_name)
                    if isinstance(section, dict):
                        target = getattr(cfg, section_name)
                        for k, v in section.items():
                            if hasattr(target, k):
                                setattr(target, k, v)
                if "log_level" in data:
                    cfg.log_level = data["log_level"]
            except (json.JSONDecodeError, OSError):
                pass
        return cfg


CONFIG = AppConfig.load()
