from __future__ import annotations

from typing import Tuple

import numpy as np

from controller.data import PixelDisplay, dimensions
from controller.settings import settings
from controller.timing import run_periodically, spawn_daemon

Color = Tuple[int, int, int]

_OCTAVES = 4

# `terrain.scroll_speed` is columns-per-second scaled by this, chosen so the
# default 1.2 keeps roughly the speed the old fractional scroll produced.
_COLUMNS_PER_SPEED_UNIT = 12.0
# Floor on the tick interval. The main loop samples at 60Hz, so columns
# produced faster than this can't be seen anyway - they'd just burn Pi.
_MIN_INTERVAL = 1 / 60
# Speed 0 means paused. Still tick occasionally so that un-pausing is picked
# up promptly, but don't shift.
_PAUSED_INTERVAL = 0.5

_DEEP_WATER: Color = (15, 40, 100)
_WATER: Color = (30, 100, 180)
_SHALLOW_WATER: Color = (80, 150, 200)
_SAND: Color = (210, 190, 120)
_GRASS: Color = (60, 160, 70)
_FOREST: Color = (35, 100, 45)
_HILLS: Color = (150, 120, 80)
_MOUNTAINS: Color = (130, 130, 140)
_SNOW: Color = (240, 245, 250)


def _hash2d(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """2D hash function for Perlin noise."""
    n = np.sin(x * 12.9898 + y * 78.233) * 43758.5453
    return n - np.floor(n)


def _noise2d(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """2D Perlin noise."""
    xi = np.floor(x).astype(int)
    yi = np.floor(y).astype(int)
    xf = x - xi
    yf = y - yi

    n00 = _hash2d(xi, yi)
    n10 = _hash2d(xi + 1, yi)
    n01 = _hash2d(xi, yi + 1)
    n11 = _hash2d(xi + 1, yi + 1)

    smooth_x = xf * xf * (3.0 - 2.0 * xf)
    smooth_y = yf * yf * (3.0 - 2.0 * yf)

    nx0 = n00 + (n10 - n00) * smooth_x
    nx1 = n01 + (n11 - n01) * smooth_x
    return nx0 + (nx1 - nx0) * smooth_y


def _fbm2d(x: np.ndarray, y: np.ndarray, octaves: int = _OCTAVES) -> np.ndarray:
    """Fractal Brownian Motion in 2D."""
    value = np.zeros_like(x)
    amplitude = 1.0
    frequency = 1.0
    max_value = 0.0
    for _ in range(octaves):
        value += amplitude * _noise2d(x * frequency, y * frequency)
        max_value += amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return value / max_value


# Height [0, 1] -> terrain color, Google Maps style top-down. Expressed as a
# threshold/palette pair rather than an if-chain so a whole frame can be
# colored with one `searchsorted` + fancy-index instead of a 2048-iteration
# Python loop over every pixel - see `_render`.
_BANDS = (
    (0.20, _DEEP_WATER),
    (0.35, _WATER),
    (0.45, _SHALLOW_WATER),
    (0.50, _SAND),
    (0.60, _GRASS),
    (0.70, _FOREST),
    (0.80, _HILLS),
    (0.90, _MOUNTAINS),
)
_THRESHOLDS = np.array([threshold for threshold, _ in _BANDS])
_PALETTE = np.array([color for _, color in _BANDS] + [_SNOW], dtype=np.int32)


class PerlinTerrain:
    """A scrolling top-down landscape.

    The terrain is kept as a buffer of heights, one column per pixel, and
    scrolled by *moving* those columns one pixel left and generating a single
    new column on the right. Nothing already on screen is ever re-sampled.

    That distinction is the whole point. This used to render every frame by
    evaluating the noise field across the full width at `x/zoom + scroll`,
    where `scroll` advanced by a fractional amount per frame. Every pixel was
    therefore resampled at a slightly different point each time, and because
    heights are quantized into nine colour bands, neighbouring pixels crossed
    their band edges at different moments - so the landscape shimmered and
    appeared to warp and pulse instead of simply moving. Shifting whole
    columns makes the motion exactly one pixel per step. It's cheaper too -
    one column of noise per frame instead of the full width, which measures
    about 2.3x less work per frame overall once the roll and the colour
    lookup are counted.
    """

    def __init__(self):
        self._zoom = None
        self._octaves = None
        self._next_x = 0
        self._heights = None
        self._regenerate()
        self._pixels = self._colorize()

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self) -> None:
        spawn_daemon(self._main_loop)

    def _main_loop(self) -> None:
        # A callable interval, so the scroll speed slider takes effect on the
        # next column rather than the next restart. Speed is expressed as a
        # tick rate because each tick is exactly one pixel of movement.
        run_periodically(self._step, self._interval, owner=self)

    def _interval(self) -> float:
        columns_per_second = (
            settings.get("terrain.scroll_speed") * _COLUMNS_PER_SPEED_UNIT
        )
        if columns_per_second <= 0:
            return _PAUSED_INTERVAL
        return max(_MIN_INTERVAL, 1.0 / columns_per_second)

    def _step(self) -> None:
        zoom = settings.get("terrain.zoom")
        octaves = settings.get("terrain.octaves")

        if zoom != self._zoom or octaves != self._octaves:
            # These change the shape of the noise itself, so the columns
            # already in the buffer are no longer consistent with the ones
            # we'd generate next. Rebuild the lot rather than scroll a seam
            # across the screen.
            self._regenerate(zoom, octaves)
        elif settings.get("terrain.scroll_speed") > 0:
            self._shift()
        else:
            # Paused, and nothing about the terrain changed - leave the frame
            # object alone so the main loop doesn't re-push an identical one.
            return

        self._pixels = self._colorize()

    def _column_heights(self, start_x: int, count: int) -> np.ndarray:
        """Noise for `count` columns starting at world column `start_x`.

        World column `i` always samples the field at `i / zoom`, so a column
        holds the same terrain no matter which screen position it occupies -
        that's what makes the scroll a pure translation.
        """
        xs = (start_x + np.arange(count)) / self._zoom
        ys = np.arange(dimensions.height) / self._zoom
        xx, yy = np.meshgrid(xs, ys)
        return np.clip(_fbm2d(xx, yy, octaves=self._octaves), 0.0, 1.0)

    def _regenerate(self, zoom: float = None, octaves: int = None) -> None:
        self._zoom = settings.get("terrain.zoom") if zoom is None else zoom
        self._octaves = settings.get("terrain.octaves") if octaves is None else octaves
        self._heights = self._column_heights(self._next_x, dimensions.width)
        # Carry on past the stretch just drawn, so a rebuild doesn't repeat
        # the terrain that was already on screen.
        self._next_x += dimensions.width

    def _shift(self) -> None:
        """Move everything one pixel left and generate the new right column."""
        # `np.roll` rather than an overlapping slice assignment, which numpy
        # doesn't define cleanly.
        self._heights = np.roll(self._heights, -1, axis=1)
        self._heights[:, -1:] = self._column_heights(self._next_x, 1)
        self._next_x += 1

    def _colorize(self) -> PixelDisplay:
        # `side="right"` reproduces the original `height < threshold` chain
        # exactly: a height equal to a threshold falls into the band above it.
        return _PALETTE[np.searchsorted(_THRESHOLDS, self._heights, side="right")]
