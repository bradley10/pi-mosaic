from __future__ import annotations

import math
import random
import time

from controller.blob_field import Blob, render_field
from controller.data import PixelDisplay, dimensions
from controller.settings import settings
from controller.timing import run_periodically, spawn_daemon

BACKGROUND = (0, 0, 0)
BANDS = [
    (0.6, (90, 5, 5)),
    (1.0, (170, 15, 10)),
    (1.6, (230, 40, 20)),
    (2.4, (255, 130, 40)),
    (3.4, (255, 220, 90)),
]

NUM_BLOBS = 4


class LavaLamp:
    def __init__(self):
        self._rng = random.Random(99)
        self._blobs = []
        self._bob_phase = []
        self._match_blob_count()
        self._start_time = time.monotonic()
        self._pixels = render_field(self._blobs, BANDS, BACKGROUND)

    def _match_blob_count(self) -> None:
        """Grow or shrink the field to the `lava.blobs` setting, keeping the
        blobs already on screen where they are. `_bob_phase` is indexed by
        blob, so it has to stay exactly the same length."""
        rng = self._rng
        wanted = settings.get("lava.blobs")
        while len(self._blobs) < wanted:
            self._blobs.append(
                Blob(
                    x=rng.uniform(dimensions.width * 0.25, dimensions.width * 0.75),
                    y=rng.uniform(0, dimensions.height),
                    vx=0.0,
                    vy=rng.uniform(0.0125, 0.03) * rng.choice([-1, 1]),
                    radius=rng.uniform(4.0, 6.5),
                )
            )
            self._bob_phase.append(rng.uniform(0, math.tau))
        if len(self._blobs) > wanted:
            del self._blobs[wanted:]
            del self._bob_phase[wanted:]

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self):
        spawn_daemon(self._main_loop)

    def _main_loop(self):
        # blob_field.render_field is now vectorized with numpy instead of a
        # per-pixel Python loop, so this can redraw much more often than the
        # old 0.12s - the vertical drift and bob speeds above are scaled down
        # to match, so blobs move at the same actual speed, just redrawn
        # more smoothly.
        run_periodically(self._render, interval=0.03, owner=self)

    def _render(self) -> None:
        self._step()
        self._pixels = render_field(self._blobs, BANDS, BACKGROUND)

    def _step(self) -> None:
        self._match_blob_count()
        t = time.monotonic() - self._start_time
        drift = settings.get("lava.drift")
        for i, b in enumerate(self._blobs):
            b.y += b.vy * drift
            if b.y <= b.radius or b.y >= dimensions.height - b.radius:
                b.vy *= -1
            # slow horizontal bob, independent of vertical drift
            b.x += math.sin(t * 0.5 + self._bob_phase[i]) * 0.02
            b.x = min(max(b.x, b.radius), dimensions.width - b.radius)
