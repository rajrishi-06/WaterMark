"""The rendering pipeline."""

from __future__ import annotations

import pytest
from PIL import Image

from watermark.core import imageio, render
from watermark.core.models import (
    Anchor,
    ImageWatermark,
    JobSettings,
    TextWatermark,
    TileSource,
)


def mean_difference(first: Image.Image, second: Image.Image) -> float:
    """Average per-pixel luminance difference — a simple "did it change?" probe."""
    left = first.convert("L").tobytes()
    right = second.convert("L").tobytes()
    return sum(abs(a - b) for a, b in zip(left, right)) / len(left)


@pytest.fixture
def source(photo):
    return imageio.load_image(photo)


@pytest.fixture
def clean(source):
    settings = JobSettings()
    settings.text.enabled = False
    return render.render(source, settings)


def test_render_returns_the_canvas_size(source):
    result = render.render(source, JobSettings())
    assert result.size == source.size
    assert result.mode == "RGBA"


def test_text_watermark_changes_pixels(source, clean):
    settings = JobSettings()
    settings.text.text = "WATERMARK"
    assert mean_difference(render.render(source, settings), clean) > 0.05


def test_disabled_text_leaves_the_image_alone(source, clean):
    settings = JobSettings()
    settings.text.enabled = False
    assert mean_difference(render.render(source, settings), clean) == 0


def test_empty_text_draws_nothing(source, clean):
    settings = JobSettings()
    settings.text.text = "   "
    assert mean_difference(render.render(source, settings), clean) == 0


def test_tokens_are_expanded(source):
    settings = JobSettings()
    settings.text.text = "{year}"
    layer_with = render.build_text_layer(settings.text, (800, 600), {"year": "2026"})
    layer_without = render.build_text_layer(settings.text, (800, 600), {})
    # "{year}" is wider than "2026", so the expanded layer must be narrower.
    assert layer_with is not None and layer_without is not None
    assert layer_with.width < layer_without.width


def test_opacity_scales_the_effect(source, clean):
    faint, strong = JobSettings(), JobSettings()
    faint.text.opacity, strong.text.opacity = 10, 100
    assert (
        mean_difference(render.render(source, faint), clean)
        < mean_difference(render.render(source, strong), clean)
    )


def test_zero_opacity_is_invisible(source, clean):
    settings = JobSettings()
    settings.text.opacity = 0
    assert mean_difference(render.render(source, settings), clean) == 0


def test_anchor_moves_the_layer(source):
    settings = JobSettings()
    settings.text.placement.anchor = Anchor.TOP_LEFT
    top_left = render.layer_geometry(source, settings)["text"]
    settings.text.placement.anchor = Anchor.BOTTOM_RIGHT
    bottom_right = render.layer_geometry(source, settings)["text"]
    assert top_left[0] < bottom_right[0] and top_left[1] < bottom_right[1]


def test_preview_is_a_faithful_miniature(source):
    """Percentages everywhere means the preview matches the full render."""
    settings = JobSettings()
    settings.text.text = "PROOF"
    settings.text.placement.anchor = Anchor.BOTTOM_RIGHT

    full = render.render(source, settings)
    preview = render.render(source, settings, preview_max_dimension=400)
    ratio = preview.width / full.width

    full_box = render.layer_geometry(source, settings, canvas_size=full.size)["text"]
    preview_box = render.layer_geometry(source, settings, canvas_size=preview.size)["text"]
    for full_value, preview_value in zip(full_box, preview_box):
        assert preview_value == pytest.approx(full_value * ratio, abs=6)


def test_logo_layer_scales_to_the_canvas(source, logo):
    settings = JobSettings()
    settings.image = ImageWatermark(enabled=True, path=logo, scale_percent=25.0)
    layer = render.build_image_layer(settings.image, source.size)
    assert layer is not None
    assert layer.width == pytest.approx(source.size[0] * 0.25, abs=2)


def test_tile_covers_more_than_a_single_stamp(source, clean):
    single, tiled = JobSettings(), JobSettings()
    tiled.tile.enabled = True
    tiled.tile.opacity = 60
    assert (
        mean_difference(render.render(source, tiled), clean)
        > mean_difference(render.render(source, single), clean)
    )


def test_tile_opacity_is_independent_of_layer_opacity(source, clean):
    """The pattern slider must mean the same thing regardless of the Text tab."""
    deltas = []
    for layer_opacity in (10, 55, 100):
        settings = JobSettings()
        settings.text.enabled = False        # pattern only
        settings.text.opacity = layer_opacity
        settings.tile.enabled = True
        settings.tile.opacity = 40
        deltas.append(mean_difference(render.render(source, settings), clean))
    assert deltas[0] == pytest.approx(deltas[1]) == pytest.approx(deltas[2])
    assert deltas[0] > 0


def test_tile_works_from_the_logo(source, clean, logo):
    settings = JobSettings()
    settings.text.enabled = False
    settings.image = ImageWatermark(enabled=False, path=logo, scale_percent=10.0)
    settings.tile.enabled = True
    settings.tile.source = TileSource.IMAGE
    settings.tile.opacity = 50
    assert mean_difference(render.render(source, settings), clean) > 0.05


def test_tiling_stays_bounded_when_spacing_is_zero(source):
    """Zero spacing on a big canvas must not try to draw a million stamps."""
    settings = JobSettings()
    settings.text.text = "."
    settings.text.size_percent = 0.6
    settings.tile.enabled = True
    settings.tile.spacing_x_percent = 0.0
    settings.tile.spacing_y_percent = 0.0
    assert render.render(source, settings).size == source.size


def test_transform_rotation_changes_the_canvas(source):
    settings = JobSettings()
    settings.transform.rotation = 90
    result = render.render(source, settings)
    assert result.size == (source.size[1], source.size[0])


def test_shadow_and_backdrop_enlarge_the_layer():
    plain = TextWatermark(text="Hello")
    decorated = TextWatermark(text="Hello")
    decorated.shadow.enabled = True
    decorated.backdrop.enabled = True

    plain_layer = render.build_text_layer(plain, (800, 600))
    decorated_layer = render.build_text_layer(decorated, (800, 600))
    assert decorated_layer.width > plain_layer.width


def test_multiline_text_is_taller():
    one = render.build_text_layer(TextWatermark(text="One"), (800, 600))
    two = render.build_text_layer(TextWatermark(text="One\nTwo"), (800, 600))
    assert two.height > one.height * 1.5


def test_reference_length_balances_extreme_aspect_ratios():
    """A panorama and a portrait of equal area get the same watermark size."""
    assert render.reference_length((4000, 500)) == render.reference_length((500, 4000))
