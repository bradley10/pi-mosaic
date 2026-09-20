from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Union

from controller.data import Duration
from controller.settings import settings

# Fallback for when no `display.idle_interval` setting is available (a gate
# constructed directly in a test). How often an off-screen program redraws
# when nothing is watching it at all - no browser has the gallery view open.
# It still ticks, so its thumbnail isn't stale the instant someone opens the
# gallery, but at a rate that costs essentially nothing.
IDLE_INTERVAL = 2.0


def spawn_daemon(target: Callable[[], None]) -> threading.Thread:
    """Run `target` in a daemon thread and return it, already started."""
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


class RenderGate:
    """Decides how often each program is *allowed* to redraw.

    Only one program is on screen at a time, but every program drives its own
    thread and, left alone, every one of them renders flat out forever. On the
    Pi that's the whole problem: CPython's GIL means only one of those ~14
    threads executes bytecode at a time, so the twelve programs nobody can see
    were stealing most of the interpreter from the one program on the board.
    That's what made the clock's breathing dot stutter and the ball and snake
    speed up and slow down - not any single slow render, but contention.

    Off-screen programs still need to redraw, but only as fast as the web
    gallery's thumbnails actually refresh - and not even that when no browser
    has the gallery open. `Controller` keeps this gate up to date; programs
    opt in by passing `owner=self` to `run_periodically`.

    Because every program animates off a frame counter rather than wall-clock
    time, throttling makes an off-screen program animate *slower* rather than
    skip ahead, so switching to it never lands mid-jump.
    """

    def __init__(self, idle_interval: Optional[Duration] = None):
        # `None` means "follow the `display.idle_interval` setting"; tests
        # pass an explicit value to pin it.
        self._idle_interval = idle_interval
        self._condition = threading.Condition()
        self._active: object = None
        # None means "nobody is watching the gallery", i.e. idle.
        self._background_interval: Optional[Duration] = None
        # Bumped on every state change; sleeping threads watch it so a
        # program that just became visible wakes up now instead of sitting
        # out the rest of a two-second idle sleep.
        self._generation = 0

    def set_active(self, owner: object) -> None:
        """Mark `owner` as the program currently on the board."""
        with self._condition:
            if owner is self._active:
                return
            self._active = owner
            self._bump()

    def set_background_interval(self, interval: Optional[Duration]) -> None:
        """Set how often off-screen programs may redraw, or `None` to idle
        them (nobody has the gallery open)."""
        with self._condition:
            if interval == self._background_interval:
                return
            self._background_interval = interval
            self._bump()

    def _bump(self) -> None:
        """Wake everyone sleeping on the gate. Caller holds the lock."""
        self._generation += 1
        self._condition.notify_all()

    @staticmethod
    def speed_for(owner: object) -> float:
        """The user's speed multiplier for `owner`, from the settings page.

        A program opts in just by having a `speed.<ClassName>` entry in the
        settings schema; anything else runs at 1x. Both multipliers are
        clamped well away from zero by the schema, so this can't stall a
        program outright.
        """
        if owner is None:
            return 1.0
        try:
            own = settings.get("speed." + type(owner).__name__)
        except KeyError:
            own = 1.0
        return float(own) * float(settings.get("display.speed"))

    def idle_interval(self) -> Duration:
        if self._idle_interval is not None:
            return self._idle_interval
        return float(settings.get("display.idle_interval"))

    def interval_for(self, owner: object, interval: Duration) -> Duration:
        """Return the interval `owner` should actually render at, given its
        preferred `interval`. `owner=None` opts out of throttling entirely -
        used by HTTP polling loops, which have nothing to do with what's on
        screen."""
        interval = interval / self.speed_for(owner)
        if owner is None or owner is self._active:
            return interval
        background = self._background_interval
        if background is None:
            return self.idle_interval()
        # Never *speed a program up* past the rate it asked for.
        return max(interval, background)

    def sleep(self, owner: object, seconds: Duration) -> bool:
        """Sleep for `seconds`, returning early if the gate changes state.

        Returns True if it was woken early, meaning the caller's schedule is
        no longer meaningful and should be restarted from now.
        """
        if seconds <= 0:
            return False
        # The active program is on the hot path and is never waiting for a
        # state change to be useful - keep it on a plain sleep rather than
        # taking a lock 30+ times a second.
        if owner is None or owner is self._active:
            time.sleep(seconds)
            return False
        with self._condition:
            generation = self._generation
            return self._condition.wait_for(
                lambda: self._generation != generation, timeout=seconds
            )


# The single gate shared by every program and by `Controller`.
render_gate = RenderGate()


def run_periodically(
    fn: Callable[[], None],
    interval: Union[Duration, Callable[[], Duration]],
    owner: object = None,
    gate: Optional[RenderGate] = None,
) -> None:
    """Call `fn` forever, keeping each call `interval` apart.

    `interval` may be a callable returning the current interval, for rates
    the user can change from the settings page - it's re-read every tick, so
    a new value takes effect on the next one.

    Schedules against a running deadline rather than sleeping `interval`
    minus the time `fn` took. The difference matters: `time.sleep` only
    guarantees a *minimum*, and under GIL contention it routinely overshoots
    by several milliseconds. Re-deriving the next wake-up from the last
    deadline absorbs that overshoot instead of letting it accumulate into
    visible speeding-up and slowing-down.

    If `fn` overruns its budget the missed slots are dropped rather than
    fired back-to-back, so a slow frame never turns into a burst of
    catch-up frames.

    Pass `owner=self` from a program's render loop to let `render_gate`
    throttle it while it's off screen; leave it `None` for work that must
    run regardless of what's on the board, like API polling.
    """
    gate = render_gate if gate is None else gate
    resolve = interval if callable(interval) else (lambda: interval)
    next_call = time.monotonic()
    while True:
        fn()

        next_call += gate.interval_for(owner, resolve())
        now = time.monotonic()
        if next_call <= now:
            next_call = now
            continue
        if gate.sleep(owner, next_call - now):
            # Woken early because visibility changed - render immediately and
            # restart the schedule from here.
            next_call = time.monotonic()
