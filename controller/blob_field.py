"""Shared metaball-style scalar-field rendering, used by the Metaballs and Lava
Lamp programs — same field math, different motion and palette per caller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from controller.color import Color
from controller.data import PixelDisplay, dimensions


@dataclass
class Blob:
    x: float
    y: float
    vx: float
    vy: float
    radius: float


_ROWS = np.arange(dimensions.height, dtype=np.float64).reshape(-1, 1)
_COLS = np.arange(dimensions.width, dtype=np.float64).reshape(1, -1)


def render_field(
    blobs: List[Blob], bands: List[Tuple[float, Color]], background: Color
) -> PixelDisplay:
    """Render `sum(radius^2 / distance^2)` for `blobs` at every pixel, colored by
    the highest-threshold band (from `bands`, ascending) each pixel clears.

    Vectorized over the whole grid with numpy (rather than a per-pixel Python
    loop) since this runs every frame - the per-pixel version was the main
    thing capping how fast Metaballs/LavaLamp could redraw."""
    field = np.zeros((dimensions.height, dimensions.width), dtype=np.float64)
    for b in blobs:
        dist_sq = np.maximum((_COLS - b.x) ** 2 + (_ROWS - b.y) ** 2, 0.001)
        field += (b.radius * b.radius) / dist_sq

    pixels = np.empty((dimensions.height, dimensions.width, 3), dtype=np.int32)
    pixels[:, :] = background
    for threshold, band_color in bands:
        pixels[field >= threshold] = band_color
    return pixels
