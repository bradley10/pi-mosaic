from __future__ import annotations

import datetime
import logging
import math
import os
import socket
import subprocess
import time
from typing import Optional, Protocol

import numpy as np

from controller.color import Color, lerp_color, pulse
from controller.data import (
    PixelDisplay,
    _font_by_key,
    dimensions,
    draw_lines_on,
    word_width,
)
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

# Palette: simple, elegant, muted
COLOR_STATUS_CONNECTING: Color = (175, 185, 200)  # soft pearl slate
COLOR_SSID_CONNECTING: Color = (120, 185, 220)  # calm ice-cyan
COLOR_FALLBACK_CONNECTING: Color = (130, 145, 165)  # muted slate
ICON_BREATHE_DIM: Color = (45, 95, 125)  # deep subtle cyan
ICON_BREATHE_BRIGHT: Color = (115, 205, 245)  # soft glowing cyan

COLOR_STATUS_CONNECTED: Color = (80, 215, 140)  # soft mint / sage
COLOR_SSID_CONNECTED: Color = (160, 215, 190)  # soft sage
ICON_CONNECTED: Color = (80, 215, 140)  # solid serene mint

CONNECTED_HOLD_DURATION = 1.2  # seconds to display 'connected' before rotation

# 7 wide by 5 high Wi-Fi icon centered at row 4
ICON_ROW = 4
ICON_WIDTH = 7
ICON_COL = (dimensions.width - ICON_WIDTH) // 2  # 28

OUTER_ARC = [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (1, 0), (1, 6)]
INNER_ARC = [(2, 2), (2, 3), (2, 4)]
CENTER_DOT = [(4, 3)]
ALL_ICON_PIXELS = OUTER_ARC + INNER_ARC + CENTER_DOT


def get_current_ssid() -> Optional[str]:
    """Return the active Wi-Fi SSID if connected/associated, or None."""
    # 1. Raspberry Pi / Linux: check iwgetid
    for iwgetid_path in ("/sbin/iwgetid", "/usr/sbin/iwgetid", "iwgetid"):
        try:
            res = subprocess.run(
                [iwgetid_path, "-r"],
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            ssid = res.stdout.strip()
            if res.returncode == 0 and ssid:
                return ssid
        except Exception:
            pass

    # 2. Raspberry Pi / Linux: wpa_cli status
    for wpa_cli_path in ("/sbin/wpa_cli", "/usr/sbin/wpa_cli", "wpa_cli"):
        try:
            res = subprocess.run(
                [wpa_cli_path, "-i", "wlan0", "status"],
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.startswith("ssid="):
                        ssid = line.split("=", 1)[1].strip()
                        if ssid:
                            return ssid
        except Exception:
            pass

    # 3. macOS fallback for local development / simulator
    try:
        res = subprocess.run(
            ["ipconfig", "getsummary", "en0"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("SSID :"):
                    ssid = stripped.split(":", 1)[1].strip()
                    if ssid:
                        return ssid
    except Exception:
        pass

    return None


def sanitize_ssid(ssid: str) -> str:
    """Ensure all characters in the SSID exist in the bitmap font."""
    return "".join(c if c in _font_by_key else "?" for c in ssid)


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

    Displays an elegant minimalist Wi-Fi icon, connection status, and
    the active network SSID in refined lowercase typography.
    """

    _BG = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

    def __init__(
        self,
        clock: Optional[HasWeather] = None,
        hold_duration: float = CONNECTED_HOLD_DURATION,
        ssid: Optional[str] = None,
    ):
        self._clock = clock
        self._hold_duration = hold_duration
        self._ssid: Optional[str] = sanitize_ssid(ssid) if ssid else None
        self._ready = False
        self._started = time.monotonic()
        self._synced_at: Optional[float] = None
        self._connected_at: Optional[float] = None
        self._pixels = self._render(0.0)

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    @property
    def ready(self) -> bool:
        """True once the rotation is safe to show. Latches: a wifi blip later
        on must not throw the board back to this screen."""
        return self._ready

    @property
    def connected(self) -> bool:
        """True once network and weather are ready, during the hold confirmation."""
        return self._connected_at is not None

    def start(self) -> None:
        spawn_daemon(self._watch_loop)
        spawn_daemon(self._render_loop)

    def _watch_loop(self) -> None:
        run_periodically(self._check, CHECK_INTERVAL)

    def _check(self) -> None:
        if self._ready:
            return

        # Poll SSID if not yet discovered
        if self._ssid is None:
            raw_ssid = get_current_ssid()
            if raw_ssid:
                self._ssid = sanitize_ssid(raw_ssid)

        time_synced = clock_is_synchronized()
        if time_synced and self._synced_at is None:
            self._synced_at = time.monotonic()

        # Check weather readiness if clock is provided and weather is enabled
        weather_needed = self._clock is not None and settings.get("clock.show_weather")
        weather_ready = (
            self._clock.has_weather if weather_needed and self._clock else True
        )

        now = time.monotonic()
        if time_synced and weather_ready:
            if self._connected_at is None:
                self._connected_at = now
                logger.info(
                    "Clock synchronized and initial data ready; holding "
                    f"connected screen for {self._hold_duration:.1f}s"
                )
            if now - self._connected_at >= self._hold_duration:
                self._ready = True
        elif now - self._started > GIVE_UP_AFTER:
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
        now = time.monotonic()
        if (
            self._connected_at is not None
            and (now - self._connected_at) >= self._hold_duration
        ):
            self._ready = True
            return
        self._pixels = self._render(now)

    def _draw_line2(
        self, pixels: PixelDisplay, text: str, color: Color, t: float
    ) -> None:
        w = word_width(text)
        if w <= 60:
            draw_lines_on(pixels, [f"-center-{text}"], color, vertical_shift=22)
        else:
            # Smooth cosine ping-pong pan for SSIDs wider than display margins
            max_travel = w - (dimensions.width - 4)
            cycle = (t % 4.0) / 4.0
            progress = (1.0 - math.cos(cycle * 2.0 * math.pi)) / 2.0
            col_start = 2 - int(round(progress * max_travel))
            draw_lines_on(
                pixels,
                [text],
                color,
                horizontal_shift=col_start,
                vertical_shift=22,
            )

    def _render(self, t: float) -> PixelDisplay:
        pixels = self._BG.copy()
        is_connected = self._connected_at is not None

        if is_connected:
            icon_color = ICON_CONNECTED
            status_text = "-center-connected"
            status_color = COLOR_STATUS_CONNECTED
            line2_color = COLOR_SSID_CONNECTED
            line2_text = self._ssid if self._ssid else "to network"
        else:
            breathe = pulse(t, period=1.8)
            icon_color = lerp_color(ICON_BREATHE_DIM, ICON_BREATHE_BRIGHT, breathe)
            status_text = "-center-connecting"
            status_color = COLOR_STATUS_CONNECTING
            line2_color = (
                COLOR_SSID_CONNECTING if self._ssid else COLOR_FALLBACK_CONNECTING
            )
            line2_text = self._ssid if self._ssid else "to wifi"

        # 1. Draw Wi-Fi icon
        for dr, dc in ALL_ICON_PIXELS:
            pixels[ICON_ROW + dr, ICON_COL + dc] = icon_color

        # 2. Draw status line (Line 1 at row 12)
        draw_lines_on(pixels, [status_text], status_color, vertical_shift=12)

        # 3. Draw SSID line (Line 2 at row 22)
        self._draw_line2(pixels, line2_text, line2_color, t)

        return pixels
