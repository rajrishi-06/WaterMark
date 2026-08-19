"""Batch processing.

Watermarking one photo at a time was the original app's biggest practical
limit — nobody shoots one photo.  This module applies a :class:`JobSettings` to
a whole list of files, in parallel, with progress reporting and a working
cancel button.

Callbacks fire on worker threads.  A GUI must marshal them onto its own thread;
:mod:`watermark.ui.app` does that with a queue.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from PIL import Image

from . import export, imageio, render, tokens
from .errors import WatermarkError
from .logs import get_logger
from .models import ConflictPolicy, ImageFormat, JobSettings
from .transforms import apply_transform

_LOG = get_logger(__name__)


class ItemStatus(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class BatchItem:
    """The outcome for one input file."""

    source: str
    status: ItemStatus
    output: Optional[str] = None
    message: str = ""
    input_bytes: int = 0
    output_bytes: int = 0
    size: Optional[Tuple[int, int]] = None

    @property
    def saved_bytes(self) -> int:
        if self.status is not ItemStatus.OK or not self.input_bytes:
            return 0
        return self.input_bytes - self.output_bytes


@dataclass
class BatchReport:
    """Aggregate result of a run."""

    items: List[BatchItem] = field(default_factory=list)
    cancelled: bool = False
    elapsed: float = 0.0

    @property
    def succeeded(self) -> List[BatchItem]:
        return [item for item in self.items if item.status is ItemStatus.OK]

    @property
    def failed(self) -> List[BatchItem]:
        return [item for item in self.items if item.status is ItemStatus.ERROR]

    @property
    def skipped(self) -> List[BatchItem]:
        return [item for item in self.items if item.status is ItemStatus.SKIPPED]

    @property
    def input_bytes(self) -> int:
        return sum(item.input_bytes for item in self.succeeded)

    @property
    def output_bytes(self) -> int:
        return sum(item.output_bytes for item in self.succeeded)

    def summary(self) -> str:
        """One-line result suitable for a status bar or the CLI."""
        parts = [f"{len(self.succeeded)} processed"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        if self.succeeded and self.input_bytes:
            delta = self.input_bytes - self.output_bytes
            direction = "smaller" if delta >= 0 else "larger"
            percent = abs(delta) / self.input_bytes * 100
            parts.append(
                f"{export.human_size(abs(delta))} {direction} ({percent:.0f}%)"
            )
        if self.cancelled:
            parts.append("cancelled")
        return ", ".join(parts)


# --------------------------------------------------------------------------- #
# Output naming
# --------------------------------------------------------------------------- #


def apply_extension(filename: str, image_format: ImageFormat) -> str:
    """Force ``filename``'s extension to match the chosen output format."""
    if image_format is ImageFormat.KEEP:
        return filename
    stem = os.path.splitext(filename)[0]
    return stem + image_format.extension


def unique_path(path: str, taken: Set[str]) -> str:
    """Return ``path`` or the first free ``name (2).ext`` variant."""
    if path.lower() not in taken and not os.path.exists(path):
        return path
    stem, extension = os.path.splitext(path)
    for counter in range(2, 10_000):
        candidate = f"{stem} ({counter}){extension}"
        if candidate.lower() not in taken and not os.path.exists(candidate):
            return candidate
    raise WatermarkError(f"Could not find a free filename near {path}")


def resolve_output_path(
    source_path: str,
    settings: JobSettings,
    image_format: ImageFormat,
    index: int = 1,
    count: int = 1,
    size: Optional[Tuple[int, int]] = None,
    taken: Optional[Set[str]] = None,
) -> Optional[str]:
    """Work out where one processed file should be written.

    Returns ``None`` when the conflict policy says to skip.  A result that
    would clobber the *source* file is always renamed instead — silently
    destroying an original is never the right default.
    """
    taken = taken if taken is not None else set()
    context = tokens.build_context(source_path, size, index, count)
    pattern = settings.output.filename_pattern or "{name}_wm{ext}"
    filename = tokens.expand(pattern, context).strip() or os.path.basename(source_path)
    filename = apply_extension(os.path.basename(filename), image_format)

    directory = settings.output.directory or os.path.dirname(os.path.abspath(source_path))
    candidate = os.path.join(os.path.abspath(directory), filename)

    if os.path.abspath(candidate) == os.path.abspath(source_path):
        return unique_path(candidate, taken)

    policy = settings.output.conflict
    if candidate.lower() in taken:
        # Two inputs mapped to the same output within this run.
        return None if policy is ConflictPolicy.SKIP else unique_path(candidate, taken)
    if os.path.exists(candidate):
        if policy is ConflictPolicy.SKIP:
            return None
        if policy is ConflictPolicy.RENAME:
            return unique_path(candidate, taken)
    return candidate


# --------------------------------------------------------------------------- #
# Single-file processing
# --------------------------------------------------------------------------- #


def process_image(
    source: imageio.SourceImage,
    settings: JobSettings,
    index: int = 1,
    count: int = 1,
    overlay: Optional[Image.Image] = None,
) -> Tuple[Image.Image, Dict[str, str]]:
    """Render an already-loaded image and return it with its token context."""
    size = apply_transform(source.image, settings.transform).size
    context = tokens.build_context(source.path, size, index, count)
    return render.render(source, settings, context, overlay=overlay), context


def process_file(
    path: str,
    settings: JobSettings,
    index: int = 1,
    count: int = 1,
    overlay: Optional[Image.Image] = None,
    taken: Optional[Set[str]] = None,
    dry_run: bool = False,
) -> BatchItem:
    """Load, watermark, encode and write one file.

    Every failure is captured in the returned :class:`BatchItem`; one bad file
    must never abort a 500-photo run.
    """
    try:
        source = imageio.load_image(path)
    except WatermarkError as exc:
        return BatchItem(source=path, status=ItemStatus.ERROR, message=str(exc))

    try:
        image, _context = process_image(source, settings, index, count, overlay)
        # The raw setting is passed, not the resolved format: "keep original"
        # must leave the source's own extension alone.
        destination = resolve_output_path(
            path, settings, settings.export.image_format, index, count, image.size, taken
        )
        if destination is None:
            return BatchItem(
                source=path,
                status=ItemStatus.SKIPPED,
                message="Output file already exists.",
                input_bytes=source.file_size,
            )

        if dry_run:
            result = export.render_to_bytes(image, settings.export, source)
        else:
            result = export.save_image(image, destination, settings.export, source)

        message = "; ".join(result.notes + source.warnings)
        return BatchItem(
            source=path,
            status=ItemStatus.OK,
            output=destination,
            message=message,
            input_bytes=source.file_size,
            output_bytes=result.byte_count,
            size=result.size,
        )
    except WatermarkError as exc:
        return BatchItem(source=path, status=ItemStatus.ERROR, message=str(exc))
    except Exception as exc:  # pragma: no cover - unexpected Pillow/OS failures
        _LOG.exception("Unexpected failure processing %s", path)
        return BatchItem(source=path, status=ItemStatus.ERROR, message=f"{type(exc).__name__}: {exc}")
    finally:
        source.image.close()


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

ProgressCallback = Callable[[int, int, str], None]
ItemCallback = Callable[[BatchItem], None]


class BatchRunner:
    """Runs a job over many files with progress, cancellation and pooling."""

    def __init__(
        self,
        files: Sequence[str],
        settings: JobSettings,
        workers: int = 4,
        on_progress: Optional[ProgressCallback] = None,
        on_item: Optional[ItemCallback] = None,
        on_finish: Optional[Callable[[BatchReport], None]] = None,
        dry_run: bool = False,
    ) -> None:
        self.files = list(files)
        self.settings = settings
        # Pillow releases the GIL around encode/decode, so threads really do
        # overlap; more than a handful just thrashes memory on large images.
        self.workers = max(1, min(int(workers), 8, len(self.files) or 1))
        self.on_progress = on_progress
        self.on_item = on_item
        self.on_finish = on_finish
        self.dry_run = dry_run

        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._taken: Set[str] = set()
        self._done = 0
        self._thread: Optional[threading.Thread] = None

    # -- control ---------------------------------------------------------- #

    def cancel(self) -> None:
        """Ask the run to stop; in-flight files finish, queued ones do not."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> threading.Thread:
        """Run in a background thread and return it (for the GUI)."""
        if self.running:
            raise WatermarkError("This batch is already running.")
        self._thread = threading.Thread(target=self._run_and_report, daemon=True)
        self._thread.start()
        return self._thread

    def _run_and_report(self) -> None:
        report = self.run()
        if self.on_finish:
            self.on_finish(report)

    # -- work -------------------------------------------------------------- #

    def _claim(self, item_index: int, path: str) -> Optional[str]:
        """Reserve an output path so concurrent workers cannot collide."""
        with self._lock:
            destination = resolve_output_path(
                path,
                self.settings,
                self.settings.export.image_format,
                item_index + 1,
                len(self.files),
                None,
                self._taken,
            )
            if destination is not None:
                self._taken.add(destination.lower())
            return destination

    def _process(self, item_index: int, path: str, overlay: Optional[Image.Image]) -> BatchItem:
        if self._cancel.is_set():
            return BatchItem(source=path, status=ItemStatus.SKIPPED, message="Cancelled")

        destination = self._claim(item_index, path)
        if destination is None:
            item = BatchItem(
                source=path, status=ItemStatus.SKIPPED, message="Output file already exists."
            )
        else:
            forced = JobSettings.from_json_dict(self.settings.to_json_dict())
            forced.output.directory = os.path.dirname(destination)
            forced.output.filename_pattern = os.path.basename(destination)
            forced.output.conflict = ConflictPolicy.OVERWRITE
            item = process_file(
                path,
                forced,
                item_index + 1,
                len(self.files),
                overlay=overlay,
                dry_run=self.dry_run,
            )

        with self._lock:
            self._done += 1
            done = self._done
        if self.on_item:
            self.on_item(item)
        if self.on_progress:
            self.on_progress(done, len(self.files), path)
        return item

    def run(self) -> BatchReport:
        """Process every file and return the report (blocking)."""
        started = time.monotonic()
        report = BatchReport()
        if not self.files:
            return report

        # Decode the logo once for the whole run instead of once per photo.
        overlay: Optional[Image.Image] = None
        if self.settings.image.enabled and self.settings.image.path:
            try:
                overlay = imageio.load_overlay(self.settings.image.path)
            except WatermarkError as exc:
                _LOG.warning("Logo could not be loaded: %s", exc)

        if self.workers == 1:
            results = [self._process(i, path, overlay) for i, path in enumerate(self.files)]
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                results = list(
                    pool.map(
                        lambda pair: self._process(pair[0], pair[1], overlay),
                        list(enumerate(self.files)),
                    )
                )

        report.items = results
        report.cancelled = self._cancel.is_set()
        report.elapsed = time.monotonic() - started
        _LOG.info("Batch finished: %s in %.1fs", report.summary(), report.elapsed)
        return report


def collect_inputs(paths: Sequence[str], recursive: bool = False) -> List[str]:
    """Expand a mix of files and folders into a sorted list of image files."""
    collected: List[str] = []
    seen: Set[str] = set()
    for entry in paths:
        candidates = (
            imageio.iter_image_files(entry, recursive)
            if os.path.isdir(entry)
            else [entry]
        )
        for candidate in candidates:
            key = os.path.abspath(candidate).lower()
            if key not in seen and imageio.is_supported(candidate):
                seen.add(key)
                collected.append(candidate)
    return collected
