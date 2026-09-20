"""Fetches the gallery's source paintings from Wikimedia Commons, crops and
downsizes each to the matrix's exact resolution, and writes the result as a
.npy file under controller/assets/paintings/ - the files `Painting` (in
controller/programs/gallery.py) loads at runtime.

Run this from a dev machine (not the Pi) whenever adding/changing an
artwork - it needs network access and a working Pillow/JPEG decoder,
neither of which the board itself relies on at runtime:

    uv run python scripts/fetch_paintings.py

To add a painting: add its title/artist/Commons file title to `SOURCES`
below, add a matching {title, artist} entry to `ARTWORKS` in
controller/programs/gallery.py, and re-run this script.

Pass titles to rebuild only those, leaving the other assets untouched:

    uv run python scripts/fetch_paintings.py "The Starry Night"

Worth using. Wikimedia regenerates its server-side thumbnails from time to
time, so a bare re-run can quietly rewrite every .npy with a slightly
different rendering of artwork you never meant to touch.
"""

from __future__ import annotations

import re
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageEnhance

sys.path.insert(0, str(Path(__file__).parent.parent))

from controller.data import dimensions  # noqa: E402
from controller.http_client import make_session  # noqa: E402

_TIMEOUT = 15
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Ask Wikimedia to scale the image down server-side rather than downloading
# the full original - some source files are gigapixel-scale scans.
_THUMB_WIDTH = 600
# Wikimedia rejects requests without a descriptive User-Agent identifying
# the client, per https://meta.wikimedia.org/wiki/User-Agent_policy.
_HEADERS = {"User-Agent": "pi-mosaic-led-board/1.0 (personal project)"}

_OUT_DIR = Path(__file__).parent.parent / "controller" / "assets" / "paintings"
# A small boost - LED matrices wash out subtle tonal differences a screen
# preview doesn't, so a slightly punchier image reads better on the board.
_CONTRAST_FACTOR = 1.2

# Fraction trimmed off all four sides before the aspect crop, per source.
# Museum scans often include a little unpainted canvas or frame edge, and at
# 64x32 a single column is 1.5% of the width - so a fringe far too thin to
# notice in the source lands as one conspicuously bright edge column on the
# board. Only set this where the source actually needs it; check with the
# column-luminance profile (a real composition doesn't jump 30+ points in one
# column and drop straight back).
_DEFAULT_INSET = 0.0

# Chosen for bold, high-contrast color masses (survives being crushed down
# to a 64x32 matrix) and a native aspect ratio already close to 2:1 (so the
# center-crop below only trims edges instead of cutting the composition).
SOURCES = [
    {
        "title": "The Great Wave",
        "file_title": "File:Tsunami by hokusai 19th century.jpg",
        # No inset: this print's bright left column is its own sky, not a scan
        # border. The source declines gently from luma 204 to the interior's
        # 192 with no step, so trimming only costs composition. Checked.
    },
    {
        "title": "Wheatfield with Crows",
        "file_title": (
            "File:Vincent van Gogh - Wheatfield with crows - Google Art Project.jpg"
        ),
    },
    {
        "title": "The Starry Night",
        # The famous MoMA one. At 1.26:1 it's the furthest from the matrix's
        # 2:1 of anything here, so the centre-crop below trims a good deal of
        # sky and village - but the swirl, the moon and the cypress all sit in
        # the band that survives.
        "file_title": "File:Van Gogh - Starry Night - Google Art Project.jpg",
        # This scan carries a bright unpainted canvas edge: the source fades
        # from luma 139 at the left border to the interior's 89 over about ten
        # of its 960 columns, and rises to 162 at the right. Downsized, that
        # became a single blazing column at each end. 1% clears it (left step
        # +37 -> +0.3, right +21 -> -3); more than that just eats composition.
        "inset": 0.01,
    },
    {
        "title": "Starry Night Over the Rhone",
        "file_title": "File:Starry Night Over the Rhone.jpg",
    },
    {
        "title": "A Sunday on La Grande Jatte",
        "file_title": (
            "File:Georges Seurat - A Sunday on La Grande Jatte -- 1884 - "
            "Google Art Project.jpg"
        ),
    },
    {
        "title": "The Gulf Stream",
        "file_title": (
            "File:Winslow Homer - The Gulf Stream - Metropolitan Museum of Art.jpg"
        ),
    },
]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _resolve_thumb_url(session: requests.Session, file_title: str) -> str:
    response = session.get(
        _COMMONS_API,
        params={
            "action": "query",
            "titles": file_title,
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": _THUMB_WIDTH,
            "format": "json",
        },
        headers=_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    pages = response.json()["query"]["pages"]
    (page,) = pages.values()
    return page["imageinfo"][0]["thumburl"]


def _to_matrix_frame(raw: bytes, inset: float = _DEFAULT_INSET) -> np.ndarray:
    """Trim `inset` off every side, center-crop to the matrix's aspect ratio
    (so the frame is fully filled, no letterbox bars), and downsize.

    No palette quantization/dithering - Floyd-Steinberg dithering relies on
    the viewer's eye blending adjacent pixels at a distance, which works on
    a screen but not on a real LED matrix, where each pixel is a separate,
    sharp, saturated dot with no blur between them - it just reads as
    speckled noise up close. A plain smooth resize lets the matrix's own
    pixel grid be the only "pixelation."
    """
    img = Image.open(BytesIO(raw)).convert("RGB")

    # Before the aspect crop, so it trims the source's own border on all four
    # sides rather than whichever pair the aspect crop happens to keep.
    if inset:
        width, height = img.size
        dx, dy = round(width * inset), round(height * inset)
        img = img.crop((dx, dy, width - dx, height - dy))

    target_ratio = dimensions.width / dimensions.height
    width, height = img.size
    current_ratio = width / height
    if current_ratio > target_ratio:
        new_width = round(height * target_ratio)
        left = (width - new_width) // 2
        img = img.crop((left, 0, left + new_width, height))
    else:
        new_height = round(width / target_ratio)
        top = (height - new_height) // 2
        img = img.crop((0, top, width, top + new_height))

    img = img.resize((dimensions.width, dimensions.height), Image.Resampling.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(_CONTRAST_FACTOR)

    return np.array(img, dtype=np.int32)


def _selected(titles: list) -> list:
    """The sources named on the command line, or all of them."""
    if not titles:
        return SOURCES
    wanted = {_slug(title) for title in titles}
    chosen = [source for source in SOURCES if _slug(source["title"]) in wanted]
    missing = wanted - {_slug(source["title"]) for source in chosen}
    if missing:
        known = ", ".join(repr(source["title"]) for source in SOURCES)
        raise SystemExit(f"Unknown painting(s): {sorted(missing)}. Known: {known}")
    return chosen


def main(titles: list) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()

    for source in _selected(titles):
        thumb_url = _resolve_thumb_url(session, source["file_title"])
        response = session.get(thumb_url, headers=_HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()

        frame = _to_matrix_frame(response.content, source.get("inset", _DEFAULT_INSET))
        out_path = _OUT_DIR / f"{_slug(source['title'])}.npy"
        np.save(out_path, frame)
        print(f"saved {out_path} ({frame.shape})")


if __name__ == "__main__":
    main(sys.argv[1:])
