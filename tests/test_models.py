"""Settings objects: serialization, forward/backward compatibility, placement."""

from __future__ import annotations

import pytest

from watermark.core.models import Anchor, ImageFormat, JobSettings, Placement, from_dict


def test_round_trip_preserves_everything():
    settings = JobSettings()
    settings.text.text = "© 2026"
    settings.text.shadow.enabled = True
    settings.export.image_format = ImageFormat.WEBP
    settings.export.target_size_kb = 250

    assert JobSettings.from_json_dict(settings.to_json_dict()) == settings


def test_copy_is_deep():
    settings = JobSettings()
    clone = settings.copy()
    clone.text.shadow.blur_radius = 99.0
    assert settings.text.shadow.blur_radius != 99.0


def test_unknown_keys_are_ignored():
    """A preset written by a newer version must still load."""
    data = JobSettings().to_json_dict()
    data["a_field_from_the_future"] = 42
    data["text"]["glow"] = True
    assert from_dict(JobSettings, data).text.text == JobSettings().text.text


def test_missing_keys_fall_back_to_defaults():
    settings = from_dict(JobSettings, {"text": {"text": "only this"}})
    assert settings.text.text == "only this"
    assert settings.export.quality == 88


def test_corrupt_value_does_not_sink_the_preset():
    data = JobSettings().to_json_dict()
    data["export"]["image_format"] = "not-a-format"
    settings = from_dict(JobSettings, data)
    assert settings.export.image_format is ImageFormat.KEEP


@pytest.mark.parametrize(
    "anchor,expected",
    [
        (Anchor.TOP_LEFT, (24, 24)),
        (Anchor.TOP_RIGHT, (876, 24)),
        (Anchor.BOTTOM_LEFT, (24, 726)),
        (Anchor.BOTTOM_RIGHT, (876, 726)),
        (Anchor.CENTER, (450, 375)),
    ],
)
def test_anchor_positions(anchor, expected):
    placement = Placement(anchor=anchor, margin_percent=3.0)
    assert placement.resolve((1000, 800), (100, 50)) == expected


def test_with_position_is_the_inverse_of_resolve():
    placement = Placement(anchor=Anchor.BOTTOM_RIGHT, margin_percent=4.0)
    moved = placement.with_position((317, 208), (1000, 800), (120, 60))
    assert moved.resolve((1000, 800), (120, 60)) == (317, 208)


def test_placement_is_resolution_independent():
    """The same placement lands in the same relative spot at any size."""
    placement = Placement(anchor=Anchor.BOTTOM_RIGHT, margin_percent=5.0)
    small = placement.resolve((500, 400), (50, 25))
    large = placement.resolve((5000, 4000), (500, 250))
    assert small[0] * 10 == pytest.approx(large[0], abs=2)
    assert small[1] * 10 == pytest.approx(large[1], abs=2)
