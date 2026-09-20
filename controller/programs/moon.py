from __future__ import annotations

import datetime
import time

import numpy as np

from controller.data import PixelDisplay, dimensions, draw_lines_on
from controller.settings import settings
from controller.sprites import (
    MOON_PHASE_NAMES,
    MOON_PHASES,
    draw_sprite_on,
    draw_starfield,
)
from controller.timing import run_periodically, spawn_daemon

NIGHT_BG = (0, 0, 0)

_REFERENCE_NEW_MOON = datetime.datetime(
    2000, 1, 6, 18, 14, tzinfo=datetime.timezone.utc
)
_SYNODIC_MONTH_DAYS = 29.53058867


def _phase_fraction(now: datetime.datetime) -> float:
    """Fraction of the current lunar cycle elapsed: 0=new, 0.5=full."""
    days = (now - _REFERENCE_NEW_MOON).total_seconds() / 86400.0
    return (days % _SYNODIC_MONTH_DAYS) / _SYNODIC_MONTH_DAYS


class Moon:
    def __init__(self):
        self._pixels = np.full(
            (dimensions.height, dimensions.width, 3), NIGHT_BG, dtype=np.int32
        )
        self._start_time = time.monotonic()

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self):
        spawn_daemon(self._main_loop)

    def _main_loop(self):
        run_periodically(self._step, interval=0.1, owner=self)

    def _step(self) -> None:
        self._pixels = self._moon_pixels()

    def _moon_pixels(self) -> PixelDisplay:
        t = time.monotonic() - self._start_time
        pixels = np.full(
            (dimensions.height, dimensions.width, 3), NIGHT_BG, dtype=np.int32
        )

        phase = _phase_fraction(datetime.datetime.now(datetime.timezone.utc))
        idx = round(phase * 8) % 8

        sprite = MOON_PHASES[idx]
        row_start = 1
        col_start = (dimensions.width - sprite.width_px) // 2

        # Keep stars from ever landing right on/next to the moon, which
        # looks odd (like they're stuck to it) especially near new moon.
        moon_center = (
            row_start + sprite.height_px // 2,
            col_start + sprite.width_px // 2,
        )
        draw_starfield(
            pixels,
            t * settings.get("moon.twinkle_speed"),
            exclude_center=moon_center,
            exclude_radius=sprite.width_px / 2 + 2,
            count=settings.get("moon.stars"),
        )

        draw_sprite_on(pixels, sprite, row_start=row_start, col_start=col_start)

        draw_lines_on(pixels, [f"-center-{MOON_PHASE_NAMES[idx]}"], vertical_shift=22)
        return pixels
