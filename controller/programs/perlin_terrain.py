from __future__ import annotations

import math
import numpy as np
import time

from controller.data import PixelDisplay, dimensions
from controller.timing import spawn_daemon

_SCALE = 40.0
_SPEED = 0.3
_DETAIL = 2.5


class PerlinTerrain:
    def __init__(self):
        self._start_time = time.monotonic()
        self._pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self):
        spawn_daemon(self._main_loop)

    def _main_loop(self):
        while True:
            self._step()
            self._pixels = self._render()
            time.sleep(0.03)

    def _step(self) -> None:
        pass

    def _interpolate(self, a: float, b: float, t: float) -> float:
        smooth_t = t * t * (3.0 - 2.0 * t)
        return a + (b - a) * smooth_t

    def _perlin_hash(self, x, y):
        n = np.sin(x * 12.9898 + y * 78.233) * 43758.5453
        return n - np.floor(n)

    def _perlin_noise(self, x: float, y: float) -> float:
        xi = int(math.floor(x))
        yi = int(math.floor(y))
        xf = x - xi
        yf = y - yi

        n00 = self._perlin_hash(xi, yi)
        n10 = self._perlin_hash(xi + 1, yi)
        n01 = self._perlin_hash(xi, yi + 1)
        n11 = self._perlin_hash(xi + 1, yi + 1)

        nx0 = self._interpolate(n00, n10, xf)
        nx1 = self._interpolate(n01, n11, xf)
        return self._interpolate(nx0, nx1, yf)

    def _fractal_brownian_motion(self, x: float, y: float, octaves: int = 4) -> float:
        value = 0.0
        amplitude = 1.0
        frequency = 1.0
        max_value = 0.0

        for _ in range(octaves):
            value += amplitude * self._perlin_noise(x * frequency, y * frequency)
            max_value += amplitude
            amplitude *= 0.5
            frequency *= 2.0

        return value / max_value

    def _render(self) -> PixelDisplay:
        pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)
        t = time.monotonic() - self._start_time

        x_coords = np.arange(dimensions.width) / _SCALE + t * _SPEED
        y_coords = np.arange(dimensions.height) / _SCALE

        xx, yy = np.meshgrid(x_coords, y_coords)

        for octave in range(3):
            frequency = 2.0 ** octave
            amplitude = 0.5 ** octave
            value_base = self._perlin_noise_vectorized(
                xx * frequency, yy * frequency, amplitude
            )
            if octave == 0:
                values = value_base
            else:
                values += value_base

        values = np.clip(values, 0, 1) * 255

        pixels[:, :, 0] = np.clip(values * 0.3, 0, 255).astype(np.int32)
        pixels[:, :, 1] = np.clip(values * 0.6, 0, 255).astype(np.int32)
        pixels[:, :, 2] = np.clip(values, 0, 255).astype(np.int32)

        return pixels

    def _perlin_noise_vectorized(self, x: np.ndarray, y: np.ndarray, amplitude: float) -> np.ndarray:
        xi = np.floor(x).astype(int)
        yi = np.floor(y).astype(int)
        xf = x - xi
        yf = y - yi

        n00 = self._perlin_hash(xi, yi)
        n10 = self._perlin_hash(xi + 1, yi)
        n01 = self._perlin_hash(xi, yi + 1)
        n11 = self._perlin_hash(xi + 1, yi + 1)

        smooth_xf = xf * xf * (3.0 - 2.0 * xf)
        smooth_yf = yf * yf * (3.0 - 2.0 * yf)

        nx0 = n00 + (n10 - n00) * smooth_xf
        nx1 = n01 + (n11 - n01) * smooth_xf
        result = nx0 + (nx1 - nx0) * smooth_yf

        return result * amplitude
