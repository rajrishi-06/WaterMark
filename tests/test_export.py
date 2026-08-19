"""Encoding, compression and writing."""

from __future__ import annotations

import pytest
from PIL import Image

from watermark.core import export, imageio, render
from watermark.core.errors import ExportError
from watermark.core.models import ExportSettings, ImageFormat, JobSettings, ResizeMode


@pytest.fixture
def source(photo):
    return imageio.load_image(photo)


@pytest.fixture
def watermarked(source):
    settings = JobSettings()
    settings.text.text = "© Test"
    return render.render(source, settings)


def test_available_formats_are_writable(watermarked):
    for image_format in export.available_formats():
        if image_format is ImageFormat.KEEP:
            continue
        result = export.encode(watermarked, ExportSettings(image_format=image_format))
        assert result.byte_count > 0


def test_rgba_saves_as_jpeg(watermarked):
    """The original app crashed here: JPEG cannot store an alpha channel."""
    assert watermarked.mode == "RGBA"
    result = export.encode(watermarked, ExportSettings(image_format=ImageFormat.JPEG))
    assert Image.open(__import__("io").BytesIO(result.data)).mode == "RGB"


def test_transparency_is_matted_with_the_chosen_colour(transparent_png):
    source = imageio.load_image(transparent_png)
    settings = ExportSettings(image_format=ImageFormat.JPEG, matte_color="#00FF00")
    result = export.encode(source.image, settings, source)
    decoded = Image.open(__import__("io").BytesIO(result.data))
    assert decoded.getpixel((2, 2))[1] > 200  # the green matte, not black


def test_keep_format_follows_the_source(source, watermarked):
    result = export.encode(watermarked, ExportSettings(image_format=ImageFormat.KEEP), source)
    assert result.image_format is ImageFormat.JPEG


def test_quality_controls_size(watermarked):
    low = export.encode(watermarked, ExportSettings(image_format=ImageFormat.JPEG, quality=20))
    high = export.encode(watermarked, ExportSettings(image_format=ImageFormat.JPEG, quality=95))
    assert low.byte_count < high.byte_count


def test_fit_within_never_upscales(watermarked):
    settings = ExportSettings(
        image_format=ImageFormat.PNG, resize_mode=ResizeMode.FIT_WITHIN, max_dimension=5000
    )
    assert export.encode(watermarked, settings).size == watermarked.size


def test_fit_within_shrinks(watermarked):
    settings = ExportSettings(
        image_format=ImageFormat.PNG, resize_mode=ResizeMode.FIT_WITHIN, max_dimension=200
    )
    assert max(export.encode(watermarked, settings).size) == 200


def test_percent_resize(watermarked):
    settings = ExportSettings(
        image_format=ImageFormat.PNG, resize_mode=ResizeMode.PERCENT, scale_percent=50
    )
    assert export.encode(watermarked, settings).size == (
        watermarked.width // 2, watermarked.height // 2
    )


def test_exact_resize_crops_rather_than_squashing(watermarked):
    settings = ExportSettings(
        image_format=ImageFormat.PNG, resize_mode=ResizeMode.EXACT,
        exact_width=300, exact_height=300,
    )
    assert export.encode(watermarked, settings).size == (300, 300)


@pytest.mark.parametrize("target_kb", [80, 30])
def test_target_size_is_respected(watermarked, target_kb):
    settings = ExportSettings(image_format=ImageFormat.JPEG, quality=98)
    result = export.compress_to_target(watermarked, settings, target_kb * 1024)
    assert result.byte_count <= target_kb * 1024


def test_target_size_reports_when_it_cannot_reach_the_goal(watermarked):
    settings = ExportSettings(image_format=ImageFormat.JPEG)
    result = export.compress_to_target(watermarked, settings, 1024)
    assert result.notes and "Could not reach" in result.notes[0]


def test_target_size_prefers_quality_over_shrinking(watermarked):
    """A generous budget should be met without touching the dimensions."""
    settings = ExportSettings(image_format=ImageFormat.JPEG, quality=95)
    result = export.compress_to_target(watermarked, settings, 200 * 1024)
    assert result.size == watermarked.size


def test_png_palette_reduces_size():
    flat = Image.new("RGB", (600, 400), (250, 250, 250))
    from PIL import ImageDraw

    draw = ImageDraw.Draw(flat)
    for i in range(30):
        draw.rectangle([i * 18, i * 12, i * 18 + 160, i * 12 + 70], outline=(20, 60, 180), width=3)

    settings = ExportSettings(image_format=ImageFormat.PNG)
    full = export.encode(flat, settings)
    paletted = export.encode(flat, settings, palette_colors=32)
    assert paletted.byte_count < full.byte_count


def test_metadata_is_stripped_by_default(tmp_path):
    path = tmp_path / "tagged.jpg"
    exif = Image.Exif()
    exif[271] = "TestCamera"
    Image.new("RGB", (200, 150), (90, 90, 90)).save(path, exif=exif)

    source = imageio.load_image(str(path))
    assert source.exif

    stripped = export.encode(source.image, ExportSettings(image_format=ImageFormat.JPEG), source)
    assert not Image.open(__import__("io").BytesIO(stripped.data)).getexif()

    kept = export.encode(
        source.image,
        ExportSettings(image_format=ImageFormat.JPEG, strip_metadata=False),
        source,
    )
    assert Image.open(__import__("io").BytesIO(kept.data)).getexif()


def test_save_creates_missing_folders(watermarked, tmp_path):
    destination = tmp_path / "a" / "b" / "out.png"
    export.save_image(watermarked, str(destination), ExportSettings(image_format=ImageFormat.PNG))
    assert destination.exists()


def test_save_leaves_no_partial_file_behind(watermarked, tmp_path):
    destination = tmp_path / "out.png"
    export.save_image(watermarked, str(destination), ExportSettings(image_format=ImageFormat.PNG))
    assert not (tmp_path / "out.png.part").exists()


def test_save_failure_is_reported_as_export_error(watermarked, tmp_path):
    directory = tmp_path / "blocked"
    directory.write_text("I am a file, not a folder")
    with pytest.raises(ExportError):
        export.save_image(
            watermarked, str(directory / "out.png"), ExportSettings(image_format=ImageFormat.PNG)
        )


def test_format_for_path():
    assert export.format_for_path("a.JPG") is ImageFormat.JPEG
    assert export.format_for_path("a.webp") is ImageFormat.WEBP
    assert export.format_for_path("a.txt") is None


@pytest.mark.parametrize(
    "value,expected",
    [(0, "0 B"), (900, "900 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB")],
)
def test_human_size(value, expected):
    assert export.human_size(value) == expected
