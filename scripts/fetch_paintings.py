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
_HEADERS = {"User-Agent": "mbta-tracker-led-board/1.0 (personal project)"}

_OUT_DIR = Path(__file__).parent.parent / "controller" / "assets" / "paintings"
# A small boost - LED matrices wash out subtle tonal differences a screen
# preview doesn't, so a slightly punchier image reads better on the board.
_CONTRAST_FACTOR = 1.4

# Chosen for bold, high-contrast color masses (survives being crushed down
# to a 64x32 matrix) and a native aspect ratio already close to 2:1 (so the
# center-crop below only trims edges instead of cutting the composition).
SOURCES = [
    {
        "title": "The Great Wave",
        "file_title": "File:Tsunami by hokusai 19th century.jpg",
    },
    {
        "title": "Wheatfield with Crows",
        "file_title": (
            "File:Vincent van Gogh - Wheatfield with crows - Google Art Project.jpg"
        ),
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


def _to_matrix_frame(raw: bytes) -> np.ndarray:
    """Center-crop to the matrix's aspect ratio (so the frame is fully
    filled, no letterbox bars) and downsize.

    No palette quantization/dithering - Floyd-Steinberg dithering relies on
    the viewer's eye blending adjacent pixels at a distance, which works on
    a screen but not on a real LED matrix, where each pixel is a separate,
    sharp, saturated dot with no blur between them - it just reads as
    speckled noise up close. A plain smooth resize lets the matrix's own
    pixel grid be the only "pixelation."
    """
    img = Image.open(BytesIO(raw)).convert("RGB")

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


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()

    for source in SOURCES:
        thumb_url = _resolve_thumb_url(session, source["file_title"])
        response = session.get(thumb_url, headers=_HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()

        frame = _to_matrix_frame(response.content)
        out_path = _OUT_DIR / f"{_slug(source['title'])}.npy"
        np.save(out_path, frame)
        print(f"saved {out_path} ({frame.shape})")


if __name__ == "__main__":
    main()
