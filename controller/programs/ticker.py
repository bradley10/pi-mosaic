from __future__ import annotations

import logging
import time

import numpy as np

from controller.data import (
    Character,
    PixelDisplay,
    dimensions,
    draw_character_on,
    draw_lines_on,
    word_width,
)
from controller.http_client import make_session
from controller.timing import run_periodically, spawn_daemon

logger = logging.getLogger(__name__)

_TIMEOUT = 8
_POLL_INTERVAL = 45
_ROTATE_SECONDS = 6

UP_COLOR = (40, 200, 60)
DOWN_COLOR = (220, 40, 40)
SPARKLINE_COLOR = (235, 235, 240)

_SPARK_TOP_ROW = 17
_SPARK_BOTTOM_ROW = 31

# A small solid arrow (triangle head + stem) rather than the bare "^"/"v"
# font glyph, drawn in the up/down color instead of tinting the whole page.
_UP_ARROW = Character(
    character_key="up_arrow",
    character_value=[0b00100, 0b01110, 0b11111, 0b00100, 0b00100, 0b00100, 0b00100],
    width_px=5,
    height_px=7,
)
_DOWN_ARROW = Character(
    character_key="down_arrow",
    character_value=[0b00100, 0b00100, 0b00100, 0b00100, 0b11111, 0b01110, 0b00100],
    width_px=5,
    height_px=7,
)
_ARROW_GAP = 3  # px between the price text and the arrow icon

# CoinGecko for BTC (both current price and 7-day history), Yahoo Finance
# for stocks, and a free metals API for gold prices. Yahoo Finance requires
# a browser-like User-Agent to avoid 429 errors.
_STOCK_SYMBOLS = ["DNA"]
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_ENTRIES = [
    {"key": "DNA", "label": "DNA"},
    {"key": "BTC", "label": "BTC"},
    {"key": "GOLD", "label": "GOLD"},
]


def _draw_sparkline(pixels: PixelDisplay, history) -> None:
    """Plot `history` (a list of prices spanning about a week) as a
    connected line across the bottom of the display. Each column fills the
    full vertical span between it and the previous column's row, rather
    than just lighting a single pixel per column, so sharp moves between
    adjacent columns don't leave a visual gap."""
    if len(history) < 2:
        return
    h_min, h_max = min(history), max(history)
    h_range = (h_max - h_min) or 1.0
    n = len(history)
    span = _SPARK_BOTTOM_ROW - _SPARK_TOP_ROW

    def row_for(col: int) -> int:
        idx = min(n - 1, int(col / dimensions.width * n))
        frac = (history[idx] - h_min) / h_range
        row = _SPARK_BOTTOM_ROW - int(frac * span)
        return max(_SPARK_TOP_ROW, min(_SPARK_BOTTOM_ROW, row))

    prev_row = row_for(0)
    for col in range(dimensions.width):
        row = row_for(col)
        top, bottom = (row, prev_row) if row < prev_row else (prev_row, row)
        pixels[top : bottom + 1, col] = SPARKLINE_COLOR
        prev_row = row


class Ticker:
    def __init__(self):
        self._session = make_session()
        self._quotes = {}  # key -> {"price": float, "is_up": bool, "history": [float]}
        self._pixels = np.zeros(
            (dimensions.height, dimensions.width, 3), dtype=np.int32
        )

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self):
        spawn_daemon(self._render_loop)
        spawn_daemon(self._http_loop)

    def _http_loop(self):
        run_periodically(self._poll, _POLL_INTERVAL)

    def _poll(self) -> None:
        self._poll_btc()
        self._poll_gold()
        self._poll_stocks()

    def _poll_btc(self) -> None:
        try:
            response = self._session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "bitcoin",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            btc = response.json()["bitcoin"]
            price = float(btc["usd"])
            is_up = float(btc.get("usd_24h_change", 0)) >= 0

            history_response = self._session.get(
                "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
                params={"vs_currency": "usd", "days": 7},
                timeout=_TIMEOUT,
            )
            history_response.raise_for_status()
            history = [p[1] for p in history_response.json().get("prices", [])]

            self._quotes["BTC"] = {"price": price, "is_up": is_up, "history": history}
        except Exception as err:
            logger.info(f"Error fetching BTC price: {err}")

    def _poll_gold(self) -> None:
        try:
            response = self._session.get(
                "https://api.metals.live/v1/spot/price",
                params={"currency": "USD"},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            price = float(data["metals"]["gold"])

            self._quotes["GOLD"] = {
                "price": price,
                "is_up": True,
                "history": [price] * 7,
            }
        except Exception as err:
            logger.info(f"Error fetching GOLD price: {err}")

    def _poll_stocks(self) -> None:
        for symbol in _STOCK_SYMBOLS:
            try:
                response = self._session.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                    params={"interval": "1h", "range": "5d"},
                    headers=_BROWSER_HEADERS,
                    timeout=_TIMEOUT,
                )
                response.raise_for_status()
                result = response.json()["chart"]["result"][0]
                meta = result["meta"]
                price = float(meta["regularMarketPrice"])
                prev_close = float(meta["chartPreviousClose"])
                closes = result["indicators"]["quote"][0]["close"]
                history = [float(c) for c in closes if c is not None]

                self._quotes[symbol] = {
                    "price": price,
                    "is_up": price >= prev_close,
                    "history": history,
                }
            except Exception as err:
                logger.info(f"Error fetching {symbol} quote: {err}")

    def _render_loop(self):
        while True:
            self._pixels = self._render()
            time.sleep(0.2)

    def _render(self) -> PixelDisplay:
        entry = _ENTRIES[int(time.time() // _ROTATE_SECONDS) % len(_ENTRIES)]
        quote = self._quotes.get(entry["key"])

        pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

        if not quote:
            draw_lines_on(
                pixels,
                ["-center-Loading", f"-center-{entry['label']}..."],
                vertical_shift=12,
            )
            return pixels

        draw_lines_on(
            pixels,
            [f"-center-{entry['label']}", f"-center-{quote['price']:.2f}"],
        )

        # Arrow icon sits just to the right of the (centered) price text,
        # colored by direction instead of tinting the whole page.
        price_text = f"{quote['price']:.2f}"
        price_width = word_width(price_text)
        price_col = (dimensions.width - price_width) // 2
        is_up = quote["is_up"]
        draw_character_on(
            pixels,
            _UP_ARROW if is_up else _DOWN_ARROW,
            row_start=8,
            col_start=price_col + price_width + _ARROW_GAP,
            color=UP_COLOR if is_up else DOWN_COLOR,
        )

        _draw_sparkline(pixels, quote["history"])
        return pixels
