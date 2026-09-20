"""Covers the terrain scroll.

The point of this program's design is that scrolling is a *translation*: the
landscape already on screen moves, it is never re-sampled. Re-sampling is what
made it shimmer and warp, so most of these tests exist to pin that down."""

import numpy as np
import pytest

from controller.data import dimensions
from controller.programs.perlin_terrain import (
    _COLUMNS_PER_SPEED_UNIT,
    _MIN_INTERVAL,
    _PAUSED_INTERVAL,
    PerlinTerrain,
)
from controller.settings import settings


@pytest.fixture
def terrain():
    return PerlinTerrain()


def test_a_frame_is_ready_before_the_first_tick(terrain):
    # Landing on the page shouldn't show a black panel for one interval.
    assert terrain.pixels.shape == (dimensions.height, dimensions.width, 3)
    assert terrain.pixels.any()


def test_shifting_moves_the_existing_terrain_exactly_one_pixel_left(terrain):
    before = terrain._heights.copy()
    terrain._shift()

    # Every column that was on screen is unchanged, just one place left. This
    # is the whole fix: nothing already drawn gets recomputed.
    assert np.array_equal(terrain._heights[:, :-1], before[:, 1:])


def test_shifting_generates_a_new_column_on_the_right(terrain):
    before = terrain._heights.copy()
    terrain._shift()
    assert not np.array_equal(terrain._heights[:, -1], before[:, -1])


def test_the_window_stays_a_view_of_one_continuous_field(terrain):
    start = terrain._next_x - dimensions.width
    for _ in range(20):
        terrain._shift()

    # Scrolling 20 columns must land exactly where sampling the field 20
    # columns along would - otherwise the terrain drifts or repeats.
    assert np.allclose(
        terrain._heights, terrain._column_heights(start + 20, dimensions.width)
    )


def test_new_columns_join_without_a_seam(terrain):
    interior = np.abs(np.diff(terrain._heights, axis=1)).mean()
    for _ in range(30):
        terrain._shift()
    join = np.abs(terrain._heights[:, -1] - terrain._heights[:, -2]).mean()

    # A newly generated column should differ from its neighbour about as much
    # as any other adjacent pair; a much bigger step would be a visible seam
    # scrolling across the board.
    assert join < interior * 3


def test_heights_stay_in_range_over_a_long_scroll(terrain):
    for _ in range(200):
        terrain._shift()
    assert terrain._heights.min() >= 0.0
    assert terrain._heights.max() <= 1.0


def test_a_rebuild_does_not_repeat_the_terrain_just_shown(terrain):
    before = terrain._heights.copy()
    terrain._regenerate()
    assert not np.allclose(terrain._heights, before)


def test_changing_zoom_rebuilds_rather_than_scrolling_a_seam_across(terrain):
    before = terrain._heights.copy()
    settings.update({"terrain.zoom": terrain._zoom + 5})
    terrain._step()

    assert terrain._zoom == pytest.approx(settings.get("terrain.zoom"))
    # Not a one-pixel shift of the old buffer.
    assert not np.array_equal(terrain._heights[:, :-1], before[:, 1:])


def test_changing_detail_rebuilds_too(terrain):
    settings.update({"terrain.octaves": terrain._octaves + 1})
    terrain._step()
    assert terrain._octaves == settings.get("terrain.octaves")


def test_scroll_speed_sets_the_tick_rate(terrain):
    settings.update({"terrain.scroll_speed": 2.0})
    assert terrain._interval() == pytest.approx(1.0 / (2.0 * _COLUMNS_PER_SPEED_UNIT))


def test_the_tick_rate_is_floored_at_what_the_main_loop_can_show(terrain):
    settings.update({"terrain.scroll_speed": 6.0})
    # Columns generated faster than the main loop samples are invisible work.
    assert terrain._interval() >= _MIN_INTERVAL


def test_zero_speed_pauses_without_spinning(terrain):
    settings.update({"terrain.scroll_speed": 0.0})
    assert terrain._interval() == _PAUSED_INTERVAL

    before = terrain._heights.copy()
    frame = terrain.pixels
    terrain._step()

    assert np.array_equal(terrain._heights, before)
    # Same array object, so the main loop won't re-push an identical frame.
    assert terrain.pixels is frame


def test_stepping_publishes_a_new_frame_object(terrain):
    frame = terrain.pixels
    terrain._step()
    # The main loop keys off identity to decide whether to push.
    assert terrain.pixels is not frame
