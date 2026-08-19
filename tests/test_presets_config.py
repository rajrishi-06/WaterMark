"""Presets and persisted preferences."""

from __future__ import annotations

import os

import pytest

from watermark.core import config, presets
from watermark.core.errors import PresetError
from watermark.core.models import ImageFormat, JobSettings


def test_builtins_are_listed_and_loadable():
    infos = presets.list_presets()
    assert len(infos) >= 9
    for info in infos:
        assert info.builtin
        loaded = presets.load_preset(info.name)
        assert isinstance(loaded, JobSettings)
        assert loaded.name == info.name


def test_compression_presets_configure_the_exporter():
    email = presets.load_preset("Email friendly (500 KB)")
    assert email.export.target_size_kb == 500
    assert email.text.enabled is False

    web = presets.load_preset("Web ready (1920px JPEG)")
    assert web.export.image_format is ImageFormat.JPEG
    assert web.export.max_dimension == 1920


def test_save_load_delete_round_trip():
    settings = JobSettings()
    settings.text.text = "mine"
    settings.export.quality = 42

    path = presets.save_preset("My Look", settings, "a description")
    assert os.path.isfile(path)

    names = [info.name for info in presets.list_presets() if not info.builtin]
    assert names == ["My Look"]

    loaded = presets.load_preset("My Look")
    assert loaded.text.text == "mine" and loaded.export.quality == 42

    presets.delete_preset("My Look")
    assert not [info for info in presets.list_presets() if not info.builtin]


def test_builtin_names_are_protected():
    with pytest.raises(PresetError):
        presets.save_preset("Signature", JobSettings())
    with pytest.raises(PresetError):
        presets.delete_preset("Signature")


def test_unknown_preset_raises():
    with pytest.raises(PresetError, match="No preset"):
        presets.load_preset("nope")


def test_unsafe_names_are_sanitised():
    assert "/" not in presets.safe_filename("../../etc/passwd")
    assert presets.safe_filename("Holiday 2026 (v2)") == "Holiday 2026 (v2).json"


def test_a_corrupt_preset_file_is_ignored():
    presets.save_preset("Good", JobSettings())
    from watermark.core.paths import preset_dir

    with open(os.path.join(preset_dir(), "broken.json"), "w", encoding="utf-8") as handle:
        handle.write("{not json")

    names = [info.name for info in presets.list_presets() if not info.builtin]
    assert names == ["Good"]


def test_config_round_trip():
    stored = config.AppConfig(theme="light")
    stored.remember_file("/tmp/one.png")
    stored.remember_file("/tmp/two.png")
    assert config.save_config(stored)

    loaded = config.load_config()
    assert loaded.theme == "light"
    assert loaded.recent_files == ["/tmp/two.png", "/tmp/one.png"]


def test_recent_files_deduplicate_and_cap():
    stored = config.AppConfig()
    for index in range(config.MAX_RECENT + 5):
        stored.remember_file(f"/tmp/file{index}.png")
    stored.remember_file("/tmp/file0.png")
    assert len(stored.recent_files) == config.MAX_RECENT
    assert stored.recent_files[0] == "/tmp/file0.png"


def test_settings_are_remembered():
    stored = config.AppConfig()
    settings = JobSettings()
    settings.text.text = "remembered"
    stored.store_settings(settings)
    config.save_config(stored)

    assert config.load_config().restore_settings().text.text == "remembered"


def test_corrupt_config_falls_back_to_defaults():
    os.makedirs(os.path.dirname(config.config_path()), exist_ok=True)
    with open(config.config_path(), "w", encoding="utf-8") as handle:
        handle.write("{{{ not json")
    assert config.load_config().theme == "dark"


def test_corrupt_stored_settings_fall_back():
    stored = config.AppConfig()
    stored.last_settings = {"export": {"quality": "not a number"}}
    assert isinstance(stored.restore_settings(), JobSettings)
