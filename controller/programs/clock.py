import datetime
import logging
import math
import threading

import numpy as np
import requests
from requests.adapters import HTTPAdapter, Retry

from controller.color import Color, lerp_color
from controller.data import PixelDisplay, dimensions, draw_lines_on
from controller.settings import settings
from controller.sprites import MOON_ICON, SUN, draw_sprite_on
from controller.timing import run_periodically, spawn_daemon

logger = logging.getLogger(__name__)

# The clock's nominal frame interval. The breathing dot's period is expressed
# in these frames, so a page-speed multiplier speeds the dot up along with
# everything else - which is what "make this page faster" should mean.
TICK = 0.03

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

        # Cached render of everything except the breathing dot - see
        # `_clock_pixels`.
        self._face: PixelDisplay = self._BG.copy()
        self._face_minute = None
        self._face_forecast = None
        # Cached `(utc_timestamp, temperature)` pairs for the current
        # forecast, so `_parse_temperature` doesn't re-parse ~96 ISO
        # timestamps every time it's called.
        self._series: list = []
        self._series_source = None
        # Set whenever a setting changes that invalidates the forecast
        # (location or units), so the polling loop refetches straight away
        # instead of up to `clock.refresh_minutes` later.
        self._refetch = threading.Event()
        settings.subscribe(self._on_settings_changed)

    @property
    def pixels(self) -> PixelDisplay:
        """Return a copy of pixels."""
        return self._pixels

    @property
    def has_weather(self) -> bool:
        """Whether a forecast has been successfully fetched."""
        return bool(self._forecast)

    def _on_settings_changed(self, changed: dict) -> None:
        if {"clock.latitude", "clock.longitude", "clock.units"} & set(changed):
            self._refetch.set()

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
        while True:
            success = False
            try:
                self._forecast = self._get_forecast()
                success = True
            except Exception as err:
                logger.info(f"Error in temperature HTTP loop: {err}")

            # If we don't have weather data yet, retry after a short delay (2s)
            # so the board doesn't sit with "-" for minutes after boot.
            timeout = (
                settings.get("clock.refresh_minutes") * 60
                if success and self._forecast
                else 2.0
            )
            self._refetch.wait(timeout=timeout)
            self._refetch.clear()

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

        # The forecast covers four days at hourly resolution, so parsing it
        # means ~96 `fromisoformat` + `astimezone` conversions. It only
        # changes when the HTTP thread swaps in a new response, so do it
        # then rather than on every call.
        if data is not self._series_source:
            self._series = [
                (
                    datetime.datetime.fromisoformat(t).astimezone(
                        datetime.timezone.utc
                    ),
                    temp,
                )
                for t, temp in zip(times, temps)
            ]
            self._series_source = data

        now = datetime.datetime.now(datetime.timezone.utc)

        previous = None  # (timestamp, temperature) of the last hour before now
        following = None  # ...and the first one after it
        for t, temp in self._series:
            if t > now:
                following = (t, temp)
                break
            elif t < now:
                previous = (t, temp)
            else:
                return f"{round(temp)}"

        if previous is None or following is None:
            return f"{sum(temps) / len(temps):.0f}"  # Fallback to average temp
        prev_hour, prev_temp = previous
        next_temp = following[1]
        temp_delta = next_temp - prev_temp
        time_since = (now - prev_hour).total_seconds() / 3600

        interpolated_temp = prev_temp + temp_delta * time_since

        return f"{interpolated_temp:.0f}"

    def _get_forecast(self) -> dict:
        """Fetch the weather forecast for the configured location and units."""
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": settings.get("clock.latitude"),
            "longitude": settings.get("clock.longitude"),
            "hourly": "temperature_2m",
            "temperature_unit": settings.get("clock.units"),
            "timezone": "GMT",
            "past_days": 2,
            "forecast_days": 2,
        }
        return self._get(url, params)

    def _main_loop(self):
        """Main loop for updating the clock display."""
        # 30ms so the breathing dot redraws ~33x per cycle instead of the old
        # ~10x, which looked jumpy.
        run_periodically(self._tick, interval=TICK, owner=self)

    def _tick(self) -> None:
        self._frame_counter += 1
        self._pixels = self._clock_pixels()

    def _clock_pixels(self) -> PixelDisplay:
        now = datetime.datetime.now()

        # Only the breathing dot changes from frame to frame. The words, the
        # sun/moon icon and the temperature readout change at most once a
        # minute, but re-rendering them 33 times a second - font lookups,
        # per-glyph pixel writes, and a full re-read of the hourly forecast -
        # was nearly all of this program's cost, all of it spent redrawing a
        # picture identical to the last one. Render the face when it actually
        # changes and stamp the dot onto a copy the rest of the time.
        # `settings.generation` is in the key so that editing anything on the
        # settings page redraws the face immediately, rather than leaving a
        # stale one on screen until the minute rolls over.
        face_key = (now.hour, now.minute, settings.generation)
        if face_key != self._face_minute or self._forecast is not self._face_forecast:
            self._face_minute = face_key
            self._face_forecast = self._forecast
            self._face = self._render_face(now)

        pixels = self._face.copy()

        # Breathing dot in the corner ticks once every 2 seconds, so
        # something on screen always animates even between minute changes.
        # Use frame counter (not time.time()) to avoid wall-clock jitter
        pulse_period_frames = max(
            1, int(round(settings.get("clock.pulse_seconds") / TICK))
        )
        pulse_phase = (self._frame_counter % pulse_period_frames) / pulse_period_frames
        tick = int(255 * (math.sin(2 * math.pi * pulse_phase) + 1) / 2)
        pixels[dimensions.height - 1][0] = (tick, tick, tick)

        return pixels

    def _render_face(self, now: datetime.datetime) -> PixelDisplay:
        """Everything on the clock page except the breathing dot."""
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

        draw_lines_on(pixels, lines)

        if settings.get("clock.show_weather"):
            temperature = self._parse_temperature(self._forecast)
            if temperature != "-":
                degrees = "°C" if settings.get("clock.units") == "celsius" else "°F"
                draw_lines_on(
                    pixels,
                    [" ", " ", " ", f"-right-{temperature}{degrees}"],
                    color=_temperature_color(temperature),
                    vertical_shift=1,
                )
        return pixels
