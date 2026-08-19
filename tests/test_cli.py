"""The command-line interface."""

from __future__ import annotations

import json
import os

import pytest

from watermark import cli
from watermark.core.models import Anchor, ImageFormat, ResizeMode


@pytest.mark.parametrize(
    "text,expected",
    [("500k", 500), ("500", 500), ("2M", 2048), ("1.5m", 1536), (" 250 KB ", 250)],
)
def test_parse_size(text, expected):
    assert cli.parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0"])
def test_parse_size_rejects_nonsense(text):
    with pytest.raises(Exception):
        cli.parse_size(text)


def test_apply_writes_the_requested_file(photo, tmp_path, capsys):
    output = tmp_path / "out.webp"
    code = cli.main(["apply", photo, "-o", str(output), "--text", "© Test", "--format", "webp"])
    assert code == 0 and output.exists()
    assert str(output) in capsys.readouterr().out


def test_apply_infers_the_format_from_the_extension(photo, tmp_path):
    output = tmp_path / "out.png"
    assert cli.main(["apply", photo, "-o", str(output)]) == 0
    from PIL import Image

    assert Image.open(output).format == "PNG"


def test_apply_reports_a_missing_file(tmp_path, capsys):
    assert cli.main(["apply", str(tmp_path / "nope.jpg"), "-o", str(tmp_path / "x.png")]) == 1
    assert "error" in capsys.readouterr().err


def test_batch_processes_a_folder(photo_folder, tmp_path):
    folder, _ = photo_folder
    output = tmp_path / "out"
    assert cli.main(["batch", str(folder), "-o", str(output), "--text", "hi"]) == 0
    assert len(os.listdir(output)) == 4


def test_batch_recursive(photo_folder, tmp_path):
    folder, _ = photo_folder
    output = tmp_path / "out"
    assert cli.main(["batch", str(folder), "-o", str(output), "-r", "-q"]) == 0
    assert len(os.listdir(output)) == 5


def test_batch_refuses_to_write_beside_originals(photo_folder, capsys):
    folder, _ = photo_folder
    assert cli.main(["batch", str(folder)]) == 2
    assert "--in-place" in capsys.readouterr().err


def test_batch_in_place_is_allowed_when_asked(photo_folder):
    folder, _ = photo_folder
    assert cli.main(["batch", str(folder), "--in-place", "-q"]) == 0
    assert any(name.endswith("_wm.jpg") for name in os.listdir(folder))


def test_batch_json_report(photo_folder, tmp_path, capsys):
    folder, _ = photo_folder
    output = tmp_path / "out"
    cli.main(["batch", str(folder), "-o", str(output), "--json", "-q"])
    payload = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert payload["processed"] == 4
    assert all(item["status"] == "ok" for item in payload["items"])


def test_batch_dry_run_writes_nothing(photo_folder, tmp_path):
    folder, _ = photo_folder
    output = tmp_path / "out"
    assert cli.main(["batch", str(folder), "-o", str(output), "--dry-run", "-q"]) == 0
    assert not output.exists()


def test_compress_hits_the_target(photo, tmp_path):
    output = tmp_path / "small"
    assert cli.main(
        ["compress", photo, "-o", str(output), "--format", "jpeg", "--target-size", "20k", "-q"]
    ) == 0
    written = os.path.join(output, os.listdir(output)[0])
    assert os.path.getsize(written) <= 20 * 1024


def test_compress_keeps_the_original_name(photo, tmp_path):
    output = tmp_path / "small"
    cli.main(["compress", photo, "-o", str(output), "--format", "webp", "-q"])
    assert os.listdir(output) == ["photo.webp"]


def test_info_json(photo, capsys):
    assert cli.main(["info", photo, "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert records[0]["width"] == 800


def test_info_human_readable(photo, capsys):
    cli.main(["info", photo])
    assert "800 × 600" in capsys.readouterr().out


def test_presets_listing(capsys):
    assert cli.main(["presets"]) == 0
    assert "Signature" in capsys.readouterr().out


def test_presets_show_is_valid_json(capsys):
    cli.main(["presets", "--show", "Signature"])
    assert json.loads(capsys.readouterr().out)["name"] == "Signature"


def test_build_settings_maps_every_flag():
    parser = cli.build_parser()
    args = parser.parse_args([
        "batch", "x", "-o", "y",
        "--text", "hello", "--size", "7.5", "--color", "#ff0000", "--opacity", "40",
        "--anchor", "top-left", "--margin", "8", "--shadow", "--backdrop",
        "--tile", "--tile-angle", "45", "--tile-opacity", "12", "--tile-spacing", "9",
        "--rotate", "90", "--flip-horizontal",
        "--format", "webp", "--quality", "70", "--max-dimension", "1600",
        "--target-size", "300k", "--keep-metadata", "--drop-icc",
    ])
    settings = cli.build_settings(args)

    assert settings.text.text == "hello" and settings.text.size_percent == 7.5
    assert settings.text.color == "#ff0000" and settings.text.opacity == 40
    assert settings.text.placement.anchor is Anchor.TOP_LEFT
    assert settings.text.placement.margin_percent == 8
    assert settings.text.shadow.enabled and settings.text.backdrop.enabled
    assert settings.tile.enabled and settings.tile.angle == 45
    assert settings.tile.opacity == 12 and settings.tile.spacing_y_percent == 9
    assert settings.transform.rotation == 90 and settings.transform.flip_horizontal
    assert settings.export.image_format is ImageFormat.WEBP
    assert settings.export.quality == 70
    assert settings.export.resize_mode is ResizeMode.FIT_WITHIN
    assert settings.export.max_dimension == 1600
    assert settings.export.target_size_kb == 300
    assert settings.export.strip_metadata is False
    assert settings.export.keep_icc_profile is False


def test_preset_flag_is_the_starting_point():
    args = cli.build_parser().parse_args(
        ["batch", "x", "-o", "y", "--preset", "Confidential — tiled", "--tile-opacity", "55"]
    )
    settings = cli.build_settings(args)
    assert settings.tile.enabled is True
    assert settings.tile.opacity == 55


def test_exact_size_parsing():
    args = cli.build_parser().parse_args(["batch", "x", "-o", "y", "--exact", "1080x1080"])
    settings = cli.build_settings(args)
    assert settings.export.resize_mode is ResizeMode.EXACT
    assert (settings.export.exact_width, settings.export.exact_height) == (1080, 1080)


def test_no_text_disables_the_watermark():
    args = cli.build_parser().parse_args(["batch", "x", "-o", "y", "--no-text"])
    assert cli.build_settings(args).text.enabled is False
