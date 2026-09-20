"""Covers the screen that holds the board until the clock can be trusted.

The controller starts before DHCP finishes (see `deploy/pi-mosaic.service`),
so without this the Clock page would spend its first seconds showing whatever
time fake-hwclock restored from the last shutdown."""

import time

import numpy as np
import pytest

from controller.data import dimensions
from controller.programs import connecting as connecting_module
from controller.programs.connecting import Connecting


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """No timesync flag, and a probe socket that always fails - i.e. a Pi
    that has booted before its network."""
    monkeypatch.setattr(connecting_module, "TIMESYNC_FLAG", str(tmp_path / "absent"))

    def refuse(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(connecting_module.socket, "create_connection", refuse)


def test_synchronized_when_the_timesync_flag_exists(monkeypatch, tmp_path):
    flag = tmp_path / "synchronized"
    flag.write_text("")
    monkeypatch.setattr(connecting_module, "TIMESYNC_FLAG", str(flag))
    # No socket probe needed - the flag alone settles it.
    monkeypatch.setattr(
        connecting_module.socket,
        "create_connection",
        lambda *a, **k: pytest.fail("should not probe when the flag is present"),
    )
    assert connecting_module.clock_is_synchronized()


def test_falls_back_to_a_socket_probe_without_the_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(connecting_module, "TIMESYNC_FLAG", str(tmp_path / "absent"))

    class Socket:
        closed = False

        def close(self):
            Socket.closed = True

    monkeypatch.setattr(
        connecting_module.socket, "create_connection", lambda *a, **k: Socket()
    )
    assert connecting_module.clock_is_synchronized()
    # The probe is a connectivity test, not a connection we keep.
    assert Socket.closed


def test_not_synchronized_when_offline(offline):
    assert not connecting_module.clock_is_synchronized()


def test_starts_not_ready_when_offline(offline):
    assert not Connecting().ready


def test_becomes_ready_once_the_clock_syncs(monkeypatch, offline):
    screen = Connecting(hold_duration=0.0)
    screen._check()
    assert not screen.ready

    monkeypatch.setattr(connecting_module, "clock_is_synchronized", lambda: True)
    screen._check()
    assert screen.ready


def test_ready_latches_so_a_later_dropout_never_returns_to_this_screen(
    monkeypatch, offline
):
    screen = Connecting(hold_duration=0.0)
    monkeypatch.setattr(connecting_module, "clock_is_synchronized", lambda: True)
    screen._check()
    assert screen.ready

    # Network drops again - the board must stay on the rotation.
    monkeypatch.setattr(connecting_module, "clock_is_synchronized", lambda: False)
    screen._check()
    assert screen.ready


def test_shows_connected_state_during_hold_before_ready(monkeypatch, offline):
    screen = Connecting(hold_duration=1.0, ssid="frogandtoad")
    monkeypatch.setattr(connecting_module, "clock_is_synchronized", lambda: True)
    screen._check()
    # Entered connected hold phase, but not yet ready to transition to clock
    assert screen.connected
    assert not screen.ready

    # Advance beyond hold duration
    screen._connected_at = time.monotonic() - 1.5
    screen._check()
    assert screen.ready


def test_gives_up_waiting_rather_than_holding_the_board_forever(offline):
    screen = Connecting()
    screen._check()
    assert not screen.ready

    screen._started = time.monotonic() - connecting_module.GIVE_UP_AFTER - 1
    screen._check()
    assert screen.ready


def test_frame_is_the_right_shape_and_dtype():
    frame = Connecting()._render(0.0)
    assert frame.shape == (dimensions.height, dimensions.width, 3)
    assert frame.dtype == np.int32


def test_frame_pixels_stay_on_the_panel():
    screen = Connecting(ssid="frogandtoad")
    for t in [0.0, 0.5, 1.0, 1.5, 2.0]:
        frame = screen._render(t)
        assert frame.max() <= 255
        assert frame.min() >= 0


def test_icon_breathes_during_connecting():
    screen = Connecting()
    # pulse oscillates between 0 and 1; 0.45s is the peak of the 1.8s sine cycle
    frame_a = screen._render(0.0)
    frame_b = screen._render(0.45)
    assert not np.array_equal(frame_a, frame_b)


def test_long_ssid_pans_without_overflow():
    # Long SSID wider than 64px display
    screen = Connecting(ssid="very_long_wifi_network_name")
    for t in [0.0, 1.0, 2.0, 3.0, 4.0]:
        frame = screen._render(t)
        assert frame.shape == (dimensions.height, dimensions.width, 3)
        assert frame.max() <= 255
        assert frame.min() >= 0


def test_render_does_not_mutate_the_shared_background():
    before = Connecting._BG.copy()
    Connecting()._render(1.0)
    assert np.array_equal(Connecting._BG, before)
    assert not Connecting._BG.any()


def test_step_stops_redrawing_once_ready(monkeypatch, offline):
    screen = Connecting(hold_duration=0.0)
    screen._step()
    frame = screen.pixels

    monkeypatch.setattr(connecting_module, "clock_is_synchronized", lambda: True)
    screen._check()
    screen._step()
    # No point burning frames on a screen that's no longer on the board.
    assert screen.pixels is frame


def test_waits_for_weather_when_clock_provided(monkeypatch, offline):
    class FakeClock:
        has_weather = False

    clock = FakeClock()
    screen = Connecting(clock=clock, hold_duration=0.0)
    monkeypatch.setattr(connecting_module, "clock_is_synchronized", lambda: True)

    screen._check()
    assert not screen.ready

    clock.has_weather = True
    screen._check()
    assert screen.ready


def test_does_not_probe_socket_if_timesyncd_dir_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(connecting_module, "TIMESYNC_FLAG", str(tmp_path / "absent"))
    monkeypatch.setattr(
        connecting_module.os.path,
        "isdir",
        lambda p: True if p == "/run/systemd/timesync" else False,
    )
    monkeypatch.setattr(
        connecting_module.socket,
        "create_connection",
        lambda *a, **k: pytest.fail("should not probe when timesyncd dir exists"),
    )
    assert not connecting_module.clock_is_synchronized()
