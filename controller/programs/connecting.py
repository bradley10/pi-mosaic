from __future__ import annotations

import datetime
import logging
import os
import socket
import time
from typing import Optional, Protocol

import numpy as np

from controller.data import PixelDisplay, dimensions, draw_lines_on
from controller.settings import settings
from controller.timing import run_periodically, spawn_daemon


class HasWeather(Protocol):
    @property
    def has_weather(self) -> bool: ...


logger = logging.getLogger(__name__)

# systemd-timesyncd creates this the moment it lands its first sync. It's the
# signal we actually care about: the Pi has no RTC, so until it appears the
# wall clock is whatever fake-hwclock restored from the last shutdown and the
# Clock page would confidently show a wrong time.
TIMESYNC_FLAG = "/run/systemd/timesync/synchronized"

# Fallback for a box that isn't running timesyncd at all (a dev machine, or a
# Pi using chrony). Reaching a DNS server means the network is up; it doesn't
# prove the clock is right, which is why the flag file is checked first.
PROBE_HOST = "1.1.1.1"
PROBE_PORT = 53
PROBE_TIMEOUT = 1.0

# How long to hold the board before giving up and showing the rotation
# anyway. A Pi whose wifi is down would otherwise sit on this screen forever,
# and a slightly wrong clock beats a board that never shows anything.
GIVE_UP_AFTER = 90.0

CHECK_INTERVAL = 0.5
FRAME_INTERVAL = 1 / 20

TEXT_COLOR = (150, 170, 190)
DOT_COLOR = (0, 190, 210)
# Three dots, 2px each with a 3px gap, centred under the word.
DOT_COUNT = 3
DOT_SIZE = 2
DOT_GAP = 3
DOT_ROW = 21


def clock_is_synchronized() -> bool:
    """Whether the system clock can be trusted.

    The flag file is authoritative when timesyncd owns the clock. Everywhere
    else, fall back to "can we open a socket at all" so this doesn't block
    forever on a machine that syncs time some other way.
    """
    if datetime.datetime.now().year < 2025:
        return False

    if os.path.exists(TIMESYNC_FLAG):
        return True

    # If systemd-timesyncd is active on this system, the lack of the synchronized
    # flag means NTP sync has NOT finished yet. Do not bypass timesyncd with a
    # socket probe while it is active and in progress.
    if os.path.isdir("/run/systemd/timesync"):
        return False

    # On a systemd system without the timesyncd directory, check timedatectl status
    if os.path.isdir("/run/systemd/system"):
        try:
            import subprocess

            res = subprocess.run(
                ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            if res.returncode == 0 and res.stdout.strip() == "yes":
                return True
        except Exception:
            pass
        return False

    try:
        socket.create_connection((PROBE_HOST, PROBE_PORT), PROBE_TIMEOUT).close()
    except OSError:
        return False
    return True


class Connecting:
    """Holds the board while the Pi has no network and no synchronized clock.

    The controller starts well before DHCP finishes - see the note in
    `deploy/pi-mosaic.service` - which is what makes the board light up
    seconds after power-on instead of most of a minute. The cost is that for
    those first seconds the wall clock is wrong, so rather than page straight
    into the rotation, `Controller` shows this until `ready` goes true.
    """

    _BG = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

    def __init__(self, clock: Optional[HasWeather] = None):
        self._clock = clock
        self._pixels = self._render(0)
        self._ready = False
        self._frame = 0
        self._started = time.monotonic()
        self._synced_at: Optional[float] = None

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    @property
    def ready(self) -> bool:
        """True once the rotation is safe to show. Latches: a wifi blip later
        on must not throw the board back to this screen."""
        return self._ready

    def start(self) -> None:
        spawn_daemon(self._watch_loop)
        spawn_daemon(self._render_loop)

    def _watch_loop(self) -> None:
        # Deliberately not gated on `render_gate`: this has to keep checking
        # whether or not anything is looking at the screen.
        run_periodically(self._check, CHECK_INTERVAL)

    def _check(self) -> None:
        if self._ready:
            return

        time_synced = clock_is_synchronized()
        if time_synced and self._synced_at is None:
            self._synced_at = time.monotonic()

        # Check weather readiness if clock is provided and weather is enabled
        weather_needed = (
            self._clock is not None and settings.get("clock.show_weather")
        )
        weather_ready = (
            self._clock.has_weather if weather_needed and self._clock else True
        )

        if time_synced and weather_ready:
            logger.info("Clock synchronized and initial data ready; starting rotation")
            self._ready = True
        elif time.monotonic() - self._started > GIVE_UP_AFTER:
            logger.warning(
                f"No clock/data sync after {GIVE_UP_AFTER:.0f}s; "
                "starting the rotation anyway"
            )
            self._ready = True

    def _render_loop(self) -> None:
        run_periodically(self._step, FRAME_INTERVAL)

    def _step(self) -> None:
        if self._ready:
            return
        self._frame += 1
        self._pixels = self._render(self._frame)

    def _render(self, frame: int) -> PixelDisplay:
        pixels = self._BG.copy()
        draw_lines_on(pixels, ["-center-CONNECTING"], TEXT_COLOR, vertical_shift=9)

        # One dot lights per ~0.4s, cycling - the usual "still working" tell.
        lit = (frame // 8) % (DOT_COUNT + 1)
        span = DOT_COUNT * DOT_SIZE + (DOT_COUNT - 1) * DOT_GAP
        left = (dimensions.width - span) // 2
        for index in range(lit):
            x = left + index * (DOT_SIZE + DOT_GAP)
            pixels[DOT_ROW : DOT_ROW + DOT_SIZE, x : x + DOT_SIZE] = DOT_COLOR
        return pixels
