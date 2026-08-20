"""Loading images safely."""

from __future__ import annotations

import pytest
from PIL import Image

from watermark.core import imageio
from watermark.core.errors import ImageTooLargeError, UnsupportedImageError


def test_describe_reports_the_facts(photo):
    source = imageio.load_image(photo)
    info = source.describe()
    assert (info["width"], info["height"]) == (800, 600)
    assert info["format"] == "JPEG"
    assert info["file_size"] > 0
    assert info["has_alpha"] is False


def test_alpha_is_detected(transparent_png):
    assert imageio.load_image(transparent_png).image.mode == "RGBA"


def test_exif_orientation_is_applied(tmp_path):
    """A phone photo tagged "rotate 90°" must come back upright."""
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (400, 200), (10, 20, 30))
    exif = Image.Exif()
    exif[274] = 6  # orientation: rotate 90° clockwise
    image.save(path, exif=exif)

    assert imageio.load_image(str(path)).size == (200, 400)


def test_missing_file_raises_a_friendly_error(tmp_path):
    with pytest.raises(UnsupportedImageError, match="File not found"):
        imageio.load_image(str(tmp_path / "nope.png"))


def test_non_image_raises_a_friendly_error(tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"this is not an image")
    with pytest.raises(UnsupportedImageError):
        imageio.load_image(str(path))


def test_oversized_image_is_refused(photo):
    with pytest.raises(ImageTooLargeError, match="megapixels"):
        imageio.load_image(photo, max_megapixels=0.01)


def test_multi_frame_images_warn(tmp_path):
    path = tmp_path / "anim.gif"
    # Frames must differ visibly or Pillow collapses them into one.
    frames = [Image.new("RGB", (60, 60), (i * 90, 20, 40)).convert("P") for i in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)
    source = imageio.load_image(str(path))
    assert any("frames" in warning for warning in source.warnings)


def test_iter_image_files_skips_non_images(photo_folder):
    folder, paths = photo_folder
    flat = list(imageio.iter_image_files(str(folder)))
    assert len(flat) == 4
    assert all(not name.endswith(".txt") for name in flat)
    assert len(list(imageio.iter_image_files(str(folder), recursive=True))) == 5


def test_is_supported_is_case_insensitive():
    assert imageio.is_supported("PHOTO.JPG")
    assert not imageio.is_supported("notes.txt")
