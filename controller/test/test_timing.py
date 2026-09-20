import statistics
import threading
import time

import pytest

from controller.timing import RenderGate, run_periodically


class _Stop(Exception):
    pass


def _collect(gate, owner, interval, count):
    """Run `run_periodically` until it has fired `count` times, returning the
    timestamp of each call."""
    calls = []

    def step():
        calls.append(time.monotonic())
        if len(calls) == count:
            raise _Stop

    with pytest.raises(_Stop):
        run_periodically(step, interval=interval, owner=owner, gate=gate)
    return calls


def test_run_periodically_calls_fn_repeatedly_and_propagates_errors():
    calls = []

    def step():
        calls.append(time.monotonic())
        if len(calls) == 3:
            raise _Stop

    with pytest.raises(_Stop):
        run_periodically(step, interval=0.01)

    assert len(calls) == 3


def _sleep_overshoot(target=0.02, samples=20):
    """How much longer than asked for `time.sleep` actually takes here.

    This is the thing `run_periodically` exists to cancel out, and it varies
    a lot by platform - several milliseconds on macOS for a 20ms sleep. Tests
    that assert on timing have to be scaled against it rather than against a
    hard-coded budget, or they're just flaky.
    """
    total = 0.0
    for _ in range(samples):
        start = time.monotonic()
        time.sleep(target)
        total += time.monotonic() - start - target
    return total / samples


def test_run_periodically_does_not_accumulate_sleep_overshoot():
    """`time.sleep` reliably overshoots. Sleeping `interval` each time makes
    every frame a little late, forever - which is an animation running slow.
    The schedule has to claw that back instead."""
    calls = []
    interval = 0.02
    frames = 12

    def step():
        calls.append(time.monotonic())
        if len(calls) == frames:
            raise _Stop

    with pytest.raises(_Stop):
        run_periodically(step, interval=interval)

    slots = len(calls) - 1
    elapsed = calls[-1] - calls[0]
    nominal = interval * slots
    # What it would cost to never correct: one overshoot per slot. The
    # corrected schedule has to land nearer the nominal time than that.
    uncorrected = nominal + _sleep_overshoot(interval) * slots
    assert elapsed < (nominal + uncorrected) / 2, (
        f"{elapsed:.4f}s elapsed; nominal {nominal:.4f}s, "
        f"uncorrected would be ~{uncorrected:.4f}s"
    )


def test_run_periodically_absorbs_a_slow_call_instead_of_drifting():
    """One expensive frame shouldn't push every later frame back. That
    accumulating lateness is what an animation slowing down actually is."""
    interval = 0.02
    frames = 12

    def run(slow_frame):
        calls = []

        def step():
            calls.append(time.monotonic())
            if slow_frame and len(calls) == 2:
                time.sleep(interval * 0.6)  # still within its own slot
            if len(calls) == frames:
                raise _Stop

        with pytest.raises(_Stop):
            run_periodically(step, interval=interval)
        return calls[-1] - calls[0]

    baseline = run(slow_frame=False)
    with_slow_frame = run(slow_frame=True)

    # The slow frame is paid for out of its own slot, so the run as a whole
    # finishes in about the same time as one with no slow frame at all.
    assert with_slow_frame < baseline + interval


def test_run_periodically_drops_missed_slots_rather_than_bursting():
    """Overrunning badly should cost frames, never produce a catch-up burst -
    a burst is what makes an animation visibly jump."""
    calls = []
    interval = 0.01

    def step():
        calls.append(time.monotonic())
        if len(calls) == 2:
            time.sleep(interval * 5)  # blow the budget wide open
        if len(calls) == 6:
            raise _Stop

    with pytest.raises(_Stop):
        run_periodically(step, interval=interval)

    # Frames after the overrun are spaced normally, not fired back to back.
    after = [b - a for a, b in zip(calls[2:], calls[3:])]
    assert all(period >= interval * 0.5 for period in after), after


def test_run_periodically_holds_a_steady_period():
    interval = 0.02
    gate = RenderGate()
    calls = _collect(gate, None, interval, 25)
    periods = [b - a for a, b in zip(calls, calls[1:])]
    # The *mean* is what has to hold: individual periods swing either side as
    # the schedule corrects for each overshoot, which is the point.
    assert statistics.mean(periods) == pytest.approx(
        interval, abs=_sleep_overshoot(interval) / 2 + 0.001
    )


def test_gate_throttles_off_screen_programs():
    gate = RenderGate(idle_interval=0.05)
    on_screen = object()
    gate.set_active(on_screen)

    assert gate.interval_for(on_screen, 0.03) == 0.03
    # Off screen with nobody watching the gallery: idle.
    assert gate.interval_for(object(), 0.03) == 0.05
    # Off screen with the gallery open: the gallery's rate.
    gate.set_background_interval(0.15)
    assert gate.interval_for(object(), 0.03) == 0.15
    # ...but never faster than the program asked for.
    assert gate.interval_for(object(), 0.5) == 0.5
    # Loops that opted out (owner=None) are never throttled.
    assert gate.interval_for(None, 0.03) == 0.03


def test_gate_wakes_a_program_that_becomes_visible():
    """An off-screen program idling on a long sleep has to redraw as soon as
    it's put on the board, not when its idle sleep happens to expire."""
    gate = RenderGate(idle_interval=10.0)
    owner = object()
    gate.set_active(object())  # something else is on screen

    woken = threading.Event()
    threading.Thread(
        target=lambda: (gate.sleep(owner, 10.0), woken.set()), daemon=True
    ).start()
    time.sleep(0.05)
    assert not woken.is_set()

    gate.set_active(owner)
    assert woken.wait(timeout=1.0)


def test_gate_sleep_reports_whether_it_was_interrupted():
    gate = RenderGate()
    gate.set_active(object())
    assert gate.sleep(object(), 0.01) is False
