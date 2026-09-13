import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from controller.data import dimensions
from controller.displays.simulate import Simulate


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


def test_display_matrix_broadcasts_to_stream(sim):
    pixels = np.zeros(
        (dimensions.height, dimensions.width, 3), dtype=dimensions.data_type
    )
    pixels[0, 0] = [1, 2, 3]

    threading.Timer(0.2, lambda: sim.display_matrix(pixels=pixels)).start()

    resp = urllib.request.urlopen(f"http://127.0.0.1:{sim.port}/stream", timeout=3)
    line = resp.readline().decode()
    assert line.startswith("data: ")
    payload = json.loads(line[len("data: ") :])
    assert payload[0][0] == [1, 2, 3]
