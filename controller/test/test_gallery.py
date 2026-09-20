"""Covers the painting pages and their live contrast control.

Paintings are the only static pages on the board, which makes them the only
ones where "the frame never changes" is a problem - see the class docstring
in `gallery.py`."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from controller.data import dimensions
from controller.programs.gallery import (
    ARTWORKS,
    Painting,
    _asset_path,
    apply_contrast,
    create_paintings,
)
from controller.settings import settings


@pytest.fixture
def painting():
    return Painting(ARTWORKS[0])


def test_every_artwork_has_an_asset_on_disk():
    for artwork in ARTWORKS:
        assert _asset_path(artwork["title"]).exists(), artwork["title"]


def test_the_starry_night_is_in_the_gallery():
    titles = [artwork["title"] for artwork in ARTWORKS]
    assert "The Starry Night" in titles
    # Distinct from the other Van Gogh night scene already in the rotation.
    assert "Starry Night Over the Rhone" in titles


def test_every_painting_is_a_well_formed_frame():
    for program in create_paintings():
        frame = program.pixels
        assert frame.shape == (dimensions.height, dimensions.width, 3)
        assert frame.min() >= 0 and frame.max() <= 255


def test_paintings_have_distinct_display_names():
    names = [program.display_name for program in create_paintings()]
    assert len(names) == len(set(names))


def test_contrast_of_one_is_the_artwork_unchanged():
    source = np.array([[[10, 128, 250]]], dtype=np.int32)
    assert np.array_equal(apply_contrast(source, 1.0), source)


def test_contrast_returns_a_new_array_not_the_source():
    source = np.array([[[10, 128, 250]]], dtype=np.int32)
    # The main loop's identity check is what makes a change visible; handing
    # back the same object would mean the new frame is never pushed.
    assert apply_contrast(source, 1.0) is not source
    assert apply_contrast(source, 1.5) is not source


def test_higher_contrast_spreads_tones_and_lower_flattens(painting):
    source = painting._source
    assert apply_contrast(source, 1.8).std() > source.std()
    assert apply_contrast(source, 0.4).std() < source.std()


def test_contrast_stays_in_range_and_keeps_dtype(painting):
    for factor in (0.3, 1.0, 1.7, 2.5):
        frame = apply_contrast(painting._source, factor)
        assert frame.min() >= 0
        assert frame.max() <= 255
        assert frame.dtype == painting._source.dtype


def test_flattening_all_the_way_converges_on_a_single_tone(painting):
    # factor 0 collapses every pixel onto the pivot.
    assert apply_contrast(painting._source, 0.0).std() == pytest.approx(0.0)


def test_changing_contrast_publishes_a_new_frame(painting):
    before = painting.pixels
    settings.update({"gallery.contrast": 1.9})

    assert painting.pixels is not before
    assert painting.pixels.std() > before.std()


def test_contrast_is_always_recomputed_from_the_pristine_asset(painting):
    original = painting.pixels.copy()

    # Dragging the slider out and back must land exactly where it started,
    # not on an image that has been through contrast twice.
    for factor in (2.2, 0.5, 1.6, 0.8, 1.0):
        settings.update({"gallery.contrast": factor})

    assert np.array_equal(painting.pixels, original)


def test_an_unrelated_setting_does_not_republish(painting):
    before = painting.pixels
    settings.update({"display.brightness": 42})
    # Brightness is a display-layer concern; rebuilding the frame for it would
    # be wasted work on every painting.
    assert painting.pixels is before


def test_new_paintings_pick_up_the_current_contrast():
    settings.update({"gallery.contrast": 2.0})
    fresh = Painting(ARTWORKS[0])
    assert np.array_equal(fresh.pixels, apply_contrast(fresh._source, 2.0))


def _column_luma(frame):
    return (frame.astype(float) @ (0.299, 0.587, 0.114)).mean(axis=0)


def test_no_painting_has_a_bright_scan_fringe_on_its_edge_columns():
    """Museum scans often include unpainted canvas or frame edge.

    At 64 wide, one column is 1.5% of the image, so a fringe too thin to
    notice in the source lands as one blazing column on the board - which is
    exactly what The Starry Night's Google Art Project scan did (+37 luma on
    column 0). The threshold is loose enough not to flag artwork that is
    genuinely bright at an edge; it's here to catch a scan artifact.
    """
    for artwork in ARTWORKS:
        frame = np.load(_asset_path(artwork["title"]))
        luma = _column_luma(frame)
        title = artwork["title"]
        assert luma[0] - luma[1] < 25, f"{title}: bright fringe on the left edge"
        assert luma[-1] - luma[-2] < 25, f"{title}: bright fringe on the right edge"


def _fetch_script():
    """`scripts/fetch_paintings.py` is a dev-machine tool, not part of the
    package, so it's loaded by path."""
    path = Path(__file__).parent.parent.parent / "scripts" / "fetch_paintings.py"
    spec = importlib.util.spec_from_file_location("fetch_paintings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bordered_image(border_px: int = 12) -> bytes:
    """A mid-grey image inside a white border, standing in for a scan that
    caught the unpainted canvas edge."""
    from io import BytesIO

    array = np.full((400, 500, 3), 90, dtype=np.uint8)
    array[:border_px, :] = array[-border_px:, :] = 255
    array[:, :border_px] = array[:, -border_px:] = 255
    buffer = BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def test_inset_removes_a_border_the_aspect_crop_would_have_kept():
    module = _fetch_script()
    raw = _bordered_image()

    without = _column_luma(module._to_matrix_frame(raw, inset=0.0))
    with_inset = _column_luma(module._to_matrix_frame(raw, inset=0.05))

    # The aspect crop trims top and bottom, so the left/right border survives
    # it - that's the one the inset has to deal with.
    assert without[0] - without[1] > 25
    assert with_inset[0] - with_inset[1] < 5
    assert with_inset[-1] - with_inset[-2] < 5


def test_inset_defaults_to_no_trim():
    module = _fetch_script()
    raw = _bordered_image()
    assert np.array_equal(
        module._to_matrix_frame(raw), module._to_matrix_frame(raw, inset=0.0)
    )


def test_only_the_named_paintings_are_selected():
    module = _fetch_script()
    # Re-running the whole set silently rewrites every asset whenever
    # Wikimedia regenerates its thumbnails, so rebuilding one has to be
    # possible.
    chosen = module._selected(["The Starry Night"])
    assert [source["title"] for source in chosen] == ["The Starry Night"]
    assert len(module._selected([])) == len(module.SOURCES)


def test_an_unknown_painting_is_rejected_rather_than_silently_skipped():
    module = _fetch_script()
    with pytest.raises(SystemExit):
        module._selected(["Not A Painting"])


def test_starry_night_carries_the_inset_that_fixes_its_scan():
    module = _fetch_script()
    (source,) = [s for s in module.SOURCES if s["title"] == "The Starry Night"]
    assert source["inset"] > 0
