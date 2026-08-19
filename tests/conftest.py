"""Shared fixtures.

Every test runs against throwaway config/data/log directories so a test run
never reads or writes the developer's real preferences and presets.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Point the app's config, preset and log directories at ``tmp_path``."""
    for variable, name in (
        ("WATERMARK_CONFIG_DIR", "config"),
        ("WATERMARK_DATA_DIR", "data"),
        ("WATERMARK_LOG_DIR", "logs"),
    ):
        monkeypatch.setenv(variable, str(tmp_path / name))
    yield tmp_path


def make_photo(path, size=(800, 600), quality=92):
    """A JPEG with enough structure that compression behaves realistically."""
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    width, height = size
    for y in range(0, height, 4):
        draw.rectangle([0, y, width, y + 4], fill=(30 + y % 200, 90, 170 - y % 150))
    for i in range(10):
        draw.ellipse(
            [i * width // 12, height // 4, i * width // 12 + 90, height // 4 + 90],
            fill=(240 - i * 12, 180, 60 + i * 15),
        )
    image.save(path, quality=quality)
    return str(path)


@pytest.fixture
def photo(tmp_path):
    return make_photo(tmp_path / "photo.jpg")


@pytest.fixture
def transparent_png(tmp_path):
    path = tmp_path / "clear.png"
    image = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse([50, 50, 350, 250], fill=(220, 40, 40, 255))
    image.save(path)
    return str(path)


@pytest.fixture
def logo(tmp_path):
    path = tmp_path / "logo.png"
    image = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle([0, 0, 299, 119], radius=20, fill=(255, 255, 255, 240))
    image.save(path)
    return str(path)


@pytest.fixture
def photo_folder(tmp_path):
    folder = tmp_path / "album"
    folder.mkdir()
    (folder / "nested").mkdir()
    paths = [make_photo(folder / f"shot{i}.jpg", (400, 300)) for i in range(4)]
    paths.append(make_photo(folder / "nested" / "deep.jpg", (400, 300)))
    (folder / "notes.txt").write_text("not an image")
    return folder, paths
