from __future__ import annotations

import logging
import sys
import time

from controller.displays.dual import Dual
from controller.displays.simulate import Simulate
from controller.programs.ball import Ball
from controller.programs.clock import Clock
from controller.programs.connecting import Connecting
from controller.programs.gallery import create_paintings
from controller.programs.lava_lamp import LavaLamp
from controller.programs.metaballs import Metaballs
from controller.programs.moon import Moon
from controller.programs.perlin_terrain import PerlinTerrain
from controller.programs.snake import Snake
from controller.programs.tetris import Tetris
from controller.programs.ticker import Ticker

# Bound under a different name on purpose. This is the package __init__, so
# `from controller.settings import settings` would overwrite the
# `controller.settings` submodule attribute with the Settings instance, and
# `import controller.settings` would then hand callers the instance instead of
# the module.
from controller.settings import settings as app_settings
from controller.timing import render_gate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s.%(funcName)s %(levelname)s: %(message)s",
    datefmt="%Y.%m.%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# How often the main loop samples the active program and pushes to the
# display. Programs render on their own threads at their own rates; this is
# just the poll rate, fast enough that no program's frames are missed.
FRAME_INTERVAL = 1 / 60
# How often off-screen programs are allowed to render while somebody has the
# web gallery open, matching the gallery's own refresh rate.
GALLERY_INTERVAL = 0.15


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
        self.perlin_terrain = PerlinTerrain()
        self.tetris = Tetris()
        # Not part of the rotation - it stands in front of it until the clock
        # is trustworthy. See `Connecting`.
        self.connecting = Connecting(clock=self.clock)
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
            self.perlin_terrain,
            self.tetris,
            *self.paintings,
        ]
        for program in programs:
            program.start()
        self.connecting.start()

        # Track the last seen counts from the keyboard
        last_button_a = 0
        last_button_b = 0
        last_frame = None
        last_brightness = None
        # Only the program on the board renders at full rate; the rest are
        # throttled to whatever the web gallery needs, or idled when nobody
        # has it open. See `RenderGate` - twelve off-screen programs
        # rendering flat out was the single biggest source of stutter. It
        # starts on `Connecting` rather than `programs[self._program]` because
        # that's what's actually on the board first, which idles all thirteen.
        render_gate.set_active(self.connecting)

        next_frame = time.monotonic()
        while True:
            # NEXT: advance one page per press
            presses_a = self.keyboard.button_a_index - last_button_a
            if presses_a > 0:
                last_button_a = self.keyboard.button_a_index
                self._program = (self._program + presses_a) % len(programs)
                logger.info(f"Switched program to index {self._program} (next)")

            # BACK: advance one page in reverse
            presses_b = self.keyboard.button_b_index - last_button_b
            if presses_b > 0:
                last_button_b = self.keyboard.button_b_index
                self._program = (self._program - presses_b) % len(programs)
                logger.info(f"Switched program to index {self._program} (back)")

            # Direct selection from gallery
            take_pending_select = getattr(self.keyboard, "take_pending_select", None)
            selected = take_pending_select() if take_pending_select else None
            if selected is not None and 0 <= selected < len(programs):
                self._program = selected
                logger.info(f"Switched program to index {self._program} (selected)")

            # The controller starts before DHCP finishes, which is what gets
            # the board lit seconds after power-on rather than most of a
            # minute. Until the clock is synchronized every page that shows a
            # time would be showing the wrong one, so hold the whole rotation
            # behind `Connecting` rather than paging into it. This latches, so
            # a wifi blip later never sends the board back here.
            active = (
                programs[self._program] if self.connecting.ready else self.connecting
            )
            render_gate.set_active(active)

            # Programs always publish a brand-new array rather than mutating
            # in place, so identity is a sound "did the frame change?" test -
            # and unlike `np.array_equal` it costs nothing.
            #
            # Brightness has to be part of the test. It reaches the panel
            # through `display_matrix` (which sets the driver's PWM duty
            # cycle) and the web mirror only re-encodes a frame it has just
            # been handed - so on a page whose frame never changes, like a
            # painting, turning the knob did nothing at all until you paged
            # away and back. `settings.get` is a plain dict lookup, so
            # checking it every tick is free.
            frame = active.pixels
            brightness = app_settings.get("display.brightness")
            if frame is not last_frame or brightness != last_brightness:
                last_frame = frame
                last_brightness = brightness
                self.display.display_matrix(pixels=frame)

            # Collecting every program's frame is only worth doing while a
            # browser actually has the gallery open; the same signal decides
            # whether off-screen programs render at all.
            self._update_gallery(programs)

            # Pace against a running deadline rather than sleeping a fixed
            # amount after a variable amount of work - otherwise the frame
            # period is "16ms plus however long this iteration took", which
            # is exactly the wobble that made the animations look jittery.
            next_frame += FRAME_INTERVAL
            now = time.monotonic()
            if next_frame <= now:
                next_frame = now
            else:
                time.sleep(next_frame - now)

    def _update_gallery(self, programs: list) -> None:
        """Push every program's current frame to the web gallery, and keep
        `render_gate` in sync with whether anyone is watching it."""
        display_gallery = getattr(self.display, "display_gallery", None)
        if display_gallery is None:
            render_gate.set_background_interval(None)
            return

        watching = getattr(self.display, "has_gallery_viewers", False)
        render_gate.set_background_interval(GALLERY_INTERVAL if watching else None)
        if not watching:
            return

        display_gallery(
            [(getattr(p, "display_name", type(p).__name__), p.pixels) for p in programs]
        )
