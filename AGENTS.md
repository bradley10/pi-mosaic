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
  pi-mosaic.service        systemd unit installed on the Pi
  deploy.sh                rsync + reinstall deps + restart service
scripts/
  fetch_paintings.py       one-off script to add/update gallery artwork assets
requirements-pi.txt        pinned deps for the Pi's stock Python 3.9 (see below)
```

## The core loop

`Controller` (`controller/__init__.py`) owns one instance of every program,
puts them in a list, and runs a loop paced to `FRAME_INTERVAL` (60Hz): read
the active program's `.pixels`, push it to the display if it changed, check
for button input, repeat. It doesn't know or care what any individual program
does internally.

It also tells `render_gate` which program is on screen and whether anyone has
the web gallery open, which is what keeps the other twelve programs from
eating the interpreter - see "Timing and performance".

## The `Program` protocol

Every page implements (`controller/programs/__init__.py`):

```python
class Program(Protocol):
    @property
    def pixels(self) -> PixelDisplay: ...  # current frame, read every ~5ms
    def start(self) -> None: ...  # kick off background thread(s)
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
        self._pixels = np.zeros(
            (dimensions.height, dimensions.width, 3), dtype=np.int32
        )

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self):
        spawn_daemon(self._main_loop)

    def _main_loop(self):
        # `owner=self` lets the render gate throttle this while it's off
        # screen - see "Timing and performance" below. Always pass it from a
        # render loop; never from an API-polling loop.
        run_periodically(self._step, interval=0.05, owner=self)  # see "Timing" below

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
  `run_periodically(fn, interval, owner=None)` calls `fn` on a fixed
  schedule, correcting for `time.sleep` overshoot so the period doesn't
  wander; if `fn` overruns its budget the missed slots are dropped rather
  than fired back-to-back. `render_gate` (a `RenderGate`) is the shared
  object that decides how fast each program is *allowed* to render.
- **`controller/http_client.py`**: `make_session()` - a `requests.Session`
  with retry/backoff already configured. Use this instead of bare
  `requests` for anything that polls an external API (see `clock.py`,
  `ticker.py`).

## Settings (`controller/settings.py`)

Everything the user can adjust from the web page's Settings tab lives in one
declarative `SCHEMA` list. **Adding a setting is one `Setting(...)` entry plus
a `settings.get("your.key")` where it's used** - the web control, type
coercion, range clamping, persistence and the cross-tab sync all fall out of
that entry. Don't add bespoke config anywhere else.

```python
Setting("ball.size", "Ball size", "int", 4, "Ball", minimum=1, maximum=12, step=1)
#        key         label        kind  default section  ...constraints
```

Kinds: `int`, `float`, `bool`, `color` (an `[r, g, b]` triple), `choice`
(needs `choices=`). `SECTION_HELP` adds a one-off note under a section
heading, for anything that would otherwise repeat on every row.

Things worth knowing before you touch this:

- **Reads are lock-free and on the hot path.** `settings.get(key)` is a dict
  lookup on an immutable snapshot; writers build a new dict and rebind it, so
  a render thread never blocks and never sees a half-applied change. Don't add
  a lock to the read path.
- **Values arriving from the web are untrusted.** `Setting.coerce` clamps
  numbers into range and rejects anything unusable, so a bad request can never
  put a value on a render thread that crashes it. A rejected key leaves the
  previous value alone and the rest of the request still applies.
- **Reading settings inside a render loop is fine, but cache what's
  expensive.** `settings.generation` is an integer bumped on every change -
  key a cached render off it rather than re-rendering per frame (see
  `Clock._clock_pixels`).
- **Saves are debounced and atomic.** Dragging a slider fires a change per
  pixel of travel; those coalesce into one write, done via a temp file plus
  `os.replace` so an unplugged board can't leave a truncated file.
- **The file lives outside the repo.** `deploy.sh` rsyncs with `--delete`, so
  a settings file in the working tree would be deleted on every deploy.
  `/var/lib/pi-mosaic/settings.json` on the Pi,
  `~/.local/state/pi-mosaic/settings.json` for dev, `PI_MOSAIC_SETTINGS` to
  override.
- **Saving has to survive the privilege drop.** The matrix driver drops root
  right after `AdaFruit()` is constructed, but every save happens later, on
  the HTTP thread. `Dual.__init__` calls `prepare_settings_file()` while still
  root to hand the file and its directory to the drop-target user - the same
  ordering constraint as binding port 80. If saving fails anyway it logs once
  and keeps going with in-memory settings rather than taking the board down.
- **A settings file from another build must still load.** Unknown keys are
  ignored and unusable values fall back to defaults, so rolling the code
  forward or back never bricks startup.

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

Two sharp edges in that script:

- **Rebuild one painting, not all six.** Pass titles
  (`... fetch_paintings.py "The Starry Night"`). Wikimedia regenerates its
  server-side thumbnails periodically, so a bare re-run quietly rewrites every
  `.npy` with a slightly different rendering of artwork you didn't mean to
  touch.
- **Watch the edge columns.** Museum scans often include a little unpainted
  canvas or frame edge. One column is 1.5% of a 64-wide frame, so a fringe far
  too thin to notice in the source becomes one conspicuously bright column on
  the board - The Starry Night's scan fades from luma 139 at its border to the
  interior's 89 over ten of 960 columns, and downsized that was a +37 spike on
  column 0. The per-source `inset` trims it. A genuine composition doesn't
  jump 30+ points in one column and drop straight back, which is what the
  fringe test in `test_gallery.py` keys on.

### The catch: static frames and the identity check

The main loop only pushes to the display when `frame is not last_frame` (see
"The core loop"). Every other program republishes a brand-new array each
frame, so that works out - but a page holding one array forever is pushed
exactly once, and *any* later change that should alter what's on the panel
silently never arrives. Two things had to be threaded around it:

- **Contrast** (`gallery.contrast`) is pixel data, so `Painting` subscribes to
  settings and rebuilds `_pixels` as a new array on change. It always
  recomputes from a pristine `_source`, never on top of the current frame, or
  dragging the slider would progressively cook the image.
- **Brightness** (`display.brightness`) is not pixel data - it's the driver's
  PWM duty cycle, applied inside `AdaFruit.display_matrix`, and the web mirror
  likewise only re-encodes a frame it's just been handed. So the main loop
  folds brightness into the change test and re-pushes the current frame when
  it moves. Fixing it in `Painting` instead would have missed the web mirror
  and any other static page.

If you add a program whose frame can sit unchanged, this is the trap.

## Display backends (`controller/displays/`)

- **`Simulate`**: a web UI (plain `http.server`, no framework) serving the
  live frame over Server-Sent Events, plus NEXT/BACK buttons and a
  scrollable gallery view where clicking a page jumps straight to it.
  Frames go over the wire as base64 of raw RGB bytes, not nested JSON
  arrays - see `_encode`. `display_matrix`/`display_gallery` only stash the
  frame; a broadcaster thread does the encoding and socket writes, and both
  are no-ops when no browser is connected. `has_viewers`/`has_gallery_viewers`
  expose that.
  Exposes `button_a_index`/`button_b_index` (monotonic counters) and
  `take_pending_select()` (pops the most-recently-clicked gallery index, or
  `None`). Used standalone for `task sim` (laptop dev, no hardware).
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

This is the part of the codebase most likely to bite you, because nothing
here fails loudly - it just makes the board look bad.

**The GIL is the budget.** There are ~14 program threads plus the main loop,
and CPython runs exactly one of them at a time. Work done on *any* thread is
work stolen from whatever is currently on the board. A program that costs
"only" 1ms a frame at 33Hz is spending 3% of the entire machine, forever,
including while nobody can see it.

**The render gate is what makes that affordable.** `render_gate`
(`controller/timing.py`) knows which program `Controller` has on screen and
whether a browser has the gallery open, and `run_periodically` consults it:

- the on-screen program renders at its own `interval`;
- off-screen programs render at the gallery's rate (~0.15s) while someone is
  watching the gallery, and at `IDLE_INTERVAL` (2s) when nobody is;
- a program that's just been put on screen is woken immediately rather than
  sitting out the rest of an idle sleep.

The gate is also where the settings page's per-page speed multipliers are
applied (`speed.<ClassName>` x `display.speed`), so a program gets both for
free by opting in.

So **pass `owner=self` from a program's render loop**, and **don't** pass it
from an API-polling loop (those must keep running regardless of what's on
screen; give those a callable `interval` if the rate is itself a setting). Forgetting `owner=self` doesn't break anything visibly - the program
just quietly burns CPU forever, which is exactly the failure this was added
to fix.

Because every program animates off a frame counter rather than wall-clock
time, throttling makes an off-screen program animate *slower* rather than
skip - switching to it never lands mid-jump.

**Nothing expensive belongs on the main loop's thread.** It's the thread that
pushes to the physical matrix, so anything slow there is visible stutter.
That's why `Simulate` stashes frames for a broadcaster thread instead of
encoding them inline, and why the main loop skips gathering gallery frames
when no browser is watching.

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
  changed, so a program that redraws identical frames costs nothing extra
  there - but its own thread still burns CPU on the unnecessary redraw, so
  don't over-tighten `interval` "just in case." That check is `is`, not
  `np.array_equal`: **programs must publish a brand-new array each frame
  rather than mutating `self._pixels` in place.** Every program already does,
  and the main loop and the simulator both hold references to the array you
  publish, so mutating one in place would tear frames as well as defeat the
  check.
- Cache anything that changes more slowly than the frame rate. `Clock` only
  re-renders its words, icon and temperature when the minute (or the
  forecast) actually changes, and stamps the breathing dot onto a copy the
  rest of the time - that alone took it from 0.13ms to 0.002ms a frame.

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
task develop   # uv sync
task sim       # simulate mode: web UI only, no hardware needed
task run       # real hardware mode - only works on the Pi
task lint      # ruff check + pyright
task format    # ruff format
task test      # pytest
```

Test patterns: `controller/test/test_data.py` for pure-function stuff,
`controller/test/test_simulate.py` for hitting `Simulate`'s HTTP endpoints
directly with a `port=0` fixture (OS picks a free port) - follow that
pattern for anything display-protocol-related rather than trying to test
against real hardware.

## Deploying to the Pi

`task deploy` (or `./deploy/deploy.sh [host]`) rsyncs the repo to
`/home/pi/pi-mosaic` (deleting anything there that's no longer in the
repo), installs `requirements-pi.txt` into the existing venv, and restarts
the systemd service. See the README's "Deploying to a Raspberry Pi" section
for SSH access, Wi-Fi setup, boot/systemd details, and log commands
(`sudo journalctl -u pi-mosaic -f`) - this file is about the code, that
one's about the device.
