from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from controller.data import PixelDisplay

_ASSETS_DIR = Path(__file__).parent.parent / "assets" / "paintings"

# Public-domain paintings chosen for two things: bold, high-contrast color
# masses that survive being crushed down to a 64x32 matrix, and a native
# aspect ratio already close to the matrix's 2:1, so the center-crop used to
# produce the asset only trims edges instead of cutting into the
# composition. Each was fetched once from Wikimedia Commons, center-cropped,
# and downsized to exactly the matrix's resolution - see
# `scripts/fetch_paintings.py` - then committed as a small .npy file under
# controller/assets/paintings/, so the board never needs network access (or
# a working JPEG decoder - the Pi's Pillow build can't identify JPEGs, see
# git history) to show them.
ARTWORKS = [
    {"title": "The Great Wave", "artist": "Hokusai"},
    {"title": "Wheatfield with Crows", "artist": "Van Gogh"},
    {"title": "Starry Night Over the Rhone", "artist": "Van Gogh"},
    {"title": "A Sunday on La Grande Jatte", "artist": "Seurat"},
    {"title": "The Gulf Stream", "artist": "Winslow Homer"},
]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _asset_path(title: str) -> Path:
    return _ASSETS_DIR / f"{_slug(title)}.npy"


class Painting:
    """Displays a single artwork - one instance per entry in `ARTWORKS`, so
    each painting gets its own page in the program rotation (button A pages
    between them individually). The frame is a static asset loaded once at
    startup, not fetched over the network."""

    def __init__(self, artwork: dict[str, str]):
        self._artwork = artwork
        self._pixels: PixelDisplay = np.load(_asset_path(artwork["title"]))

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    @property
    def display_name(self) -> str:
        """Distinguishes each instance in the simulator's gallery overview,
        which otherwise labels every program by its class name alone."""
        return self._artwork["title"]

    def start(self):
        pass


def create_paintings() -> list[Painting]:
    """One `Painting` program per entry in `ARTWORKS`."""
    return [Painting(artwork) for artwork in ARTWORKS]
