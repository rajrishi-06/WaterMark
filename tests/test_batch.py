"""Batch processing: naming, conflicts, isolation and reporting."""

from __future__ import annotations

import os

import pytest

from watermark.core import presets
from watermark.core.batch import (
    BatchRunner,
    ItemStatus,
    apply_extension,
    collect_inputs,
    process_file,
    resolve_output_path,
)
from watermark.core.models import ConflictPolicy, ImageFormat, JobSettings


@pytest.fixture
def settings(tmp_path):
    job = JobSettings()
    job.text.text = "© Test"
    job.output.directory = str(tmp_path / "out")
    return job


def test_collect_inputs_expands_folders_and_skips_junk(photo_folder):
    folder, _paths = photo_folder
    assert len(collect_inputs([str(folder)])) == 4
    assert len(collect_inputs([str(folder)], recursive=True)) == 5


def test_collect_inputs_deduplicates(photo):
    assert collect_inputs([photo, photo]) == [photo]


def test_apply_extension_follows_the_format():
    assert apply_extension("a.png", ImageFormat.JPEG) == "a.jpg"
    assert apply_extension("a.png", ImageFormat.KEEP) == "a.png"


def test_batch_writes_every_file(photo_folder, settings):
    folder, _ = photo_folder
    report = BatchRunner(collect_inputs([str(folder)]), settings, workers=3).run()
    assert len(report.succeeded) == 4
    assert not report.failed
    assert len(os.listdir(settings.output.directory)) == 4


def test_rename_policy_never_overwrites(photo_folder, settings):
    folder, _ = photo_folder
    files = collect_inputs([str(folder)])
    BatchRunner(files, settings, workers=2).run()
    BatchRunner(files, settings, workers=2).run()
    assert len(os.listdir(settings.output.directory)) == 8


def test_skip_policy_leaves_existing_files(photo_folder, settings):
    folder, _ = photo_folder
    files = collect_inputs([str(folder)])
    BatchRunner(files, settings, workers=2).run()
    settings.output.conflict = ConflictPolicy.SKIP
    second = BatchRunner(files, settings, workers=2).run()
    assert len(second.skipped) == 4 and not second.succeeded


def test_overwrite_policy_reuses_the_name(photo_folder, settings):
    folder, _ = photo_folder
    files = collect_inputs([str(folder)])
    settings.output.conflict = ConflictPolicy.OVERWRITE
    BatchRunner(files, settings, workers=2).run()
    BatchRunner(files, settings, workers=2).run()
    assert len(os.listdir(settings.output.directory)) == 4


def test_output_never_clobbers_the_source(photo):
    """Writing in place with the source's own name must rename instead."""
    settings = JobSettings()
    settings.output.directory = ""
    settings.output.filename_pattern = "{filename}"
    settings.output.conflict = ConflictPolicy.OVERWRITE
    destination = resolve_output_path(photo, settings, ImageFormat.KEEP)
    assert destination is not None
    assert os.path.abspath(destination) != os.path.abspath(photo)


def test_concurrent_runs_do_not_collide(photo_folder, settings):
    """Two inputs mapping to one name get distinct files, not a lost race."""
    folder, _ = photo_folder
    settings.output.filename_pattern = "same{ext}"
    report = BatchRunner(collect_inputs([str(folder)]), settings, workers=4).run()
    outputs = {item.output for item in report.succeeded}
    assert len(outputs) == len(report.succeeded) == 4


def test_tokens_work_in_filenames(photo, settings):
    settings.output.filename_pattern = "{name}-{index}of{count}{ext}"
    report = BatchRunner([photo], settings).run()
    assert os.path.basename(report.succeeded[0].output) == "photo-1of1.jpg"


def test_format_conversion_fixes_the_extension(photo, settings):
    settings.export.image_format = ImageFormat.WEBP
    report = BatchRunner([photo], settings).run()
    assert report.succeeded[0].output.endswith(".webp")


def test_a_broken_file_does_not_stop_the_run(photo_folder, settings, tmp_path):
    folder, _ = photo_folder
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"nope")
    files = collect_inputs([str(folder)]) + [str(broken)]

    report = BatchRunner(files, settings, workers=3).run()
    assert len(report.succeeded) == 4
    assert len(report.failed) == 1
    assert "not an image" in report.failed[0].message


def test_dry_run_writes_nothing(photo_folder, settings):
    folder, _ = photo_folder
    report = BatchRunner(collect_inputs([str(folder)]), settings, dry_run=True).run()
    assert len(report.succeeded) == 4
    assert not os.path.isdir(settings.output.directory)


def test_progress_is_reported_once_per_file(photo_folder, settings):
    folder, _ = photo_folder
    seen = []
    BatchRunner(
        collect_inputs([str(folder)]), settings, workers=2,
        on_progress=lambda done, total, path: seen.append((done, total)),
    ).run()
    assert sorted(done for done, _ in seen) == [1, 2, 3, 4]


def test_cancel_stops_queued_work(photo_folder, settings):
    folder, _ = photo_folder
    runner = BatchRunner(collect_inputs([str(folder)]), settings, workers=1)
    runner.cancel()
    report = runner.run()
    assert report.cancelled
    assert not report.succeeded


def test_report_summary_mentions_the_savings(photo, settings):
    settings.export.image_format = ImageFormat.WEBP
    settings.export.quality = 40
    report = BatchRunner([photo], settings).run()
    assert "processed" in report.summary()


def test_presets_survive_a_batch(photo, settings, tmp_path):
    preset = presets.load_preset("Confidential — tiled")
    preset.output.directory = str(tmp_path / "tiled")
    report = BatchRunner([photo], preset).run()
    assert report.succeeded and os.path.exists(report.succeeded[0].output)


def test_process_file_reports_status_for_a_missing_file(settings):
    item = process_file("/definitely/not/here.jpg", settings)
    assert item.status is ItemStatus.ERROR
