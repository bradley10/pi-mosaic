import threading
import time
from typing import Callable

from controller.data import Duration


def spawn_daemon(target: Callable[[], None]) -> threading.Thread:
    """Run `target` in a daemon thread and return it, already started."""
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def run_periodically(fn: Callable[[], None], interval: Duration) -> None:
    """Call `fn` forever, sleeping just enough to keep each call ~`interval` apart.

    Does not correct for drift across iterations; if a call to `fn` overruns
    `interval`, the next call starts immediately rather than trying to catch up.
    """
    while True:
        start = time.monotonic()
        fn()
        elapsed = time.monotonic() - start
        time.sleep(max(0.0, interval - elapsed))
