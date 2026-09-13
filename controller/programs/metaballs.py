from __future__ import annotations

import random
import time

from controller.blob_field import Blob, render_field
from controller.data import PixelDisplay, dimensions
from controller.timing import spawn_daemon

BACKGROUND = (0, 0, 0)
BANDS = [
    (0.6, (40, 60, 150)),
    (1.0, (80, 130, 210)),
    (1.6, (170, 225, 255)),
]

NUM_BLOBS = 5


class Metaballs:
    def __init__(self):
        rng = random.Random(21)
        self._blobs = [
            Blob(
                x=rng.uniform(0, dimensions.width),
                y=rng.uniform(0, dimensions.height),
                vx=rng.uniform(-0.1, 0.1),
                vy=rng.uniform(-0.1, 0.1),
                radius=rng.uniform(3.0, 5.5),
            )
            for _ in range(NUM_BLOBS)
        ]
        self._pixels = render_field(self._blobs, BANDS, BACKGROUND)

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self):
        spawn_daemon(self._main_loop)

    def _main_loop(self):
        # blob_field.render_field is now vectorized with numpy instead of a
        # per-pixel Python loop, so this can redraw much more often than the
        # old 0.08s - velocities above are scaled down to match, so the
        # blobs drift at the same actual speed, just redrawn more smoothly.
        while True:
            self._step()
            self._pixels = render_field(self._blobs, BANDS, BACKGROUND)
            time.sleep(0.02)

    def _step(self) -> None:
        for b in self._blobs:
            b.x += b.vx
            b.y += b.vy
            if b.x <= 0 or b.x >= dimensions.width:
                b.vx *= -1
            if b.y <= 0 or b.y >= dimensions.height:
                b.vy *= -1
