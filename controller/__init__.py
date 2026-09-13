from __future__ import annotations

import logging
import sys
import time

import numpy as np

from controller.displays.dual import Dual
from controller.displays.simulate import Simulate
from controller.programs.ball import Ball
from controller.programs.clock import Clock
from controller.programs.gallery import create_paintings
from controller.programs.lava_lamp import LavaLamp
from controller.programs.metaballs import Metaballs
from controller.programs.moon import Moon
from controller.programs.snake import Snake
from controller.programs.ticker import Ticker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s.%(funcName)s %(levelname)s: %(message)s",
    datefmt="%Y.%m.%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class Controller:
    def __init__(self):
        self.simulate = len(sys.argv) > 1 and sys.argv[1] == "simulate"

        self._program = 0

        self.clock = Clock()
        self.ball = Ball()
        self.snake = Snake()
        self.moon = Moon()
        self.metaballs = Metaballs()
        self.lava_lamp = LavaLamp()
        self.ticker = Ticker()
        # Reads .npy asset files from disk, so must be constructed before
        # Dual/AdaFruit below - see the privilege-drop note there.
        self.paintings = create_paintings()

        if self.simulate:
            self.display: Simulate | Dual = Simulate()
            self.keyboard: Simulate | Dual = self.display
        else:
            # Drives the physical LED matrix and mirrors the same frames to
            # the web simulator at the same time, so it can also be viewed
            # and controlled remotely (e.g. over Tailscale).
            self.display = Dual()
            self.keyboard = self.display

    def start(self):
        self._main_loop()

    def _main_loop(self):
        programs = [
            self.clock,
            self.ball,
            self.snake,
            self.moon,
            self.metaballs,
            self.lava_lamp,
            self.ticker,
            *self.paintings,
        ]
        for program in programs:
            program.start()

        pixels = None

        # Track the last seen counts from the keyboard
        last_button_a = 0
        last_button_b = 0

        while True:
            # NEXT: advance one page per press, even if several presses land
            # between loop ticks.
            presses_a = self.keyboard.button_a_index - last_button_a
            if presses_a > 0:
                last_button_a = self.keyboard.button_a_index
                self._program = (self._program + presses_a) % len(programs)
                logger.info(f"Switched program to index {self._program} (next)")

            # BACK: same, in reverse.
            presses_b = self.keyboard.button_b_index - last_button_b
            if presses_b > 0:
                last_button_b = self.keyboard.button_b_index
                self._program = (self._program - presses_b) % len(programs)
                logger.info(f"Switched program to index {self._program} (back)")

            # Clicking a page directly in the web gallery view jumps
            # straight to it.
            take_pending_select = getattr(self.keyboard, "take_pending_select", None)
            selected = take_pending_select() if take_pending_select else None
            if selected is not None and 0 <= selected < len(programs):
                self._program = selected
                logger.info(f"Switched program to index {self._program} (selected)")

            # Update the display only if pixels change.
            new_pixels = programs[self._program].pixels
            if pixels is None or not np.array_equal(pixels, new_pixels):
                pixels = new_pixels
                self.display.display_matrix(pixels=pixels)

            # The simulator's gallery view wants every program's frame at
            # once; real hardware only ever shows the one active program.
            if hasattr(self.display, "display_gallery"):
                self.display.display_gallery(
                    [
                        (getattr(p, "display_name", type(p).__name__), p.pixels)
                        for p in programs
                    ]
                )

            time.sleep(0.005)
