from __future__ import annotations

import time
from typing import Tuple

import numpy as np

from controller.data import PixelDisplay, dimensions
from controller.timing import spawn_daemon

Color = Tuple[int, int, int]

_SCALE = 12.0
_SPEED = 1.2
_OCTAVES = 4

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


def _terrain_color(height: float) -> Color:
    """Map height value [0, 1] to terrain color (Google Maps style top-down)."""
    if height < 0.2:
        return _DEEP_WATER
    elif height < 0.35:
        return _WATER
    elif height < 0.45:
        return _SHALLOW_WATER
    elif height < 0.5:
        return _SAND
    elif height < 0.6:
        return _GRASS
    elif height < 0.7:
        return _FOREST
    elif height < 0.8:
        return _HILLS
    elif height < 0.9:
        return _MOUNTAINS
    else:
        return _SNOW


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
        """Render top-down view of terrain (Google Maps style)."""
        t = time.monotonic() - self._start_time

        x_coords = np.arange(dimensions.width) / _SCALE + t * _SPEED
        y_coords = np.arange(dimensions.height) / _SCALE

        xx, yy = np.meshgrid(x_coords, y_coords)

        noise = _fbm2d(xx, yy, octaves=4)
        noise = np.clip(noise, 0.0, 1.0)

        pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

        for y in range(dimensions.height):
            for x in range(dimensions.width):
                height = noise[y, x]
                color = _terrain_color(height)
                pixels[y, x] = color

        return pixels
