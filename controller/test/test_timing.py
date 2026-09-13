import time

import pytest

from controller.timing import run_periodically


class _Stop(Exception):
    pass


def test_run_periodically_calls_fn_repeatedly_and_propagates_errors():
    calls = []

    def step():
        calls.append(time.monotonic())
        if len(calls) == 3:
            raise _Stop

    with pytest.raises(_Stop):
        run_periodically(step, interval=0.01)

    assert len(calls) == 3
