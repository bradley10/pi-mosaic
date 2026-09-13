import numpy as np
import pytest

from controller.data import dimensions, draw_lines_on, validate_pixels, word_width


def test_word_width():
    assert word_width("a") == 5
    assert word_width("ab") == 11
    assert word_width("abc") == 17


def test_draw_lines_on_returns_same_shape_and_dtype():
    pixels = np.zeros(
        (dimensions.height, dimensions.width, 3), dtype=dimensions.data_type
    )
    result = draw_lines_on(pixels, ["hi"])
    assert result.shape == pixels.shape
    assert result.dtype == pixels.dtype


def test_validate_pixels_rejects_wrong_shape():
    @validate_pixels
    def fn(pixels):
        return pixels

    with pytest.raises(ValueError):
        fn(pixels=np.zeros((1, 1, 3), dtype=dimensions.data_type))


def test_validate_pixels_rejects_wrong_dtype():
    @validate_pixels
    def fn(pixels):
        return pixels

    with pytest.raises(ValueError):
        fn(pixels=np.zeros((dimensions.height, dimensions.width, 3), dtype=np.float32))


def test_validate_pixels_accepts_valid_input():
    @validate_pixels
    def fn(pixels):
        return pixels

    pixels = np.zeros(
        (dimensions.height, dimensions.width, 3), dtype=dimensions.data_type
    )
    assert fn(pixels=pixels) is pixels
