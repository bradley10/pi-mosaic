import datetime
import logging
import math
import time

import numpy as np
import requests
from requests.adapters import HTTPAdapter, Retry

from controller.color import Color, lerp_color, pulse
from controller.data import PixelDisplay, dimensions, draw_lines_on
from controller.sprites import MOON_ICON, SUN, draw_sprite_on
from controller.timing import run_periodically, spawn_daemon

logger = logging.getLogger(__name__)

COLD_COLOR: Color = (120, 170, 255)
NEUTRAL_COLOR: Color = (255, 255, 255)
HOT_COLOR: Color = (255, 90, 70)


def _temperature_color(temp_str: str) -> Color:
    """Cold-blue -> neutral-white -> hot-red gradient by Fahrenheit value."""
    try:
        temp = float(temp_str)
    except (TypeError, ValueError):
        return NEUTRAL_COLOR
    if temp <= 70:
        return lerp_color(NEUTRAL_COLOR, COLD_COLOR, (70 - temp) / 50)
    return lerp_color(NEUTRAL_COLOR, HOT_COLOR, (temp - 70) / 30)


class Clock:
    _BG = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

    _TIMEOUT = 5

    _TIMES = {
        1: "One",
        2: "Two",
        3: "Three",
        4: "Four",
        5: "Five",
        6: "Six",
        7: "Seven",
        8: "Eight",
        9: "Nine",
        10: "Ten",
        11: "Eleven",
        12: "Twelve",
        13: "Thirteen",
        14: "Fourteen",
        15: "Fifteen",
        16: "Sixteen",
        17: "Seventeen",
        18: "Eighteen",
        19: "Nineteen",
        20: "Twenty",
        21: "Twenty One",
        22: "Twenty Two",
        23: "Twenty Three",
        24: "Twenty Four",
        25: "Twenty Five",
        26: "Twenty Six",
        27: "Twenty Seven",
        28: "Twenty Eight",
        29: "Twenty Nine",
        30: "Thirty",
        31: "Thirty One",
        32: "Thirty Two",
        33: "Thirty Three",
        34: "Thirty Four",
        35: "Thirty Five",
        36: "Thirty Six",
        37: "Thirty Seven",
        38: "Thirty Eight",
        39: "Thirty Nine",
        40: "Forty",
        41: "Forty One",
        42: "Forty Two",
        43: "Forty Three",
        44: "Forty Four",
        45: "Forty Five",
        46: "Forty Six",
        47: "Forty Seven",
        48: "Forty Eight",
        49: "Forty Nine",
        50: "Fifty",
        51: "Fifty One",
        52: "Fifty Two",
        53: "Fifty Three",
        54: "Fifty Four",
        55: "Fifty Five",
        56: "Fifty Six",
        57: "Fifty Seven",
        58: "Fifty Eight",
        59: "Fifty Nine",
        60: "Sixty",
    }

    _MONTHS = {
        1: "Jan",
        2: "Feb",
        3: "Mar",
        4: "Apr",
        5: "May",
        6: "Jun",
        7: "Jul",
        8: "Aug",
        9: "Sep",
        10: "Oct",
        11: "Nov",
        12: "Dec",
    }

    _WEEKDAYS = {
        0: "Mon",
        1: "Tue",
        2: "Wed",
        3: "Thu",
        4: "Fri",
        5: "Sat",
        6: "Sun",
    }

    def __init__(self):
        self._pixels = self._BG.copy()

        self._session = requests.Session()
        adapter = HTTPAdapter(
            max_retries=Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"],
            )
        )
        self._session.mount("https://", adapter)

        self._forecast = {}
        self._frame_counter = 0  # For smooth pulsing (frame-based, not time-based)

    @property
    def pixels(self) -> PixelDisplay:
        """Return a copy of pixels."""
        return self._pixels

    def start(self):
        """Start the clock's main loop in a separate thread."""
        spawn_daemon(self._main_loop)
        spawn_daemon(self._http_loop)

    def _get(self, url: str, params: dict) -> dict:
        """Generic method to fetch data from an http API."""
        try:
            logger.info(f"GET {url} {params}")
            response = self._session.get(url, params=params, timeout=self._TIMEOUT)
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as http_err:
                logger.info(f"Error {response.status_code}: {response.text}")
                raise http_err

            response_json = response.json()
            logger.info(f"Success {response.status_code}")
            return response_json
        except requests.exceptions.HTTPError as http_err:
            logger.info(f"HTTP error occurred: {http_err}")
        except requests.exceptions.RequestException as req_err:
            logger.info(f"Request exception: {req_err}")
        except Exception as err:
            logger.info(f"Error occurred: {err}")
            raise
        raise ValueError("No data returned from API")

    def _http_loop(self):
        def inner():
            self._forecast = self._get_forecast()

        while True:
            try:
                inner()
            except Exception as err:
                logger.info(f"Error in temperature HTTP loop: {err}")

            time.sleep(120)

    def _parse_temperature(self, data: dict) -> str:
        """
        Parse and interpolate the temperature from the forecast data
        """
        if not data:
            return "-"
        try:
            times = data["hourly"]["time"]
            temps = data["hourly"]["temperature_2m"]
        except KeyError:
            logger.info("Error parsing forecast data")
            return "-"

        time_temps = {
            datetime.datetime.fromisoformat(t).astimezone(datetime.timezone.utc): temp
            for t, temp in zip(times, temps)
        }
        now = datetime.datetime.now(datetime.timezone.utc)

        prev_hour, next_hour = None, None
        for t, temp in time_temps.items():
            if t > now:
                next_hour = t
                break
            elif t < now:
                prev_hour = t
            else:
                return f"{round(temp)}"

        if prev_hour is None or next_hour is None:
            return f"{sum(temps) / len(temps):.0f}"  # Fallback to average temp
        temp_delta = time_temps[next_hour] - time_temps[prev_hour]
        time_delta = now - prev_hour
        time_since = time_delta.total_seconds() / 3600

        interpolated_temp = time_temps[prev_hour] + temp_delta * time_since

        return f"{interpolated_temp:.0f}"

    def _get_forecast(self) -> dict:
        """Fetch the weather forecast. Request it in America/New_York timezone."""
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": 42.365732,
            "longitude": -71.099705,
            "hourly": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "timezone": "GMT",
            "past_days": 2,
            "forecast_days": 2,
        }
        return self._get(url, params)

    def _main_loop(self):
        """Main loop for updating the clock display."""
        # 30ms so the breathing dot (2s period) redraws ~33x/cycle instead of
        # the old ~10x/cycle, which looked jumpy.
        run_periodically(self._tick, interval=0.03)

    def _tick(self) -> None:
        self._frame_counter += 1
        self._pixels = self._clock_pixels()

    def _clock_pixels(self) -> PixelDisplay:
        now = datetime.datetime.now()
        hour = now.hour
        minute = now.minute
        hour_12 = hour % 12 or 12

        d = ""

        if minute == 0:
            lines = [self._TIMES[hour_12], "O'Clock", " ", d]
        else:
            min_word = self._TIMES[minute].split(" ")
            if len(min_word) == 1:
                lines = [
                    self._TIMES[hour_12].lower(),
                    (
                        "o' " + min_word[0].lower()
                        if minute < 10
                        else min_word[0].lower() + "  "
                    ),
                    " ",
                    d,
                ]
            else:
                lines = [
                    self._TIMES[hour_12].lower(),
                    min_word[0].lower(),
                    min_word[1].lower(),
                    d,
                ]

        hour_float = hour + minute / 60 + now.second / 3600
        pixels = self._BG.copy()

        icon = SUN if 7 <= hour_float < 18 else MOON_ICON
        draw_sprite_on(
            pixels, icon, row_start=0, col_start=dimensions.width - icon.width_px
        )

        # Breathing dot in the corner ticks once every 2 seconds, so
        # something on screen always animates even between minute changes.
        # Use frame counter (not time.time()) to avoid wall-clock jitter
        pulse_period_frames = int(2.0 / 0.03)  # 2 seconds / 30ms per frame = 67 frames
        pulse_phase = (self._frame_counter % pulse_period_frames) / pulse_period_frames
        tick = int(255 * (math.sin(2 * math.pi * pulse_phase) + 1) / 2)
        pixels[dimensions.height - 1][0] = (tick, tick, tick)

        draw_lines_on(pixels, lines)

        temperature = self._parse_temperature(self._forecast)
        draw_lines_on(
            pixels,
            [" ", " ", " ", f"-right-{temperature}°F"],
            color=_temperature_color(temperature),
            vertical_shift=1,
        )
        return pixels
