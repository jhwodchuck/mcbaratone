import threading
import time
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..transport.transport import Transport


class Ticker:
    """Lightweight poller that mirrors tick/update events from Baritone."""

    def __init__(self, transport: "Transport", interval: float = 0.1) -> None:
        self.transport = transport
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._callback: Optional[Callable[[], None]] = None

    def start(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if self._callback:
                self._callback()
            time.sleep(self.interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval * 5)
