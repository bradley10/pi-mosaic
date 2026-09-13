# AGENTS.md

Reference for working in this repo — the mental model, the conventions, and
the sharp edges. Read this before adding a feature; it should save you from
re-deriving things the hard way.

## What this is

Controller software for a 64x32 RGB LED matrix on a Raspberry Pi
(`rpi-rgb-led-matrix`). It cycles through small "programs" (pages) - a
clock, a self-playing snake, ambient animations, a price ticker, a painting
gallery, etc. On the Pi it drives the physical matrix **and** a web page
mirroring it at the same time, and the web page's buttons are the only
input (no physical buttons currently wired up).

## Orientation

```
controller/
  __init__.py           Controller: owns all Program instances, the main loop
  __main__.py            `python -m controller` entry point
  data.py                 dimensions, PixelDisplay type, font + draw_lines_on
  color.py                Color type, lerp_color, color_from_hsv, hue_shift, pulse
  sprites.py               multi-color pixel-art icons (Sprite/draw_sprite_on)
  blob_field.py            vectorized metaball-field renderer (Metaballs/LavaLamp)
  timing.py                spawn_daemon, run_periodically
  http_client.py           requests.Session with retry/backoff, for polling APIs
  assets/paintings/*.npy   static frame data for the Painting program
  programs/                one file per page - see "Adding a new program" below
  displays/                Simulate (web UI), AdaFruit (real hardware), Dual (both)
  test/                    pytest suite
deploy/
  mbta-tracker.service     systemd unit installed on the Pi
  deploy.sh                rsync + reinstall deps + restart service
scripts/
  fetch_paintings.py       one-off script to add/update gallery artwork assets
requirements-pi.txt        pinned deps for the Pi's stock Python 3.9 (see below)
```

## The core loop

`Controller` (`controller/__init__.py`) owns one instance of every program,
puts them in a list, and runs a tight loop: read the active program's
`.pixels`, push it to the display if it changed, check for button input,
repeat. It doesn't know or care what any individual program does internally.

## The `Program` protocol

Every page implements (`controller/programs/__init__.py`):

```python
class Program(Protocol):
    @property
    def pixels(self) -> PixelDisplay: ...   # current frame, read every ~5ms
    def start(self) -> None: ...             # kick off background thread(s)
```

Programs are **self-driving**: `start()` spawns a daemon thread (or two -
one for rendering, one for polling an API) that updates `self._pixels` on
its own schedule, independent of the main loop's poll rate. The main loop
never calls into program logic beyond reading `.pixels`.

Optional: a `display_name` property labels the program in the web UI's
"Scroll All Pages" gallery view instead of the class name (see
`Painting.display_name` in `gallery.py` - useful when you have several
instances of the same class, like one `Painting` per artwork).

### Adding a new program

1. Create `controller/programs/your_thing.py`. Look at `controller/programs/ball.py`
   for the simplest possible example, or `moon.py` for one with a static
   background + a font readout, or `metaballs.py`/`lava_lamp.py` for the
   blob-field pattern, or `ticker.py`/`clock.py` for one that polls an
   HTTP API in a second thread.
2. Give it `pixels` (a property returning a `(32, 64, 3)` int32 numpy array)
   and `start()`.
3. Register it in `controller/__init__.py`: construct it in
   `Controller.__init__`, add it to the `programs` list in `_main_loop`.
   That's it - paging, the gallery view, and button routing all work
   automatically once it's in that list.
4. If it needs to load a big/static asset (like the paintings), do that at
   startup in `__init__`, before `Dual()`/`AdaFruit()` are constructed - see
   the privilege-drop note below for why order matters.

A minimal program body:

```python
import numpy as np
from controller.data import PixelDisplay, dimensions
from controller.timing import run_periodically, spawn_daemon

class YourThing:
    def __init__(self):
        self._pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self):
        spawn_daemon(self._main_loop)

    def _main_loop(self):
        run_periodically(self._step, interval=0.05)  # pick a rate; see "Timing" below

    def _step(self):
        self._pixels = self._render()

    def _render(self) -> PixelDisplay:
        pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)
        # ... draw here ...
        return pixels
```

## Shared building blocks

- **`controller/data.py`**: `dimensions` (64x32), `PixelDisplay` (numpy array
  type alias), a built-in bitmap font, and `draw_lines_on(pixels, lines,
  color=..., vertical_shift=..., horizontal_shift=...)` for text. Lines can
  be prefixed `-left-`, `-right-`, or `-center-` for alignment. `word_width`
  computes a string's pixel width if you need to position something next to
  text (see `ticker.py`'s arrow icon placement for an example).
  `validate_pixels` is a decorator that asserts an array is the right
  shape/dtype - most `display_matrix` implementations use it.
- **`controller/color.py`**: `Color = Tuple[int,int,int]`, `lerp_color`,
  `color_from_hsv`, `hue_shift`, `pulse(t, period)` (smooth 0-1 breathing
  oscillation - used for the clock's corner dot).
- **`controller/sprites.py`**: `Sprite` (multi-color pixel art, vs. the
  font's single-color glyphs) + `draw_sprite_on`. Has mirror/rotate helpers
  and a shared starfield background. Note: several sprites here (planes,
  boats, DNA helix, coin, satellite) were built for programs that have
  since been removed and aren't used by anything currently active - don't
  assume every sprite here is wired up.
- **`controller/blob_field.py`**: `render_field(blobs, bands, background)` -
  the metaball math shared by Metaballs and LavaLamp. Vectorized with numpy
  (grid-wide array ops, not a per-pixel Python loop) since it runs every
  frame; if you add a similar field-based effect, reuse this rather than
  writing a new per-pixel loop - see "Timing and performance" below for why
  that matters.
- **`controller/timing.py`**: `spawn_daemon(fn)` starts a daemon thread.
  `run_periodically(fn, interval)` calls `fn` in a loop, sleeping just
  enough to keep calls `interval` apart - it does **not** correct for
  drift, so if `fn` occasionally overruns `interval` the next call just
  starts immediately rather than trying to catch up.
- **`controller/http_client.py`**: `make_session()` - a `requests.Session`
  with retry/backoff already configured. Use this instead of bare
  `requests` for anything that polls an external API (see `clock.py`,
  `ticker.py`).

## Static assets (the paintings pattern)

`controller/programs/gallery.py` shows the pattern for a program whose
frames are precomputed rather than rendered live: `scripts/fetch_paintings.py`
fetches/crops/downsizes source images on a dev machine and writes each as a
`.npy` file under `controller/assets/paintings/`, committed to the repo. The
board just does `np.load()` at startup - no network access or image decoder
needed at runtime (the Pi's Pillow build can't even identify JPEGs - see git
history). Follow this pattern for anything else that's expensive/impossible
to compute on the Pi but fine to precompute once: add the source to the
script, run it on your dev machine, commit the output.

## Display backends (`controller/displays/`)

- **`Simulate`**: a web UI (plain `http.server`, no framework) serving the
  live frame over Server-Sent Events, plus NEXT/BACK buttons and a
  scrollable gallery view where clicking a page jumps straight to it.
  Exposes `button_a_index`/`button_b_index` (monotonic counters) and
  `take_pending_select()` (pops the most-recently-clicked gallery index, or
  `None`). Used standalone for `make sim` (laptop dev, no hardware).
- **`AdaFruit`**: drives the real matrix via `rpi-rgb-led-matrix`. Parses a
  large set of `--led-*` CLI flags (hardware mapping, brightness, GPIO
  slowdown, etc.) into `RGBMatrixOptions`.
- **`Dual`**: used on the Pi. Constructs both of the above and forwards
  `display_matrix`/`display_gallery` to each, so the physical board and the
  web mirror always show the same thing. `button_a_index`/`button_b_index`/
  `take_pending_select()` proxy straight to `Simulate` (web UI is the only
  input right now).

### The privilege-drop gotcha in `Dual`

**`AdaFruit`'s `RGBMatrix` drops root privileges by default** once the
hardware is initialized (`rpi-rgb-led-matrix`'s own behavior; see
`--led-no-drop-privs` in `adafruit.py`). The systemd service runs as root
specifically so the matrix driver can get raw GPIO/memory access - but
after `AdaFruit()` is constructed, the process is no longer root.

**Anything that still needs root privileges must be constructed *before*
`AdaFruit`.** This has already bitten us twice: binding port 80 (`<1024`
needs privilege) crash-looped with `PermissionError: [Errno 13]` until
`Simulate` was moved ahead of `AdaFruit` in `Dual.__init__`; physical GPIO
button setup silently failed the same way before it was removed entirely.
If you add anything to `Dual.__init__` (or to `Controller.__init__` before
it) that touches privileged resources, put it before the `AdaFruit()` line
and say why in a comment, or it'll work in every manual test and then fail
specifically when run for real as the systemd service.

## Input model

Programs never read input directly. `Controller._main_loop` reads
`self.keyboard.button_a_index`/`button_b_index` (NEXT/BACK, monotonic
counters - the loop diffs them against last-seen values so multiple presses
between ticks aren't lost) and, if the display supports it,
`take_pending_select()` for gallery-view clicks. Add a new input source by
exposing the same interface, not by having a program poll something itself.

## Timing and performance

- `run_periodically`'s docstring is not a suggestion: pick an `interval`
  with real headroom over what the render actually costs, especially since
  the Pi is much slower than a dev machine. Benchmark with something like:
  ```sh
  uv run python -c "
  import time
  from controller.programs.your_thing import YourThing
  t = YourThing()
  start = time.perf_counter()
  for _ in range(200): t._step()
  print((time.perf_counter() - start) / 200 * 1000, 'ms/step')
  "
  ```
- Per-pixel Python loops over the full 32x64 grid are the main way a
  program ends up needing a suspiciously slow interval to avoid jitter -
  vectorize with numpy instead (see `blob_field.render_field`'s history:
  the original nested-loop version needed ~0.08-0.12s intervals; the
  vectorized version benchmarks at ~0.07ms/frame).
- Don't call the same expensive method twice per tick to "check, then use
  the result" (`if expensive(): return expensive()`) - compute once, store
  it, reuse it. This was a real bug in `Snake.set_path()`.
- The main loop only pushes a frame to the display when `pixels` actually
  changed (`np.array_equal`), so a program that redraws identical frames
  costs nothing extra there - but its own thread still burns CPU on the
  unnecessary redraw, so don't over-tighten `interval` "just in case."

## Two Python environments - keep new code 3.9-compatible

Local dev uses `uv`/`pyproject.toml`/`uv.lock` against a modern Python
(3.10+). The Pi runs its stock Raspbian Bullseye Python **3.9** (Bullseye
only ships 3.8/3.9, and the compiled `rgbmatrix` hardware bindings are tied
to that specific interpreter - upgrading it would mean recompiling
`rpi-rgb-led-matrix`). So:

- Every file needs `from __future__ import annotations` at the top if it
  uses `X | Y` union syntax anywhere (annotations or otherwise) - that's
  3.10+ only without it. Check any new file for this.
- `typing.ParamSpec` is 3.10+; `controller/data.py` has the fallback
  pattern (`try: from typing import ParamSpec / except ImportError: from
  typing_extensions import ParamSpec`) if you need it elsewhere.
- New runtime dependencies need a piwheels-compatible pin added to
  `requirements-pi.txt` (separate from `pyproject.toml`, which targets the
  newer dev Python) - check availability first:
  ```sh
  ssh pi@raspberrypi "python3 -m pip download --no-deps -d /tmp/check <package>==<version>"
  ```
  Also check whether the version needs a system shared library that isn't
  installed (e.g. Pillow's wheel needed `libopenjp2-7`, numpy's needed
  `libopenblas0` - both had to be `apt install`ed on the Pi; look for
  `ImportError: lib*.so.*: cannot open shared object file` when testing).
- The Pi's venv was created with `--system-site-packages` so it can see the
  system-wide `rgbmatrix` install; don't recreate it without that flag.

## Dev workflow

```sh
make develop   # uv sync
make sim       # simulate mode: web UI only, no hardware needed
make run       # real hardware mode - only works on the Pi
make lint      # ruff check + pyright
make format    # ruff format
make test      # pytest
```

Test patterns: `controller/test/test_data.py` for pure-function stuff,
`controller/test/test_simulate.py` for hitting `Simulate`'s HTTP endpoints
directly with a `port=0` fixture (OS picks a free port) - follow that
pattern for anything display-protocol-related rather than trying to test
against real hardware.

## Deploying to the Pi

`make deploy` (or `./deploy/deploy.sh [host]`) rsyncs the repo to
`/home/pi/mbta-tracker` (deleting anything there that's no longer in the
repo), installs `requirements-pi.txt` into the existing venv, and restarts
the systemd service. See the README's "Deploying to a Raspberry Pi" section
for SSH access, Wi-Fi setup, boot/systemd details, and log commands
(`sudo journalctl -u mbta-tracker -f`) - this file is about the code, that
one's about the device.
