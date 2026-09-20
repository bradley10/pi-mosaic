import random

import numpy as np

from controller.data import PixelDisplay, dimensions
from controller.settings import settings
from controller.timing import run_periodically, spawn_daemon


class Ball:
    _BG = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

    def __init__(self):
        self.ball_frequency_hz = 20

        # ball_width:ball_height ratio must not be the same as width:height,
        # otherwise the animation will repeat every 1-4 loops
        self.ball_width = 4
        self.ball_height = 4

        self.ball_dx = 1
        self.ball_dy = 1

        self.ball_x_position = dimensions.width // 2 - self.ball_width
        self.ball_y_position = dimensions.height // 2 - self.ball_height

        self._pixels = self._BG.copy()

        self.ball_color = (255, 255 // 2, 255 // 2)

    @property
    def pixels(self) -> PixelDisplay:
        """Return a copy of pixels."""
        return self._pixels

    def start(self):
        """Polling method placeholder."""
        spawn_daemon(self._main_loop)

    def _main_loop(self):
        """Main loop for the ball program."""
        run_periodically(self._step, 1 / self.ball_frequency_hz, owner=self)

    def _step(self) -> None:
        pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

        # Re-read the size every step so the settings page takes effect
        # immediately. Drawing wraps with `%`, and the bounce test below is
        # relative to the current size, so a resize mid-flight is safe.
        self.ball_width = self.ball_height = settings.get("ball.size")
        if not settings.get("ball.random_colors"):
            self.ball_color = settings.get("ball.color")

        # move
        self.ball_x_position += self.ball_dx
        self.ball_y_position += self.ball_dy

        # Draw the logo at the new position
        for i in range(self.ball_height):
            for j in range(self.ball_width):
                pixels[(self.ball_y_position + i) % dimensions.height][
                    (self.ball_x_position + j) % dimensions.width
                ] = self.ball_color

        # Check for bouncing
        if (
            self.ball_x_position <= 0
            or self.ball_x_position >= dimensions.width - self.ball_width
        ):
            self.ball_dx *= -1
            self._recolor()
        if (
            self.ball_y_position <= 0
            or self.ball_y_position >= dimensions.height - self.ball_height
        ):
            self.ball_dy *= -1
            self._recolor()

        self._pixels = pixels

    def _recolor(self) -> None:
        if not settings.get("ball.random_colors"):
            self.ball_color = settings.get("ball.color")
            return
        self.ball_color = (
            random.randint(0, 255),
            random.randint(0, 255),
            random.randint(0, 255),
        )
