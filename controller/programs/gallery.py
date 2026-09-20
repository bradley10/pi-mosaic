from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from controller.data import PixelDisplay
from controller.settings import settings

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
    {"title": "The Starry Night", "artist": "Van Gogh"},
    {"title": "Starry Night Over the Rhone", "artist": "Van Gogh"},
    {"title": "A Sunday on La Grande Jatte", "artist": "Seurat"},
    {"title": "The Gulf Stream", "artist": "Winslow Homer"},
]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _asset_path(title: str) -> Path:
    return _ASSETS_DIR / f"{_slug(title)}.npy"


def apply_contrast(source: PixelDisplay, factor: float) -> PixelDisplay:
    """Push `source`'s tones apart (factor > 1) or together (factor < 1).

    Pivots on the frame's own mean luminance rather than a flat 128, which is
    what `ImageEnhance.Contrast` does in `scripts/fetch_paintings.py` - so
    turning this knob behaves like the boost already baked into the assets
    instead of also shifting the whole image lighter or darker.
    """
    if factor == 1.0:
        return source.copy()
    # Rec. 601 luma, matching PIL's "L" conversion.
    pivot = float(np.dot(source.reshape(-1, 3).mean(axis=0), (0.299, 0.587, 0.114)))
    adjusted = (source - pivot) * factor + pivot
    return adjusted.clip(0, 255).astype(source.dtype)


class Painting:
    """Displays a single artwork - one instance per entry in `ARTWORKS`, so
    each painting gets its own page in the program rotation (button A pages
    between them individually). The frame is a static asset loaded once at
    startup, not fetched over the network.

    Static is the awkward part. Every other program republishes a brand-new
    array each frame, which is what the main loop's `frame is not last_frame`
    identity check keys off. A painting that held one array forever would be
    pushed to the display exactly once, so a later contrast change would never
    reach the board - hence `_republish` on every settings change. (Brightness
    is a separate path: it's a hardware PWM setting, applied by `AdaFruit`
    directly on change for the same reason.)
    """

    def __init__(self, artwork: dict[str, str]):
        self._artwork = artwork
        # Kept pristine - contrast is always recomputed from this, never
        # applied on top of an already-adjusted frame, so dragging the slider
        # back and forth doesn't progressively destroy the image.
        self._source: PixelDisplay = np.load(_asset_path(artwork["title"]))
        self._contrast = None
        self._pixels: PixelDisplay = self._source.copy()
        self._republish()
        settings.subscribe(self._on_settings_changed)

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

    def _on_settings_changed(self, changed: dict) -> None:
        if "gallery.contrast" in changed:
            self._republish()

    def _republish(self) -> None:
        """Rebuild the frame as a *new* array, so the main loop notices."""
        factor = settings.get("gallery.contrast")
        if factor == self._contrast:
            return
        self._contrast = factor
        self._pixels = apply_contrast(self._source, factor)


def create_paintings() -> list[Painting]:
    """One `Painting` program per entry in `ARTWORKS`."""
    return [Painting(artwork) for artwork in ARTWORKS]
