from __future__ import annotations

import time
from typing import Tuple

import numpy as np

from controller.data import PixelDisplay, dimensions
from controller.timing import spawn_daemon

Color = Tuple[int, int, int]

_SCALE = 16.0  # smaller = more hills/valleys visible across the width
_SPEED = 0.55  # noise-units/sec scrolled, i.e. how fast the world goes by
_OCTAVES = 4

# Row bands (0 = top of the display). Land rises toward `_MIN_SURFACE` and
# the sea floor deepens toward `_MAX_SURFACE`; anything below `_SEA_LEVEL` is
# underwater. Kept close to the bottom of the display (rather than spanning
# its full height) so most of a hill's visible cross-section is its thin
# topsoil layer, not the stone underneath - and there's always plenty of
# sky above it.
_SEA_LEVEL = 22
_MIN_SURFACE = 15
_MAX_SURFACE = 28

_SKY_TOP: Color = (70, 140, 220)
_SKY_HORIZON: Color = (185, 225, 248)
_SHALLOW_WATER: Color = (60, 150, 210)
_DEEP_WATER: Color = (10, 35, 110)
_FOAM: Color = (225, 240, 250)
_SAND: Color = (225, 195, 130)
_GRASS: Color = (70, 165, 70)
_FOREST: Color = (35, 110, 50)
_DIRT: Color = (110, 75, 48)
_STONE: Color = (125, 125, 130)
_SNOW: Color = (245, 246, 250)


def _lerp_rows(c1: Color, c2: Color, t: np.ndarray) -> np.ndarray:
    """Lerp between two colors across an array of `t` in [0, 1]; returns an
    (n, 3) int array, one row per `t`."""
    t = np.clip(t, 0.0, 1.0)[:, None]
    return (np.array(c1) + (np.array(c2) - np.array(c1)) * t).astype(np.int32)


def _hash(x: np.ndarray) -> np.ndarray:
    n = np.sin(x * 127.1) * 43758.5453
    return n - np.floor(n)


def _noise_1d(x: np.ndarray) -> np.ndarray:
    xi = np.floor(x)
    xf = x - xi
    n0 = _hash(xi)
    n1 = _hash(xi + 1)
    smooth_t = xf * xf * (3.0 - 2.0 * xf)
    return n0 + (n1 - n0) * smooth_t


def _fbm_1d(x: np.ndarray, octaves: int = _OCTAVES) -> np.ndarray:
    value = np.zeros_like(x)
    amplitude = 1.0
    frequency = 1.0
    max_value = 0.0
    for _ in range(octaves):
        value = value + amplitude * _noise_1d(x * frequency)
        max_value += amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return value / max_value


def _ground_top_color(surface_row: int) -> Color:
    """The surface block color at `surface_row` - lower rows are taller, so
    this shades from snowy peaks down through forest and grass to beach
    sand, then to a sandy/rocky seabed once the ground dips underwater."""
    if surface_row > _SEA_LEVEL:
        return _SAND if surface_row - _SEA_LEVEL <= 3 else _STONE
    if surface_row <= _MIN_SURFACE:
        return _SNOW
    if surface_row <= _MIN_SURFACE + 2:
        return _STONE
    if surface_row <= _SEA_LEVEL - 3:
        return _FOREST
    if surface_row <= _SEA_LEVEL - 1:
        return _GRASS
    return _SAND


def _land_column(depth_below_surface: np.ndarray, top_color: Color) -> np.ndarray:
    """Below-surface fill for one land column: a thin topsoil layer over
    dirt over stone, mountains/beaches staying a uniform material."""
    if top_color == _SAND:
        return np.tile(np.array(_SAND), (len(depth_below_surface), 1))

    if top_color in (_SNOW, _STONE):
        is_cap = depth_below_surface == 0
        return np.where(is_cap[:, None], np.array(top_color), np.array(_STONE))

    is_top = depth_below_surface == 0
    is_dirt = (depth_below_surface > 0) & (depth_below_surface <= 5)
    fill = np.broadcast_to(np.array(_STONE), (len(depth_below_surface), 3)).copy()
    fill[is_dirt] = _DIRT
    fill[is_top] = top_color
    return fill


class PerlinTerrain:
    def __init__(self):
        self._start_time = time.monotonic()
        self._pixels = np.zeros(
            (dimensions.height, dimensions.width, 3), dtype=np.int32
        )

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self) -> None:
        spawn_daemon(self._main_loop)

    def _main_loop(self) -> None:
        while True:
            self._pixels = self._render()
            time.sleep(0.03)

    def _render(self) -> PixelDisplay:
        t = time.monotonic() - self._start_time
        x_coords = np.arange(dimensions.width) / _SCALE + t * _SPEED

        noise = _fbm_1d(x_coords)
        surface = np.clip(
            np.round(_MIN_SURFACE + (1.0 - noise) * (_MAX_SURFACE - _MIN_SURFACE)),
            _MIN_SURFACE,
            _MAX_SURFACE,
        ).astype(np.int32)

        rows = np.arange(dimensions.height)
        pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)
        foam_mix = (np.sin(t * 3.0) + 1.0) / 2.0

        for x in range(dimensions.width):
            surface_row = int(surface[x])
            column = np.empty((dimensions.height, 3), dtype=np.int32)

            # Ground (grass/sand/stone/...) always starts at `surface_row`
            # and runs to the bottom of the display; water, when present,
            # sits on top of it between sea level and the surface - so a
            # dip below sea level floods gradually instead of the column
            # instantly swapping from solid ground to open water.
            sky_end = min(surface_row, _SEA_LEVEL)
            sky_rows = rows < sky_end
            sky_t = rows[sky_rows] / max(1, _SEA_LEVEL - 1)
            column[sky_rows] = _lerp_rows(_SKY_TOP, _SKY_HORIZON, sky_t)

            water_rows = (rows >= _SEA_LEVEL) & (rows < surface_row)
            if np.any(water_rows):
                depth_t = (rows[water_rows] - _SEA_LEVEL) / max(
                    1, _MAX_SURFACE - _SEA_LEVEL
                )
                water = _lerp_rows(_SHALLOW_WATER, _DEEP_WATER, depth_t)
                water[0] = _lerp_rows(_FOAM, _SHALLOW_WATER, np.array([foam_mix]))[0]
                column[water_rows] = water

            ground_rows = rows >= surface_row
            top_color = _ground_top_color(surface_row)
            depth_below = rows[ground_rows] - surface_row
            column[ground_rows] = _land_column(depth_below, top_color)

            pixels[:, x, :] = column

        return pixels
