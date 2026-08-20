"""Background rendering for the GUI.

A live preview that blocks the UI thread is not a live preview.  Every render
happens on a worker thread; rapid edits coalesce so that dragging a slider
renders the *latest* state rather than queueing thirty stale frames.
"""

from __future__ import annotations

import functools
import queue
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from PIL import Image

from ..core import export, render, tokens
from ..core.imageio import SourceImage
from ..core.logs import get_logger
from ..core.models import ExportSettings, ImageFormat, JobSettings, ResizeMode
from ..core.transforms import apply_transform, scaled_size

_LOG = get_logger(__name__)


@dataclass
class PreviewResult:
    """One finished preview frame."""

    image: Optional[Image.Image] = None
    #: The same frame with no watermark, for the compare view.
    original: Optional[Image.Image] = None
    geometry: Dict[str, Tuple[int, int, int, int]] = field(default_factory=dict)
    full_size: Tuple[int, int] = (0, 0)
    preview_size: Tuple[int, int] = (0, 0)
    output_size: Tuple[int, int] = (0, 0)
    estimated_bytes: Optional[int] = None
    output_format: Optional[ImageFormat] = None
    error: Optional[str] = None
    #: Non-fatal problem worth telling the user about (e.g. a missing logo).
    warning: Optional[str] = None
    token: int = 0


def estimate_output_bytes(
    preview: Image.Image,
    settings: ExportSettings,
    source: Optional[SourceImage],
    output_size: Tuple[int, int],
) -> Optional[int]:
    """Approximate the exported file size from the preview frame.

    Encoding the full-resolution image on every keystroke would be far too
    slow, so the small preview is encoded and scaled by the pixel-count ratio.
    Compressed size tracks pixel count closely enough at a fixed quality for
    this to be a useful "will this fit in an email?" signal — it is labelled as
    an estimate in the UI.
    """
    try:
        preview_pixels = max(1, preview.width * preview.height)
        # Estimate the *unresized* encoding, then scale to the real output.
        probe = ExportSettings(**{**settings.__dict__, "resize_mode": ResizeMode.NONE,
                                  "target_size_kb": None})
        result = export.encode(preview, probe, source)
        ratio = (output_size[0] * output_size[1]) / preview_pixels
        return int(result.byte_count * ratio)
    except Exception:  # pragma: no cover - estimation must never break a render
        _LOG.debug("Size estimation failed", exc_info=True)
        return None


class PreviewEngine:
    """Serialises preview renders onto one worker thread."""

    def __init__(self) -> None:
        self.results: "queue.Queue[PreviewResult]" = queue.Queue()
        self._pending: Optional[Tuple[int, SourceImage, JobSettings, int, Optional[str]]] = None
        self._condition = threading.Condition()
        self._stop = False
        self._token = 0
        self._overlay_cache: Dict[str, Image.Image] = {}
        self._thread = threading.Thread(target=self._loop, daemon=True, name="preview")
        self._thread.start()

    # -- public API --------------------------------------------------------- #

    def request(
        self,
        source: SourceImage,
        settings: JobSettings,
        max_dimension: int = 1100,
    ) -> int:
        """Queue a render, replacing any request that has not started yet."""
        with self._condition:
            self._token += 1
            token = self._token
            # Settings are copied because the UI thread keeps mutating its own.
            self._pending = (token, source, settings.copy(), max_dimension, None)
            self._condition.notify()
        return token

    def shutdown(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify()

    def drain(self) -> Optional[PreviewResult]:
        """Return the newest finished frame, discarding superseded ones."""
        latest: Optional[PreviewResult] = None
        while True:
            try:
                latest = self.results.get_nowait()
            except queue.Empty:
                return latest

    # -- worker ------------------------------------------------------------- #

    def _overlay(self, settings: JobSettings) -> Tuple[Optional[Image.Image], Optional[str]]:
        """Decode the logo once and reuse it across frames.

        Returns ``(overlay, problem)``.  A logo that has been moved or deleted
        must not blank the preview — the rest of the image still renders and the
        problem is reported in the status bar instead.
        """
        path = settings.image.path
        if not settings.image.enabled or not path:
            return None, None
        if path not in self._overlay_cache:
            from ..core.imageio import load_overlay

            try:
                self._overlay_cache.clear()
                self._overlay_cache[path] = load_overlay(path)
            except Exception as exc:
                _LOG.debug("Logo could not be loaded for the preview: %s", exc)
                return None, str(exc)
        return self._overlay_cache.get(path), None

    def _loop(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._stop:
                    self._condition.wait()
                if self._stop:
                    return
                token, source, settings, max_dimension, _ = self._pending
                self._pending = None
            self.results.put(self._render(token, source, settings, max_dimension))

    def _render(
        self, token: int, source: SourceImage, settings: JobSettings, max_dimension: int
    ) -> PreviewResult:
        try:
            overlay, problem = self._overlay(settings)
            if problem:
                # Draw everything else rather than failing the whole frame.
                settings.image.enabled = False
            full_size = apply_transform(source.image, settings.transform).size
            context = tokens.build_context(source.path, full_size)
            image = render.render(
                source, settings, context,
                preview_max_dimension=max_dimension,
                overlay=overlay,
            )
            # The same geometry with every layer switched off: what the photo
            # looked like before the app touched it.
            bare = settings.copy()
            bare.text.enabled = False
            bare.image.enabled = False
            bare.tile.enabled = False
            original = render.render(
                source, bare, context, preview_max_dimension=max_dimension
            )
            geometry = render.layer_geometry(source, settings, context, canvas_size=image.size)
            output_size = scaled_size(
                full_size,
                settings.export.resize_mode,
                settings.export.max_dimension,
                settings.export.scale_percent,
                (settings.export.exact_width, settings.export.exact_height),
            )
            estimate = estimate_output_bytes(image, settings.export, source, output_size)
            if settings.export.target_size_kb:
                estimate = min(estimate or 0, settings.export.target_size_kb * 1024) or None
            return PreviewResult(
                image=image,
                original=original,
                geometry=geometry,
                full_size=full_size,
                preview_size=image.size,
                output_size=output_size,
                estimated_bytes=estimate,
                output_format=export.resolve_format(settings.export, source),
                warning=problem,
                token=token,
            )
        except Exception as exc:
            _LOG.warning("Preview render failed: %s", exc)
            _LOG.debug(traceback.format_exc())
            return PreviewResult(error=str(exc), token=token)


def run_async(
    work: Callable[[], Any],
    on_done: Callable[[Any], None],
    on_error: Callable[[Exception], None],
    result_queue: "queue.Queue[Callable[[], None]]",
) -> threading.Thread:
    """Run ``work`` off-thread and post the callback onto ``result_queue``.

    The queue is drained by the Tk main loop, which is the only place Tk
    widgets may legally be touched.
    """

    def target() -> None:
        try:
            value = work()
        except Exception as exc:  # noqa: BLE001 - reported to the user verbatim
            # functools.partial binds the exception now.  A lambda would not:
            # Python deletes the `as exc` name when the except block ends, so
            # the callback would raise NameError instead of showing the error.
            result_queue.put(functools.partial(on_error, exc))
        else:
            result_queue.put(functools.partial(on_done, value))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread
