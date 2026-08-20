"""Geometry: flips, rotation and crop."""

from __future__ import annotations

import pytest
from PIL import Image

from watermark.core.models import ResizeMode, TransformSettings
from watermark.core.transforms import (
    apply_transform,
    crop_box,
    crop_from_box,
    normalize_crop,
    resize_to,
    scaled_size,
)


@pytest.fixture
def frame():
    return Image.new("RGB", (1000, 800), (40, 80, 160))


# -- crop ------------------------------------------------------------------ #


@pytest.mark.parametrize("value", [None, [], [0, 0, 0, 0], [0.0, 0.0, 0.0, 0.0]])
def test_a_no_op_crop_normalizes_to_none(value):
    assert normalize_crop(value) is None


def test_crop_values_are_clamped_into_range():
    assert normalize_crop([-1, 0, 2, 0]) == pytest.approx([0.0, 0.0, 0.98, 0.0])


def test_opposing_insets_cannot_consume_the_whole_axis():
    """Dragging both edges past each other must still leave an image."""
    left, _top, right, _bottom = normalize_crop([0.9, 0, 0.9, 0])
    assert left + right <= 0.98
    assert left > 0 and right > 0


def test_crop_box_and_back_round_trip():
    crop = [0.1, 0.2, 0.1, 0.05]
    box = crop_box(crop, (1000, 800))
    assert box == (100, 160, 900, 760)
    assert crop_from_box(box, (1000, 800)) == pytest.approx(crop)


def test_crop_box_of_no_crop_is_the_whole_frame():
    assert crop_box(None, (640, 480)) == (0, 0, 640, 480)


def test_crop_is_resolution_independent():
    """The same preset crops the same proportion of any size."""
    crop = [0.25, 0.25, 0.25, 0.25]
    small = crop_box(crop, (400, 400))
    large = crop_box(crop, (4000, 4000))
    assert (small[2] - small[0]) * 10 == (large[2] - large[0])


def test_apply_transform_crops(frame):
    result = apply_transform(frame, TransformSettings(crop=[0.1, 0.2, 0.1, 0.0]))
    assert result.size == (800, 640)


def test_crop_happens_after_rotation(frame):
    """You straighten first, then trim — so the crop matches what you see."""
    settings = TransformSettings(rotation=90, crop=[0.1, 0.0, 0.1, 0.0])
    # Rotating 1000x800 gives 800x1000; trimming 10% off each side gives 640x1000.
    assert apply_transform(frame, settings).size == (640, 1000)


def test_a_degenerate_crop_is_ignored_rather_than_raising(frame):
    settings = TransformSettings()
    settings.crop = [0.5, 0.5, 0.5, 0.5]  # set past the normalizer, as a bad preset might
    assert apply_transform(frame, settings).size[0] >= 1


def test_is_identity_agrees_with_the_normalizer():
    assert TransformSettings(crop=[0, 0, 0, 0]).is_identity()
    assert not TransformSettings(crop=[0.1, 0, 0, 0]).is_identity()


# -- flips and rotation ----------------------------------------------------- #


def test_identity_returns_the_same_object(frame):
    assert apply_transform(frame, TransformSettings()) is frame


@pytest.mark.parametrize("angle,expected", [(90, (800, 1000)), (180, (1000, 800)), (270, (800, 1000))])
def test_right_angles_swap_the_axes(frame, angle, expected):
    assert apply_transform(frame, TransformSettings(rotation=angle)).size == expected


def test_free_rotation_expands_the_canvas(frame):
    rotated = apply_transform(frame, TransformSettings(rotation=30))
    assert rotated.width > frame.width and rotated.height > frame.height


def test_flips_preserve_the_size(frame):
    flipped = apply_transform(frame, TransformSettings(flip_horizontal=True, flip_vertical=True))
    assert flipped.size == frame.size


# -- resize ----------------------------------------------------------------- #


def test_fit_within_never_enlarges():
    assert scaled_size((800, 600), ResizeMode.FIT_WITHIN, max_dimension=4000) == (800, 600)


def test_fit_within_preserves_aspect():
    assert scaled_size((1000, 500), ResizeMode.FIT_WITHIN, max_dimension=500) == (500, 250)


def test_exact_resize_covers_and_crops(frame):
    """A square target must crop a landscape frame, not squash it."""
    squared = resize_to(frame, (400, 400), cover=True)
    assert squared.size == (400, 400)
