"""A tiny multi-color pixel-art icon library.

Structurally parallel to the font system in `controller/data.py`
(`Character`/`Font`/`draw_character_on`), but each `Sprite` may use several
palette colors instead of a single foreground color, since small pictures
(a boat, a plane, a sun) read much better with 2-4 colors than 1-bit.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List

from controller.color import Color, pulse
from controller.data import PixelDisplay, dimensions


@dataclass(frozen=True)
class Sprite:
    rows: List[str]
    """Each row is a string of palette-key characters; ' ' is transparent."""

    palette: Dict[str, Color]

    @property
    def height_px(self) -> int:
        return len(self.rows)

    @property
    def width_px(self) -> int:
        return max((len(r) for r in self.rows), default=0)


def draw_sprite_on(
    pixels: PixelDisplay, sprite: Sprite, row_start: int, col_start: int
) -> PixelDisplay:
    """Blit `sprite` at (row_start, col_start), skipping transparent cells."""
    for i, row in enumerate(sprite.rows):
        for j, key in enumerate(row):
            if key == " ":
                continue
            color = sprite.palette.get(key)
            if color is None:
                continue
            r, c = row_start + i, col_start + j
            if 0 <= r < pixels.shape[0] and 0 <= c < pixels.shape[1]:
                pixels[r][c] = color
    return pixels


def mirror_horizontal(sprite: Sprite) -> Sprite:
    return Sprite(rows=[row[::-1] for row in sprite.rows], palette=sprite.palette)


def mirror_vertical(sprite: Sprite) -> Sprite:
    return Sprite(rows=list(reversed(sprite.rows)), palette=sprite.palette)


def rotate_90_cw(sprite: Sprite) -> Sprite:
    """Rotate a (near-)square sprite 90 degrees clockwise."""
    h = sprite.height_px
    w = sprite.width_px
    padded = [row.ljust(w) for row in sprite.rows]
    new_rows = ["".join(padded[h - 1 - r][c] for r in range(h)) for c in range(w)]
    return Sprite(rows=new_rows, palette=sprite.palette)


# --- Palette colors -----------------------------------------------------

SUN_CORE: Color = (255, 210, 40)
SUN_RAY: Color = (255, 150, 20)
CLOUD_LIGHT: Color = (235, 235, 245)
CLOUD_SHADOW: Color = (175, 180, 195)
MOON_SILVER: Color = (215, 215, 225)
STAR_WHITE: Color = (255, 255, 255)
PLANE_BODY: Color = (225, 225, 235)
PLANE_ACCENT: Color = (210, 50, 50)
SAT_BODY: Color = (210, 210, 220)
SAT_PANEL: Color = (50, 90, 190)
BOAT_HULL: Color = (120, 80, 55)
BOAT_HULL_GREY: Color = (150, 150, 160)
BOAT_HULL_RED: Color = (190, 50, 50)
BOAT_SAIL: Color = (240, 240, 240)
BOAT_CABIN: Color = (215, 215, 220)
WAVE_LIGHT: Color = (90, 160, 210)
DNA_A: Color = (70, 150, 230)
DNA_B: Color = (90, 210, 140)
DNA_RUNG: Color = (170, 190, 195)
COIN_GOLD: Color = (255, 195, 40)
COIN_SHADE: Color = (210, 150, 20)
CHART_GREEN: Color = (70, 210, 140)
BUBBLE_WHITE: Color = (235, 235, 240)


# --- Sky / weather -------------------------------------------------------

SUN = Sprite(
    rows=[
        "    r    ",
        "  r Y r  ",
        "   YYY   ",
        " rYYYYYr ",
        "rYYYYYYYr",
        " rYYYYYr ",
        "   YYY   ",
        "  r Y r  ",
        "    r    ",
    ],
    palette={"Y": SUN_CORE, "r": SUN_RAY},
)

CLOUD = Sprite(
    rows=[
        "  LL LL  ",
        " LLLLLLL ",
        "LLLLLLLLL",
        "SSSSSSSSS",
        " SSSSSSS ",
    ],
    palette={"L": CLOUD_LIGHT, "S": CLOUD_SHADOW},
)

MOON_ICON = Sprite(
    rows=[
        "  MMM  ",
        " MMMMM ",
        "MMMMMMM",
        "MMMMMMM",
        "MMMMMMM",
        " MMMMM ",
        "  MMM  ",
    ],
    palette={"M": MOON_SILVER},
)


MOON_DARK_SIDE: Color = (40, 42, 55)


def _moon_phase_sprite(phase_fraction: float, radius: int = 9) -> Sprite:
    """Build a full moon disc for `phase_fraction` in [0, 1) (0=new, 0.5=full).

    The whole circle is always drawn so the moon's shape reads clearly even
    near new moon; illuminated pixels use 'L' (bright silver), the dark limb
    uses 'D' (a dim grey) rather than being left transparent. The terminator
    is a proper ellipse (scaled by each row's circle half-width) rather than
    a single vertical cutoff, so crescents/gibbous shapes actually curve.
    """
    local = phase_fraction if phase_fraction <= 0.5 else phase_fraction - 0.5
    term_center = radius * (1 - (local / 0.5) * 2)
    waxing = phase_fraction <= 0.5

    # A strict circle (dx^2+dy^2<=r^2) tapers to a single pixel at its top,
    # bottom, left and right - mathematically correct but reads as "pointy"
    # at this resolution. Padding the boundary test rounds those tips out to
    # a few pixels wide, which looks smoother without changing the overall
    # size much. The terminator's half-width uses the same padded boundary
    # so it never pokes outside the (now slightly larger) disc.
    radius_sq = radius * radius + 3

    rows = []
    for dy in range(-radius, radius + 1):
        chars = []
        row_half_width = max(0.0, radius_sq - dy * dy) ** 0.5
        term = term_center * (row_half_width / radius)
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius_sq:
                chars.append(" ")
                continue
            lit = dx >= term if waxing else dx <= term
            chars.append("L" if lit else "D")
        rows.append("".join(chars))
    return Sprite(rows=rows, palette={"L": MOON_SILVER, "D": MOON_DARK_SIDE})


_MOON_RADIUS = 9

MOON_NEW = _moon_phase_sprite(0.0, radius=_MOON_RADIUS)
MOON_WAXING_CRESCENT = _moon_phase_sprite(0.125, radius=_MOON_RADIUS)
MOON_FIRST_QUARTER = _moon_phase_sprite(0.25, radius=_MOON_RADIUS)
MOON_WAXING_GIBBOUS = _moon_phase_sprite(0.375, radius=_MOON_RADIUS)
MOON_FULL = _moon_phase_sprite(0.5, radius=_MOON_RADIUS)
MOON_WANING_GIBBOUS = _moon_phase_sprite(0.625, radius=_MOON_RADIUS)
MOON_LAST_QUARTER = _moon_phase_sprite(0.75, radius=_MOON_RADIUS)
MOON_WANING_CRESCENT = _moon_phase_sprite(0.875, radius=_MOON_RADIUS)


# Kept short (<=8 chars) so `-center-` text fits the 64px-wide display.
MOON_PHASE_NAMES = [
    "New",
    "Wax Cres",
    "1st Qtr",
    "Wax Gibb",
    "Full",
    "Wan Gibb",
    "Last Qtr",
    "Wan Cres",
]

MOON_PHASES = [
    MOON_NEW,
    MOON_WAXING_CRESCENT,
    MOON_FIRST_QUARTER,
    MOON_WAXING_GIBBOUS,
    MOON_FULL,
    MOON_WANING_GIBBOUS,
    MOON_LAST_QUARTER,
    MOON_WANING_CRESCENT,
]


# --- Sky vehicles --------------------------------------------------------

PLANE_STRAIGHT = Sprite(
    rows=[
        "   T   ",
        "  AAA  ",
        " AAAAA ",
        "AAAAAAA",
        " AAAAA ",
        "  AAA  ",
        " A   A ",
    ],
    palette={"A": PLANE_BODY, "T": PLANE_ACCENT},
)

PLANE_DIAGONAL = Sprite(
    rows=[
        "A     T",
        " A   A ",
        "  A A  ",
        "   A   ",
        "  A A  ",
        " A   A ",
        "A     A",
    ],
    palette={"A": PLANE_BODY, "T": PLANE_ACCENT},
)

PLANE_HEADINGS: Dict[str, Sprite] = {
    "N": PLANE_STRAIGHT,
    "E": rotate_90_cw(PLANE_STRAIGHT),
    "S": rotate_90_cw(rotate_90_cw(PLANE_STRAIGHT)),
    "W": rotate_90_cw(rotate_90_cw(rotate_90_cw(PLANE_STRAIGHT))),
    "NE": PLANE_DIAGONAL,
    "SE": rotate_90_cw(PLANE_DIAGONAL),
    "SW": rotate_90_cw(rotate_90_cw(PLANE_DIAGONAL)),
    "NW": rotate_90_cw(rotate_90_cw(rotate_90_cw(PLANE_DIAGONAL))),
}

_COMPASS_ORDER = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def plane_sprite_for_heading(degrees: float) -> Sprite:
    """Pick the closest of the 8 pre-rotated plane sprites for a compass heading."""
    idx = round((degrees % 360) / 45) % 8
    return PLANE_HEADINGS[_COMPASS_ORDER[idx]]


SATELLITE = Sprite(
    rows=[
        "BB   BB",
        "BB S BB",
        "BBSSSBB",
        "BB S BB",
        "BB   BB",
    ],
    palette={"B": SAT_PANEL, "S": SAT_BODY},
)


# --- Boats ----------------------------------------------------------------

BOAT_FERRY = Sprite(
    rows=[
        "  CCCCC  ",
        " CCCCCCC ",
        "HHHHHHHHH",
        " HHHHHHH ",
    ],
    palette={"C": BOAT_CABIN, "H": BOAT_HULL_GREY},
)

BOAT_CARGO = Sprite(
    rows=[
        "       C ",
        "HHHHHHHHH",
        "HHHHHHHHH",
        " HHHHHHH ",
    ],
    palette={"C": BOAT_CABIN, "H": BOAT_HULL},
)

BOAT_SAILBOAT = Sprite(
    rows=[
        "    S    ",
        "   SSS   ",
        "  SSSSS  ",
        "HHHHHHHHH",
    ],
    palette={"S": BOAT_SAIL, "H": BOAT_HULL},
)

BOAT_FISHING = Sprite(
    rows=[
        "    M    ",
        "   HHH   ",
        "  HHHHH  ",
        " HHHHHHH ",
    ],
    palette={"M": BOAT_CABIN, "H": BOAT_HULL},
)

BOAT_TUG = Sprite(
    rows=[
        "  CCC    ",
        "  CCC    ",
        "HHHHHHHHH",
        " HHHHHHH ",
    ],
    palette={"C": BOAT_CABIN, "H": BOAT_HULL_RED},
)

BOATS: Dict[str, Sprite] = {
    "ferry": BOAT_FERRY,
    "cargo": BOAT_CARGO,
    "tanker": BOAT_CARGO,
    "sailboat": BOAT_SAILBOAT,
    "fishing": BOAT_FISHING,
    "tug": BOAT_TUG,
}

WAVE_TILE = Sprite(
    rows=[
        "W  W  W ",
        " W  W  W",
    ],
    palette={"W": WAVE_LIGHT},
)


def draw_wave_band(
    pixels: PixelDisplay, row_start: int, phase_offset: int, color: Color = WAVE_LIGHT
) -> PixelDisplay:
    """Tile `WAVE_TILE` across the full width, shifted by `phase_offset`."""
    tile = WAVE_TILE.rows
    tile_w = len(tile[0])
    width = pixels.shape[1]
    for i, row in enumerate(tile):
        r = row_start + i
        if not (0 <= r < pixels.shape[0]):
            continue
        for c in range(-tile_w, width + tile_w):
            key = row[(c - phase_offset) % tile_w]
            if key == " ":
                continue
            if 0 <= c < width:
                pixels[r][c] = color
    return pixels


# --- Ticker icons ----------------------------------------------------------

DNA_HELIX = Sprite(
    rows=[
        "A   B",
        " A B ",
        "  X  ",
        " B A ",
        "B   A",
        " B A ",
        "  X  ",
        " A B ",
        "A   B",
    ],
    palette={"A": DNA_A, "B": DNA_B, "X": DNA_RUNG},
)

COIN = Sprite(
    rows=[
        " GGGGG ",
        "GGGGGGG",
        "GG$$$GG",
        "GG$$$GG",
        "GG$$$GG",
        "GGGGGGG",
        " GGGGG ",
    ],
    palette={"G": COIN_GOLD, "$": COIN_SHADE},
)

CHART_UP = Sprite(
    rows=[
        "      C",
        "     CC",
        "    CC ",
        "   CC  ",
        "  CC   ",
        " CC    ",
        "CC     ",
    ],
    palette={"C": CHART_GREEN},
)


# --- Starfield background (shared by Moon Phase and ISS pages) -------------

# Generated once at the `moon.stars` setting's maximum and sliced to the
# configured count, so turning the count up or down adds and removes stars
# without shuffling the ones already on screen.
MAX_STARS = 60
_STAR_RNG = random.Random(1234)
STAR_POSITIONS = [
    (
        _STAR_RNG.randint(0, dimensions.height - 1),
        _STAR_RNG.randint(0, dimensions.width - 1),
    )
    for _ in range(MAX_STARS)
]


def draw_starfield(
    pixels: PixelDisplay,
    t: float,
    exclude_center: tuple | None = None,
    exclude_radius: float = 0,
    count: int | None = None,
) -> PixelDisplay:
    """Draw a fixed set of stars whose brightness twinkles over time `t`
    (seconds). Pass `exclude_center`/`exclude_radius` to skip any star that
    would land too close to another element (e.g. a moon icon), so it never
    looks like a star is touching/stuck to it. `count` limits how many of
    `STAR_POSITIONS` are drawn (default: all of them)."""
    stars = STAR_POSITIONS if count is None else STAR_POSITIONS[: max(0, count)]
    for i, (r, c) in enumerate(stars):
        if exclude_center is not None:
            dr, dc = r - exclude_center[0], c - exclude_center[1]
            if dr * dr + dc * dc <= exclude_radius * exclude_radius:
                continue
        brightness = 0.35 + 0.65 * pulse(t * 0.7 + i * 1.7, period=3.0 + (i % 5))
        value = int(255 * brightness)
        pixels[r][c] = (value, value, value)
    return pixels


# --- Misc -------------------------------------------------------------------

SPEECH_BUBBLE = Sprite(
    rows=[
        " BBBBBBB ",
        "BBBBBBBBB",
        "BBBBBBBBB",
        "BBBBBBBBB",
        " BBB BBB ",
        "  BB     ",
    ],
    palette={"B": BUBBLE_WHITE},
)
