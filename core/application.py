from __future__ import annotations

import asyncio
import signal
import sys
import threading

from market_tracker.config import APP_NAME, APP_VERSION
from market_tracker.core.data_manager import DataManager
from market_tracker.core.event_manager import EventManager
from market_tracker.utils.logger import get_logger, setup_logging

log = get_logger("app")


class Application:
    def __init__(self) -> None:
        setup_logging()
        self.events = EventManager()
        self.data_manager = DataManager(self.events)
        self.events.data_manager = self.data_manager
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    def start_pipeline(self) -> bool:
        if self._thread is not None:
            return True

        def _run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self.events.attach_loop(self._loop)
            try:
                self._loop.run_until_complete(self._pipeline_main())
            except Exception:
                log.exception("pipeline thread crashed")
            finally:
                try:
                    tasks = asyncio.all_tasks(self._loop)
                    for t in tasks:
                        t.cancel()
                    self._loop.run_until_complete(
                        asyncio.gather(*tasks, return_exceptions=True))
                    self._loop.close()
                except Exception:
                    pass

        self._thread = threading.Thread(target=_run, name="pipeline",
                                        daemon=True)
        self._thread.start()
        self._started.wait(timeout=15)
        return self._started.is_set()

    async def _pipeline_main(self) -> None:
        self._started.set()
        try:
            await self.data_manager.start()
            stop = asyncio.Event()
            self._stop_event = stop
            await stop.wait()
        finally:
            await self.data_manager.stop()

    def request_shutdown(self) -> None:
        log.info("shutdown requested")
        if hasattr(self, "_stop_event"):
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def join_pipeline(self, timeout: float = 10.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)


def main() -> int:
    app = Application()
    if not app.start_pipeline():
        log.error("pipeline failed to start")

    from market_tracker.ui.main_window import MainWindow

    from PySide6.QtWidgets import QApplication
    qapp = QApplication(sys.argv)
    from PySide6.QtWidgets import QStyleFactory
    qapp.setStyle(QStyleFactory.create("Fusion"))
    qapp.setApplicationName(APP_NAME)
    qapp.setApplicationVersion(APP_VERSION)
    from PySide6.QtGui import QIcon
    from market_tracker.config import PACKAGE_DIR
    icon_path = PACKAGE_DIR / "assets" / "icon.ico"
    if icon_path.exists():
        qapp.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow(app.events, on_close=app.request_shutdown)
    window.show()

    signal.signal(signal.SIGINT, lambda *_: (
        app.request_shutdown(), qapp.quit()))

    exit_code = qapp.exec()
    app.request_shutdown()
    app.join_pipeline()
    log.info("=== %s exited (code %d) ===", APP_NAME, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
