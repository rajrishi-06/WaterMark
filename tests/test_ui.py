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


def test_light_palette_separates_surfaces_from_controls():
    """A white button face on a white panel makes the button invisible."""
    from watermark.ui.theme import DARK, LIGHT

    for palette in (LIGHT, DARK):
        assert palette.field != palette.panel, palette.name
        assert palette.border != palette.panel, palette.name


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
def test_png_only_controls_are_disabled_for_other_formats(app, photo):
    """Palette quantization does nothing outside PNG, so it must not look live."""
    app.load(photo)
    pump(app, 0.4)

    app.settings.export.image_format = ImageFormat.PNG
    app._update_export_state()
    assert str(app.palette_check.cget("state")) == "normal"
    assert str(app.palette_spin.cget("state")) == "normal"

    app.settings.export.image_format = ImageFormat.WEBP
    app._update_export_state()
    assert str(app.palette_check.cget("state")) == "disabled"
    assert str(app.palette_spin.cget("state")) == "disabled"


@needs_display
def test_resize_controls_track_the_resize_mode(app, photo):
    app.load(photo)
    pump(app, 0.4)
    app.settings.export.resize_mode = ResizeMode.PERCENT
    app._update_export_state()
    assert str(app.scale_slider.scale.cget("state")) == "normal"
    assert str(app.max_dimension_slider.scale.cget("state")) == "disabled"


@needs_display
def test_menubar_is_themed(app):
    """The menubar was Tk's default grey strip above a dark window."""
    menubar = app.root.nametowidget(app.root.cget("menu"))
    assert str(menubar.cget("background")) == app.palette.panel


@needs_display
def test_every_tab_builds_and_can_be_selected(app, photo):
    app.load(photo)
    pump(app, 0.4)
    labels = [app.notebook.tab(i, "text") for i in range(app.notebook.index("end"))]
    assert labels == ["Text", "Logo", "Pattern", "Adjust", "Export"]
    for index in range(len(labels)):
        app.notebook.select(index)
        pump(app, 0.15)
        assert app.notebook.index("current") == index


@needs_display
def test_rotate_buttons_swap_the_canvas_and_keep_the_watermark_upright(app, photo):
    app.load(photo)
    pump(app)
    before = app.canvas.image_size
    app._set_rotation(90)
    pump(app)
    after = app.canvas.image_size
    assert after[0] < after[1] < before[0] + after[1]
    assert (before[1], before[0]) == after
    assert app.canvas.layer_box("text") is not None


@needs_display
def test_preferences_dialog_applies_and_persists(app):
    from watermark.ui.dialogs import PreferencesDialog

    called = []
    dialog = PreferencesDialog(app.root, app.palette, app.config, lambda: called.append(True))
    pump(app, 0.3)
    dialog.preview_var.set(1500)
    dialog.workers_var.set(7)
    dialog._apply()
    pump(app, 0.2)

    assert called == [True]
    assert app.config.preview_resolution == 1500
    assert app.config.batch_workers == 7


@needs_display
def test_batch_dialog_runs_to_completion(app, photo_folder, tmp_path, monkeypatch):
    from watermark.ui import dialogs

    monkeypatch.setattr(dialogs.messagebox, "askyesno", lambda *a, **k: False)
    folder, _ = photo_folder
    output = tmp_path / "batch_out"

    dialog = dialogs.BatchDialog(app.root, app.palette, app.settings.copy(), app.config,
                                 [str(folder)])
    pump(app, 0.3)
    # Dropping a folder on the dialog picks up sub-folders too.
    assert len(dialog.files) == 5

    dialog.output_var.set(str(output))
    dialog.pattern_var.set("{name}_{index}of{count}{ext}")
    dialog._start()
    for _ in range(200):
        pump(app, 0.05)
        if dialog.runner is None and dialog.progress_var.get() >= 100:
            break

    written = sorted(p.name for p in output.iterdir())
    assert len(written) == 5
    assert "shot0_1of5.jpg" in written
    assert "processed" in dialog.status_var.get()
    dialog.destroy()


@needs_display
def test_batch_dialog_stops_polling_once_closed(app, photo, capsys):
    """A pending after-job used to fire against the destroyed dialog."""
    from watermark.ui import dialogs

    dialog = dialogs.BatchDialog(app.root, app.palette, app.settings.copy(), app.config, [photo])
    pump(app, 0.3)
    dialog.destroy()
    pump(app, 0.5)
    assert "invalid command name" not in capsys.readouterr().err


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
def test_crop_mode_round_trip(app, photo):
    """Enter crop, drag a handle, leave — the render must follow."""
    from watermark.core.transforms import apply_transform, normalize_crop

    app.load(photo)
    pump(app)
    assert "No crop" in app.crop_label.cget("text")
    assert str(app.crop_clear_button.cget("state")) == "disabled"

    app.toggle_crop_mode()
    pump(app)
    assert app.canvas.crop_mode is True
    assert "Finish" in app.crop_button.cget("text")
    width, height = app.canvas.image_size
    assert app.canvas.crop_rect == [0.0, 0.0, float(width), float(height)]

    app.canvas._crop_start = list(app.canvas.crop_rect)
    app.canvas._resize_crop("se", width * 0.75, height * 0.75)
    pump(app, 0.3)
    assert normalize_crop(app.settings.transform.crop) is not None
    assert "Cropped to" in app.crop_label.cget("text")
    assert str(app.crop_clear_button.cget("state")) == "normal"

    app.toggle_crop_mode()
    pump(app)
    assert app.canvas.crop_mode is False
    cropped = apply_transform(app.source.image, app.settings.transform).size
    assert cropped[0] < app.source.size[0] and cropped[1] < app.source.size[1]


@needs_display
def test_crop_mode_shows_the_uncropped_frame(app, photo):
    """The selection needs the whole image underneath it to sit on."""
    app.load(photo)
    pump(app)
    source_ratio = app.source.size[0] / app.source.size[1]

    # An asymmetric crop, so the framing visibly differs from the original.
    app.settings.transform.crop = [0.3, 0.0, 0.3, 0.0]
    app._sync_from_settings()
    pump(app)
    cropped_ratio = app.canvas.image_size[0] / app.canvas.image_size[1]
    assert cropped_ratio < source_ratio * 0.75

    app.toggle_crop_mode()
    pump(app)
    full_ratio = app.canvas.image_size[0] / app.canvas.image_size[1]
    assert full_ratio == pytest.approx(source_ratio, abs=0.02)
    # …and the stored crop is untouched by merely looking at the full frame.
    assert app.settings.transform.crop == [0.3, 0.0, 0.3, 0.0]
    app.toggle_crop_mode()


@needs_display
def test_crop_handles_clamp_to_the_frame(app, photo):
    app.load(photo)
    pump(app)
    app.toggle_crop_mode()
    pump(app)
    width, height = app.canvas.image_size

    app.canvas._crop_start = list(app.canvas.crop_rect)
    app.canvas._resize_crop("nw", -900, -900)
    assert app.canvas.crop_rect[0] >= 0 and app.canvas.crop_rect[1] >= 0

    app.canvas._crop_start = list(app.canvas.crop_rect)
    app.canvas._resize_crop("w", width * 5, height / 2)
    assert app.canvas.crop_rect[0] < app.canvas.crop_rect[2]
    app.toggle_crop_mode()


@needs_display
def test_clearing_the_crop_is_undoable(app, photo):
    from watermark.core.transforms import normalize_crop

    app.load(photo)
    pump(app)
    app.settings.transform.crop = [0.2, 0.2, 0.2, 0.2]
    app._refresh_crop_controls()
    app.clear_crop()
    pump(app, 0.3)
    assert normalize_crop(app.settings.transform.crop) is None
    app.undo()
    assert normalize_crop(app.settings.transform.crop) is not None


@needs_display
def test_compare_shows_the_image_without_its_watermark(app, photo):
    app.load(photo)
    pump(app)
    assert app.canvas._original is not None

    app.toggle_compare()
    assert app.canvas.comparing is True
    assert app.compare_var.get() is True

    app.toggle_compare()
    assert app.canvas.comparing is False

    app._peek_original(True)
    assert app.canvas.comparing is True
    app._peek_original(False)
    assert app.canvas.comparing is False


@needs_display
def test_compare_is_inert_with_no_image(app):
    app.toggle_compare()
    assert app.canvas.comparing is False
    assert app.compare_var.get() is False


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
