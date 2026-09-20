import base64
import json
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from controller.data import dimensions
from controller.displays.simulate import Simulate, _encode
from controller.settings import settings


@pytest.fixture
def sim():
    s = Simulate(port=0, open_browser=False)
    yield s
    s.close()


def test_serves_home_page_with_buttons(sim):
    resp = urllib.request.urlopen(f"http://127.0.0.1:{sim.port}/", timeout=2)
    assert resp.status == 200
    body = resp.read().decode()
    assert "button-a" in body
    assert "button-b" in body
    assert "<title>Pi Mosaic</title>" in body
    assert 'rel="icon"' in body


def test_serves_favicon(sim):
    for path in ("/favicon.ico", "/favicon.svg"):
        resp = urllib.request.urlopen(f"http://127.0.0.1:{sim.port}{path}", timeout=2)
        assert resp.status == 200
        assert "image/svg+xml" in resp.headers.get("Content-Type", "")
        data = resp.read()
        assert b"<svg" in data


def test_unknown_path_is_404(sim):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"http://127.0.0.1:{sim.port}/nope", timeout=2)
    assert exc_info.value.code == 404


def test_button_endpoints_increment_counters(sim):
    assert sim.button_a_index == 0
    assert sim.button_b_index == 0

    urllib.request.urlopen(
        urllib.request.Request(f"http://127.0.0.1:{sim.port}/button/a", method="POST"),
        timeout=2,
    )
    assert sim.button_a_index == 1
    assert sim.button_b_index == 0

    urllib.request.urlopen(
        urllib.request.Request(f"http://127.0.0.1:{sim.port}/button/b", method="POST"),
        timeout=2,
    )
    assert sim.button_b_index == 1


def _frame(marker=(1, 2, 3)):
    pixels = np.zeros(
        (dimensions.height, dimensions.width, 3), dtype=dimensions.data_type
    )
    pixels[0, 0] = marker
    return pixels


def _read_event(resp):
    """Read one SSE `data:` payload off an open stream.

    Events are terminated by a blank line, so skip those to stay aligned when
    reading more than one event from the same stream.
    """
    while True:
        line = resp.readline().decode()
        assert line, "stream closed"
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
        assert line.strip() == "", f"unexpected SSE line {line!r}"


def test_display_matrix_broadcasts_to_stream(sim):
    settings.update({"display.brightness": 100})
    pixels = _frame()

    # Frames are handed to a broadcaster thread, so keep pushing until the
    # stream has picked one up rather than relying on a single well-timed
    # call landing after the client registers.
    stop = threading.Event()

    def push():
        while not stop.is_set():
            sim.display_matrix(pixels=pixels)
            time.sleep(0.02)

    pusher = threading.Thread(target=push, daemon=True)
    pusher.start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{sim.port}/stream", timeout=3)
        payload = _read_event(resp)
    finally:
        stop.set()
        pusher.join(timeout=1)

    assert payload["width"] == dimensions.width
    assert payload["height"] == dimensions.height
    rgb = base64.b64decode(payload["pixels"])
    assert len(rgb) == dimensions.width * dimensions.height * 3
    assert list(rgb[:3]) == [1, 2, 3]


def test_display_matrix_does_nothing_without_viewers(sim):
    """The Pi calls this from the same thread that drives the physical
    matrix, so with no browser connected it must not encode anything."""
    assert sim.has_viewers is False
    sim.display_matrix(pixels=_frame())
    assert sim._latest is None


def test_display_gallery_does_nothing_without_viewers(sim):
    assert sim.has_gallery_viewers is False
    sim.display_gallery([("Clock", _frame())])
    assert sim._latest_gallery is None


def test_display_gallery_broadcasts_named_frames(sim):
    settings.update({"display.brightness": 100})
    stop = threading.Event()

    def push():
        while not stop.is_set():
            sim.display_gallery([("Clock", _frame((9, 8, 7)))])
            time.sleep(0.02)

    pusher = threading.Thread(target=push, daemon=True)
    pusher.start()
    try:
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{sim.port}/gallery-stream", timeout=3
        )
        payload = _read_event(resp)
    finally:
        stop.set()
        pusher.join(timeout=1)

    assert [frame["name"] for frame in payload] == ["Clock"]
    assert list(base64.b64decode(payload[0]["pixels"])[:3]) == [9, 8, 7]


def test_brightness_dims_the_web_mirror(sim):
    """The mirror is supposed to look like the panel, and the driver dims the
    panel in hardware - so the encoder has to dim the mirror to match."""
    settings.update({"display.brightness": 50})
    bright = _encode(_frame((200, 100, 40)))
    assert list(base64.b64decode(bright["pixels"])[:3]) == [100, 50, 20]

    settings.update({"display.brightness": 100})
    full = _encode(_frame((200, 100, 40)))
    assert list(base64.b64decode(full["pixels"])[:3]) == [200, 100, 40]


# --- Settings endpoints -----------------------------------------------------


def _get_json(sim, path):
    return json.loads(
        urllib.request.urlopen(f"http://127.0.0.1:{sim.port}{path}").read()
    )


def _post_json(sim, path, payload):
    request = urllib.request.Request(
        f"http://127.0.0.1:{sim.port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(request, timeout=2).read())


def test_settings_endpoint_serves_schema_and_values(sim):
    payload = _get_json(sim, "/settings")
    keys = {entry["key"] for entry in payload["schema"]["settings"]}
    assert "display.brightness" in keys
    assert payload["values"]["display.brightness"] == 75
    # Every setting belongs to a declared section, so the UI can group them.
    sections = set(payload["schema"]["sections"])
    assert all(entry["section"] in sections for entry in payload["schema"]["settings"])


def test_posting_settings_applies_and_reports_rejections(sim):
    result = _post_json(sim, "/settings", {"display.brightness": 30, "bogus": 1})
    assert result["applied"] == {"display.brightness": 30}
    assert result["rejected"] == ["bogus"]
    assert settings.get("display.brightness") == 30


def test_posting_a_non_object_body_is_a_400(sim):
    request = urllib.request.Request(
        f"http://127.0.0.1:{sim.port}/settings",
        data=b"[1,2,3]",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request, timeout=2)
    assert exc_info.value.code == 400


def test_oversized_body_is_refused(sim):
    """The server listens on 0.0.0.0:80 on the Pi; it must not try to buffer
    whatever anyone on the network sends it."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{sim.port}/settings",
        data=b'{"display.brightness": 1}' + b" " * (128 * 1024),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request, timeout=2)
    assert exc_info.value.code == 400
    assert settings.get("display.brightness") == 75


def test_reset_endpoint_restores_defaults(sim):
    _post_json(sim, "/settings", {"display.brightness": 10})
    result = _post_json(sim, "/settings/reset", {})
    assert result["values"]["display.brightness"] == 75


def test_settings_changes_are_broadcast_to_other_tabs(sim):
    """Two browsers open on the board should agree about what the settings
    are, so a change from one has to reach the other."""
    resp = urllib.request.urlopen(
        f"http://127.0.0.1:{sim.port}/settings-stream", timeout=3
    )
    first = _read_event(resp)  # current state, sent on connect
    assert first["display.brightness"] == 75

    threading.Timer(0.1, lambda: settings.update({"display.brightness": 22})).start()
    assert _read_event(resp)["display.brightness"] == 22
