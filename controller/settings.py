from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Where settings live, in order of preference.
#
# Deliberately NOT inside the repo directory: `deploy/deploy.sh` rsyncs with
# `--delete`, so anything sitting in the working tree that isn't in git gets
# wiped on every deploy - settings would silently reset each time you shipped
# a change.
_ENV_VAR = "PI_MOSAIC_SETTINGS"
_SYSTEM_PATH = "/var/lib/pi-mosaic/settings.json"

# rpi-rgb-led-matrix drops root privileges to this user once the hardware is
# initialized (see the privilege-drop note in `Dual`). The settings file is
# created while we're still root, so it has to be handed to that user or
# every later save fails with EACCES. Override if you pass a different
# `--led-drop-priv-user`.
DROP_PRIVILEGES_USER = os.environ.get("PI_MOSAIC_DROP_USER", "daemon")

# Saves are coalesced this long, so dragging a slider doesn't write the file
# once per pixel of travel.
SAVE_DEBOUNCE_SECONDS = 0.75


class Setting:
    """One adjustable value, and everything the web UI needs to render a
    control for it. The schema below is the single source of truth: adding an
    entry there is enough to make a setting appear in the UI, be validated,
    and be persisted."""

    def __init__(
        self,
        key: str,
        label: str,
        kind: str,
        default: Any,
        section: str,
        minimum: Optional[float] = None,
        maximum: Optional[float] = None,
        step: Optional[float] = None,
        choices: Optional[Sequence[str]] = None,
        help: str = "",
    ):
        self.key = key
        self.label = label
        self.kind = kind  # float | int | bool | color | choice
        self.default = default
        self.section = section
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.choices = list(choices) if choices else None
        self.help = help

    def coerce(self, value: Any) -> Any:
        """Force `value` into this setting's type and range.

        Everything arriving here came off an HTTP request or a file on disk,
        so it's untrusted: anything unusable raises `ValueError` and the
        caller keeps the previous value rather than storing junk that would
        crash a render thread later.
        """
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)

        if self.kind == "choice":
            text = str(value)
            if self.choices is None or text not in self.choices:
                raise ValueError(f"{self.key}: {value!r} is not one of {self.choices}")
            return text

        if self.kind == "color":
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ValueError(f"{self.key}: expected [r, g, b], got {value!r}")
            return tuple(max(0, min(255, int(component))) for component in value)

        if self.kind == "int":
            number: float = int(round(float(value)))
        elif self.kind == "float":
            number = float(value)
        else:
            raise ValueError(f"{self.key}: unknown kind {self.kind!r}")

        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{self.key}: {value!r} is not finite")
        # Clamp rather than reject: a slider that sends 101 should land on
        # 100, not leave the board on a stale value.
        if self.minimum is not None:
            number = max(self.minimum, number)
        if self.maximum is not None:
            number = min(self.maximum, number)
        return int(number) if self.kind == "int" else float(number)

    def as_json(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "default": list(self.default) if self.kind == "color" else self.default,
            "section": self.section,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "choices": self.choices,
            "help": self.help,
        }


def _speed(key: str, label: str) -> Setting:
    # No per-row help: the section description below says it once.
    return Setting(
        f"speed.{key}",
        label,
        "float",
        1.0,
        "Page speed",
        minimum=0.1,
        maximum=5.0,
        step=0.05,
    )


# The whole adjustable surface of the board, in display order.
SCHEMA: List[Setting] = [
    # --- Display -----------------------------------------------------------
    Setting(
        "display.brightness",
        "Brightness",
        "int",
        75,
        "Display",
        minimum=1,
        maximum=100,
        step=1,
    ),
    Setting(
        "display.speed",
        "Master speed",
        "float",
        1.0,
        "Display",
        minimum=0.1,
        maximum=5.0,
        step=0.05,
    ),
    Setting(
        "display.web_fps",
        "Web mirror FPS",
        "int",
        30,
        "Display",
        minimum=2,
        maximum=60,
        step=1,
        help="Lower gives the Pi more CPU.",
    ),
    Setting(
        "display.gallery_fps",
        "Gallery FPS",
        "float",
        6.7,
        "Display",
        minimum=0.5,
        maximum=15.0,
        step=0.1,
    ),
    Setting(
        "display.idle_interval",
        "Off-screen page interval",
        "float",
        2.0,
        "Display",
        minimum=0.1,
        maximum=30.0,
        step=0.1,
        help="Higher is cheaper.",
    ),
    # --- Per-page speed ----------------------------------------------------
    _speed("Clock", "Clock"),
    _speed("Ball", "Ball"),
    _speed("Snake", "Snake"),
    _speed("Moon", "Moon phase"),
    _speed("Metaballs", "Metaballs"),
    _speed("LavaLamp", "Lava lamp"),
    _speed("Ticker", "Ticker"),
    _speed("PerlinTerrain", "Terrain"),
    _speed("Tetris", "Tetris"),
    # --- Clock -------------------------------------------------------------
    Setting(
        "clock.pulse_seconds",
        "Breathing dot period",
        "float",
        2.0,
        "Clock",
        minimum=0.2,
        maximum=20.0,
        step=0.1,
    ),
    Setting("clock.show_weather", "Show temperature", "bool", True, "Clock"),
    Setting(
        "clock.units",
        "Temperature units",
        "choice",
        "fahrenheit",
        "Clock",
        choices=["fahrenheit", "celsius"],
    ),
    Setting(
        "clock.latitude",
        "Latitude",
        "float",
        42.365732,
        "Clock",
        minimum=-90.0,
        maximum=90.0,
        step=0.000001,
    ),
    Setting(
        "clock.longitude",
        "Longitude",
        "float",
        -71.099705,
        "Clock",
        minimum=-180.0,
        maximum=180.0,
        step=0.000001,
    ),
    Setting(
        "clock.refresh_minutes",
        "Weather refresh",
        "int",
        2,
        "Clock",
        minimum=1,
        maximum=180,
        step=1,
    ),
    # --- Ball --------------------------------------------------------------
    Setting("ball.size", "Ball size", "int", 4, "Ball", minimum=1, maximum=12, step=1),
    Setting("ball.random_colors", "New colour on each bounce", "bool", True, "Ball"),
    Setting(
        "ball.color",
        "Ball colour",
        "color",
        (255, 127, 127),
        "Ball",
        help="Used when random colours are off.",
    ),
    # --- Snake -------------------------------------------------------------
    Setting("snake.body_color", "Body", "color", (50, 220, 50), "Snake"),
    Setting("snake.head_color", "Head", "color", (90, 120, 190), "Snake"),
    Setting("snake.apple_color", "Apple", "color", (220, 50, 50), "Snake"),
    # --- Moon --------------------------------------------------------------
    Setting(
        "moon.stars",
        "Stars",
        "int",
        18,
        "Moon phase",
        minimum=0,
        maximum=60,
        step=1,
    ),
    Setting(
        "moon.twinkle_speed",
        "Twinkle speed",
        "float",
        1.0,
        "Moon phase",
        minimum=0.0,
        maximum=5.0,
        step=0.05,
    ),
    # --- Metaballs / Lava lamp --------------------------------------------
    Setting(
        "metaballs.blobs",
        "Blobs",
        "int",
        5,
        "Metaballs",
        minimum=1,
        maximum=12,
        step=1,
    ),
    Setting(
        "metaballs.drift",
        "Drift speed",
        "float",
        1.0,
        "Metaballs",
        minimum=0.0,
        maximum=5.0,
        step=0.05,
    ),
    Setting(
        "lava.blobs",
        "Blobs",
        "int",
        4,
        "Lava lamp",
        minimum=1,
        maximum=10,
        step=1,
    ),
    Setting(
        "lava.drift",
        "Drift speed",
        "float",
        1.0,
        "Lava lamp",
        minimum=0.0,
        maximum=5.0,
        step=0.05,
    ),
    # --- Terrain -----------------------------------------------------------
    Setting(
        "terrain.zoom",
        "Zoom",
        "float",
        12.0,
        "Terrain",
        minimum=2.0,
        maximum=40.0,
        step=0.5,
        help="Higher = fewer, larger land masses.",
    ),
    Setting(
        "terrain.scroll_speed",
        "Scroll speed",
        "float",
        1.2,
        "Terrain",
        minimum=0.0,
        maximum=6.0,
        step=0.05,
    ),
    Setting(
        "terrain.octaves",
        "Detail",
        "int",
        4,
        "Terrain",
        minimum=1,
        maximum=6,
        step=1,
        help="Each layer costs CPU.",
    ),
    # --- Tetris ------------------------------------------------------------
    Setting(
        "tetris.fall_seconds",
        "Starting fall interval",
        "float",
        0.14,
        "Tetris",
        minimum=0.02,
        maximum=1.0,
        step=0.01,
    ),
    Setting(
        "tetris.min_fall_seconds",
        "Fastest fall interval",
        "float",
        0.07,
        "Tetris",
        minimum=0.01,
        maximum=1.0,
        step=0.01,
        help="Fastest the game gets.",
    ),
    # --- Ticker ------------------------------------------------------------
    Setting(
        "ticker.rotate_seconds",
        "Seconds per symbol",
        "int",
        6,
        "Ticker",
        minimum=1,
        maximum=120,
        step=1,
    ),
    Setting(
        "ticker.poll_seconds",
        "Quote refresh",
        "int",
        45,
        "Ticker",
        minimum=10,
        maximum=900,
        step=5,
        help="Don't set too low.",
    ),
    # --- Gallery -----------------------------------------------------------
    Setting(
        "gallery.contrast",
        "Contrast",
        "float",
        1.0,
        "Gallery",
        minimum=0.3,
        maximum=2.5,
        step=0.05,
        help="1.0 is the artwork as stored.",
    ),
]

_BY_KEY: Dict[str, Setting] = {setting.key: setting for setting in SCHEMA}

SECTIONS: List[str] = []
for _setting in SCHEMA:
    if _setting.section not in SECTIONS:
        SECTIONS.append(_setting.section)


def default_path() -> str:
    """Where to read and write settings.

    `PI_MOSAIC_SETTINGS` wins. Otherwise use a system location that survives
    a deploy, falling back to a per-user one when that isn't writable (i.e.
    on a dev machine, where we aren't root).
    """
    from_env = os.environ.get(_ENV_VAR)
    if from_env:
        return from_env

    system_dir = os.path.dirname(_SYSTEM_PATH)
    try:
        os.makedirs(system_dir, exist_ok=True)
        if os.access(system_dir, os.W_OK):
            return _SYSTEM_PATH
    except OSError:
        pass

    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "pi-mosaic", "settings.json")


class Settings:
    """Live, persisted, thread-safe settings.

    Reads are lock-free by design: `get` is on the hot path of every render
    thread, so writers swap in a whole new dict rather than making readers
    take a lock. Rebinding an attribute is atomic under the GIL, so a reader
    always sees a complete, self-consistent snapshot - just possibly the
    previous one, which for a brightness slider is entirely fine.
    """

    def __init__(self, path: Optional[str] = None, autosave: bool = True):
        self.path = path if path is not None else default_path()
        self._values: Dict[str, Any] = {s.key: s.default for s in SCHEMA}
        # Bumped on every applied change. Anything caching a render keyed on
        # settings can compare this integer instead of comparing values.
        self.generation = 0
        self._write_lock = threading.Lock()
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._autosave = autosave
        self._save_timer: Optional[threading.Timer] = None
        self._save_failed = False

        self.load()

    # --- reading -----------------------------------------------------------

    def get(self, key: str) -> Any:
        return self._values[key]

    def as_dict(self) -> Dict[str, Any]:
        values = self._values
        return {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in values.items()
        }

    @staticmethod
    def schema_json() -> dict:
        return {
            "sections": SECTIONS,
            "settings": [setting.as_json() for setting in SCHEMA],
        }

    # --- writing -----------------------------------------------------------

    def subscribe(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        """Call `listener(changed)` after any change. Listeners run on the
        caller's thread and must not block."""
        self._listeners.append(listener)

    def update(self, incoming: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """Apply `incoming`, returning `(applied, rejected_keys)`.

        Unknown keys and unusable values are skipped rather than raising, so
        one bad field in a request can't stop the rest from applying.
        """
        applied: Dict[str, Any] = {}
        rejected: List[str] = []

        with self._write_lock:
            updated = dict(self._values)
            for key, raw in incoming.items():
                setting = _BY_KEY.get(key)
                if setting is None:
                    rejected.append(key)
                    continue
                try:
                    value = setting.coerce(raw)
                except (TypeError, ValueError) as err:
                    logger.info(f"Rejected setting {key}={raw!r}: {err}")
                    rejected.append(key)
                    continue
                if updated[key] != value:
                    updated[key] = value
                    applied[key] = value

            if applied:
                self._values = updated  # atomic swap; readers never see a tear
                self.generation += 1

        if applied:
            self._notify(applied)
            self._schedule_save()
        return applied, rejected

    def reset(self) -> Dict[str, Any]:
        """Restore every setting to its default."""
        return self.update({s.key: s.default for s in SCHEMA})[0]

    def _notify(self, changed: Dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(changed)
            except Exception as err:  # a bad listener must not wedge the UI
                logger.info(f"Settings listener failed: {err}")

    # --- persistence -------------------------------------------------------

    def load(self) -> None:
        """Read the settings file, keeping defaults for anything missing.

        A corrupt or unreadable file is logged and ignored rather than
        raising: the board coming up with default settings is much better
        than it not coming up.
        """
        try:
            with open(self.path, "r") as handle:
                stored = json.load(handle)
        except FileNotFoundError:
            logger.info(f"No settings file at {self.path}; using defaults")
            return
        except (OSError, ValueError) as err:
            logger.info(f"Ignoring unreadable settings at {self.path}: {err}")
            return

        if not isinstance(stored, dict):
            logger.info(f"Ignoring settings at {self.path}: expected an object")
            return

        values = dict(self._values)
        for key, raw in stored.items():
            setting = _BY_KEY.get(key)
            if setting is None:
                continue  # a setting from a newer/older build - leave it be
            try:
                values[key] = setting.coerce(raw)
            except (TypeError, ValueError) as err:
                logger.info(f"Ignoring stored setting {key}={raw!r}: {err}")
        self._values = values
        self.generation += 1
        logger.info(f"Loaded settings from {self.path}")

    def _schedule_save(self) -> None:
        if not self._autosave:
            return
        with self._write_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(SAVE_DEBOUNCE_SECONDS, self.save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def save(self) -> bool:
        """Write the settings file atomically. Returns whether it worked.

        Writes to a temporary file in the same directory and `os.replace`s it
        over the target, so a crash or a power cut mid-write leaves the old
        file intact rather than a truncated one - this runs on a board that
        gets unplugged rather than shut down.
        """
        payload = json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", dir=directory, prefix=".settings-", suffix=".tmp", delete=False
            )
            try:
                with handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.path)
            except BaseException:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
                raise
        except OSError as err:
            # Most likely cause on the Pi: the matrix driver dropped root and
            # the file belongs to someone else. Say so once, loudly, and keep
            # running - settings still apply, they just won't survive a
            # restart.
            if not self._save_failed:
                self._save_failed = True
                logger.warning(
                    f"Could not save settings to {self.path}: {err}. "
                    f"Changes will apply but won't persist across a restart. "
                    f"See prepare_settings_file() in controller/settings.py."
                )
            return False

        self._save_failed = False
        logger.info(f"Saved settings to {self.path}")
        return True


def prepare_settings_file(path: str, user: str = DROP_PRIVILEGES_USER) -> None:
    """Make `path` writable by the user we're about to become.

    `rpi-rgb-led-matrix` drops root privileges as soon as the matrix is
    initialized, so by the time anyone touches the settings page the process
    is no longer root and can't create a file under a root-owned directory.
    Call this while still root - before `AdaFruit()` - to create the file and
    directory and hand them over.

    Best-effort by design: on a dev machine we aren't root and there's
    nothing to do, and a failure here only costs persistence, so it must
    never stop the board from starting.
    """
    if os.geteuid() != 0:
        return

    try:
        import pwd

        entry = pwd.getpwnam(user)
    except (ImportError, KeyError):
        logger.info(f"No such user {user!r}; leaving settings ownership alone")
        return

    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "a"):
                pass
        # The directory too: `save` writes a temp file beside the target and
        # renames it, which needs write permission on the directory itself.
        os.chown(directory, entry.pw_uid, entry.pw_gid)
        os.chown(path, entry.pw_uid, entry.pw_gid)
        logger.info(f"Settings at {path} handed to {user} ahead of privilege drop")
    except OSError as err:
        logger.warning(f"Could not prepare {path} for {user}: {err}")


# The instance everything shares.
settings = Settings()
