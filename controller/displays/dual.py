from __future__ import annotations

import logging

from controller.data import PixelDisplay
from controller.displays import DisplayProtocol
from controller.displays.adafruit import AdaFruit
from controller.displays.simulate import Simulate

logger = logging.getLogger(__name__)


class Dual(DisplayProtocol):
    """Drives the physical LED matrix and mirrors the same frames to the web
    simulator at the same time, so the display can also be viewed and
    controlled remotely - e.g. over Tailscale - while it's running on real
    hardware. The physical GPIO buttons are disabled; the web page's
    buttons (and clicking a page in its gallery view) are the only input."""

    def __init__(self):
        # AdaFruit's RGBMatrix drops root privileges by default once the
        # hardware is initialized (rpi-rgb-led-matrix's own
        # drop_privileges behavior - see --led-no-drop-privs in
        # adafruit.py). Anything that still needs root - binding port 80 -
        # has to be constructed first, while we're still root; AdaFruit
        # must go last.
        #
        # Port 80 (the default for http://) so the page is reachable by
        # just typing the Pi's hostname, no ":8765" needed.
        self.web = Simulate(host="0.0.0.0", port=80, open_browser=False)
        self.hardware = AdaFruit()

    @property
    def button_a_index(self) -> int:
        return self.web.button_a_index

    @property
    def button_b_index(self) -> int:
        return self.web.button_b_index

    def take_pending_select(self) -> int | None:
        return self.web.take_pending_select()

    def display_matrix(self, pixels: PixelDisplay) -> None:
        self.hardware.display_matrix(pixels=pixels)
        self.web.display_matrix(pixels=pixels)

    def display_gallery(self, frames: list[tuple[str, PixelDisplay]]) -> None:
        self.web.display_gallery(frames)
