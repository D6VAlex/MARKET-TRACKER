from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from typing import Any

from market_tracker.config import CONFIG, APP_NAME, APP_VERSION

_CONFIGURED = False

_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)-28s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _ExtraFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        extras: list[str] = []
        for key in ("category", "provider", "attempt", "state", "status"):
            val = getattr(record, key, None)
            if val is not None and val != "":
                extras.append(f"{key}={val}")
        if extras:
            msg = f"{msg} | {' '.join(extras)}"
        return msg


def setup_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger("market_tracker")
    root.setLevel(getattr(logging, (level or CONFIG.log_level).upper(), logging.INFO))
    root.propagate = False

    formatter = _ExtraFormatter(_FORMAT, _DATEFMT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            CONFIG.log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass

    root.info("=== %s v%s starting ===", APP_NAME, APP_VERSION)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"market_tracker.{name}")


class ConnectionTracker:

    def __init__(self) -> None:
        self.online: bool | None = None
        self.last_change: float = time.time()
        self.consecutive_failures = 0

    def record_success(self) -> None:
        changed = self.online is not True
        self.online = True
        self.consecutive_failures = 0
        if changed:
            self.last_change = time.time()

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.online is not False:
            self.online = False
            self.last_change = time.time()

    @property
    def state_text(self) -> str:
        if self.online is None:
            return "UNKNOWN"
        return "ONLINE" if self.online else "OFFLINE"

    def as_dict(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "state_text": self.state_text,
            "consecutive_failures": self.consecutive_failures,
            "since": self.last_change,
        }
