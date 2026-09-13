import math
from typing import Tuple

Color = Tuple[int, int, int]


def lerp_color(c1: Color, c2: Color, t: float) -> Color:
    """Linearly interpolate between two colors. `t` is clamped to [0, 1]."""
    t = max(0.0, min(1.0, t))
    r = int(c1[0] + (c2[0] - c1[0]) * t)
    g = int(c1[1] + (c2[1] - c1[1]) * t)
    b = int(c1[2] + (c2[2] - c1[2]) * t)
    return (r, g, b)


def color_from_hsv(h: float, s: float, v: float) -> Color:
    """Build an RGB `Color` from hue (degrees), saturation (0-1), value (0-1)."""
    h = h % 360
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0.0
    elif h < 120:
        r, g, b = x, c, 0.0
    elif h < 180:
        r, g, b = 0.0, c, x
    elif h < 240:
        r, g, b = 0.0, x, c
    elif h < 300:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


def hue_shift(color: Color, degrees: float) -> Color:
    """Rotate `color`'s hue by `degrees`, preserving its saturation/value."""
    r, g, b = (c / 255.0 for c in color)
    mx, mn = max(r, g, b), min(r, g, b)
    v = mx
    d = mx - mn
    s = 0.0 if mx == 0 else d / mx

    if d == 0:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / d) + 360) % 360
    elif mx == g:
        h = (60 * ((b - r) / d) + 120) % 360
    else:
        h = (60 * ((r - g) / d) + 240) % 360

    return color_from_hsv(h + degrees, s, v)


def pulse(t: float, period: float) -> float:
    """Return a smooth 0-1 'breathing' value oscillating with the given period."""
    return (math.sin(2 * math.pi * t / period) + 1) / 2
