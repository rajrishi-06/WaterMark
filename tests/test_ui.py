"""User-interface tests.

The engine and drop-parsing pieces run anywhere.  The window itself needs a
display, so those tests skip cleanly on a headless CI box (and run under
``xvfb-run`` when one is available).
"""

from __future__ import annotations

import os

import pytest

from watermark.core import imageio
from watermark.core.models import ImageFormat, JobSettings, ResizeMode
from watermark.ui import dnd
from watermark.ui.engine import PreviewEngine, estimate_output_bytes, run_async

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _has_display() -> bool:
    if os.name == "nt" or os.uname().sysname == "Darwin":  # pragma: no cover
        return True
    return bool(os.environ.get("DISPLAY"))


needs_display = pytest.mark.skipif(not _has_display(), reason="no display available")


# --------------------------------------------------------------------------- #
# Headless pieces
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload,expected",
    [
        ("/a/b.png", ["/a/b.png"]),
        ("{/a/b c.png} /d/e.png", ["/a/b c.png", "/d/e.png"]),
        ("", []),
    ],
)
def test_drop_payload_parsing(payload, expected):
    assert dnd.parse_drop_paths(payload) == expected


def test_preview_engine_renders_and_coalesces(photo):
    source = imageio.load_image(photo)
    engine = PreviewEngine()
    try:
        settings = JobSettings()
        for size in (3.0, 4.0, 5.0):
            settings.text.size_percent = size
            engine.request(source, settings, max_dimension=300)

        result = None
        for _ in range(200):
            result = engine.results.get(timeout=5)
            if result.token == 3:
                break
        assert result is not None and result.error is None
        assert max(result.image.size) <= 300
        assert result.full_size == source.size
        assert "text" in result.geometry
        assert result.estimated_bytes and result.estimated_bytes > 0
    finally:
        engine.shutdown()


def test_preview_engine_reports_errors_without_dying(photo):
    source = imageio.load_image(photo)
    engine = PreviewEngine()
    try:
        settings = JobSettings()
        settings.image.enabled = True
        settings.image.path = "/definitely/missing/logo.png"
        engine.request(source, settings, max_dimension=200)
        result = engine.results.get(timeout=5)
        # A missing logo must not take the whole preview down.
        assert result.error is None and result.image is not None
        assert result.warning and "not found" in result.warning
    finally:
        engine.shutdown()


def test_run_async_delivers_a_result():
    import queue

    callbacks: "queue.Queue" = queue.Queue()
    seen = []
    run_async(lambda: 21 * 2, seen.append, seen.append, callbacks).join(timeout=5)
    callbacks.get(timeout=5)()
    assert seen == [42]


def test_run_async_delivers_the_exception_itself():
    """Regression: the failure callback must receive the real exception.

    Binding it in a lambda used to raise NameError instead, because Python
    deletes the ``as exc`` name when the except block ends — which meant a
    failed export crashed the very dialog meant to report it.
    """
    import queue

    callbacks: "queue.Queue" = queue.Queue()
    failures = []

    def boom():
        raise ValueError("disk is full")

    run_async(boom, lambda _v: None, failures.append, callbacks).join(timeout=5)
    callbacks.get(timeout=5)()
    assert isinstance(failures[0], ValueError)
    assert str(failures[0]) == "disk is full"


def test_size_estimate_tracks_the_output_dimensions(photo):
    from watermark.core import render
    from watermark.core.models import ExportSettings

    source = imageio.load_image(photo)
    preview = render.render(source, JobSettings(), preview_max_dimension=300)
    settings = ExportSettings(image_format=ImageFormat.JPEG)

    small = estimate_output_bytes(preview, settings, source, (400, 300))
    large = estimate_output_bytes(preview, settings, source, (1600, 1200))
    assert small and large and large > small * 3


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #


@pytest.fixture
def app():
    from watermark.ui.app import WatermarkApp

    instance = WatermarkApp()
    yield instance
    instance.engine.shutdown()
    instance.root.destroy()


def pump(app, seconds: float = 1.2) -> None:
    """Run the Tk event loop long enough for a preview frame to arrive."""
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.01)


@needs_display
def test_window_starts_empty(app):
    assert app.source is None
    assert "Open an image" in app.status_var.get()


@needs_display
def test_loading_an_image_produces_a_preview(app, photo):
    app.load(photo)
    pump(app)
    assert app.source is not None
    assert app.canvas.image_size is not None
    assert "800 × 600" in app.status_var.get()


@needs_display
def test_editing_a_setting_re_renders(app, photo):
    app.load(photo)
    pump(app)
    app.vars["text.size_percent"].set(12.0)
    pump(app)
    assert app.settings.text.size_percent == 12.0
    assert app.canvas.layer_box("text") is not None


@needs_display
def test_dragging_moves_the_watermark(app, photo):
    app.load(photo)
    pump(app)
    app._on_layer_moved("text", (25.0, 25.0))
    pump(app)
    box = app.canvas.layer_box("text")
    assert box is not None
    assert box[0] == pytest.approx(25, abs=3) and box[1] == pytest.approx(25, abs=3)


@needs_display
def test_undo_and_redo(app, photo):
    app.load(photo)
    pump(app, 0.4)
    app.push_undo()
    app.settings.text.opacity = 12
    app._sync_from_settings()
    pump(app, 0.3)

    app.undo()
    assert app.settings.text.opacity != 12
    app.redo()
    assert app.settings.text.opacity == 12


@needs_display
def test_applying_a_preset_updates_the_widgets(app, photo):
    app.load(photo)
    pump(app, 0.4)
    app.apply_preset("Confidential — tiled")
    pump(app, 0.4)
    assert app.settings.tile.enabled is True
    assert app.vars["tile.enabled"].get() is True
    assert app.text_box.get("1.0", "end-1c") == "CONFIDENTIAL"


@needs_display
def test_preset_keeps_the_users_output_folder(app, tmp_path):
    app.settings.output.directory = str(tmp_path)
    app.apply_preset("Signature")
    assert app.settings.output.directory == str(tmp_path)


@needs_display
def test_theme_toggle_rebuilds_cleanly(app, photo):
    app.load(photo)
    pump(app, 0.4)
    before = app.palette.name
    app.toggle_theme()
    pump(app, 0.4)
    assert app.palette.name != before
    # Every control must be re-bound after the rebuild.
    assert app.vars and app.text_box.winfo_exists()
    app.toggle_theme()
    pump(app, 0.3)
    assert app.palette.name == before


@needs_display
def test_export_controls_track_the_format(app, photo):
    app.load(photo)
    pump(app, 0.4)
    app.settings.export.image_format = ImageFormat.PNG
    app._update_export_state()
    assert str(app.png_level_slider.scale.cget("state")) == "normal"

    app.settings.export.image_format = ImageFormat.JPEG
    app._update_export_state()
    assert str(app.quality_slider.scale.cget("state")) == "normal"


@needs_display
def test_status_bar_shows_the_export_estimate(app, photo):
    app.load(photo)
    pump(app)
    app.settings.export.image_format = ImageFormat.WEBP
    app.settings.export.resize_mode = ResizeMode.FIT_WITHIN
    app.settings.export.max_dimension = 400
    app._sync_from_settings()
    pump(app)
    status = app.status_var.get()
    assert "WEBP" in status and "400" in status


@needs_display
def test_a_failed_export_reports_instead_of_crashing(app, photo, tmp_path, monkeypatch):
    """The whole run_async error path, end to end through the window."""
    from watermark.core.errors import ExportError

    shown = []
    monkeypatch.setattr(
        "watermark.ui.app.messagebox.showerror",
        lambda title, message, **kwargs: shown.append(message),
    )
    monkeypatch.setattr(
        "watermark.ui.app.filedialog.asksaveasfilename",
        lambda **kwargs: str(tmp_path / "out.png"),
    )
    monkeypatch.setattr(
        "watermark.core.export.save_image",
        lambda *a, **k: (_ for _ in ()).throw(ExportError("no space left on device")),
    )

    app.load(photo)
    pump(app, 0.5)
    app.save_as()
    pump(app, 1.0)

    assert shown and "no space left on device" in shown[0]
    assert app.busy is False


@needs_display
def test_batch_dialog_opens_with_the_current_file(app, photo):
    from watermark.ui.dialogs import BatchDialog

    app.load(photo)
    pump(app, 0.4)
    dialog = BatchDialog(app.root, app.palette, app.settings.copy(), app.config, [photo])
    pump(app, 0.3)
    assert dialog.files == [photo]
    dialog.destroy()


@needs_display
def test_settings_persist_across_restarts(app, photo, tmp_path):
    from watermark.core import config as config_module

    app.settings.text.text = "persist me"
    app.config.window_geometry = "900x700+10+10"
    app.config.store_settings(app.settings)
    config_module.save_config(app.config)

    assert config_module.load_config().restore_settings().text.text == "persist me"
