# Changelog

## 2.0.0

A rewrite around a GUI-free core engine, with batch processing, compression and
a live-preview interface.

### Added

- **Compression and export**: PNG / JPEG / WebP / AVIF / TIFF conversion, per
  format quality, progressive JPEG, chroma subsampling, PNG compression effort
  and palette quantization, and a "fit under N KB" search that trades quality
  first and dimensions only if it must.
- **Resize on export**: fit within a maximum, scale by percentage, or crop to an
  exact size.
- **Batch processing**: files or folders (optionally recursive), parallel
  workers, progress, cancellation, filename patterns with tokens, and
  rename/overwrite/skip conflict policies.
- **Privacy**: EXIF and GPS stripped by default, with ICC handling separate.
- **Live preview** rendered off the UI thread, with zoom, pan and
  drag-to-position for each watermark.
- **Nine built-in presets** plus user presets saved as JSON.
- **Watermark controls**: nine-point placement with percentage margins, opacity,
  rotation, outline, drop shadow, backdrop plate, multi-line text, font picker
  over bundled and system fonts, and `{token}` substitution.
- **Tiled watermarks** built from the text *or* the logo, with angle, spacing,
  stagger and an opacity that means the same thing regardless of other tabs.
- **A command-line interface** (`apply`, `batch`, `compress`, `info`, `presets`)
  with a JSON report mode.
- Undo/redo, recent files, keyboard shortcuts, dark/light themes, persisted
  settings, rotating file logs, and a test suite.

### Fixed

- **EXIF orientation is now applied on load.** Phone photos were previously
  watermarked sideways.
- **Saving an RGBA image as JPEG no longer crashes.** Transparency is composited
  onto a configurable matte colour instead of raising `OSError`.
- **Failures are visible.** Errors went to `print()`, which nobody sees when the
  app is launched from a desktop icon; they are now typed exceptions surfaced in
  the interface and written to a log file.
- **Drag-and-drop is optional.** `tkinterdnd2` was a hard import, so a failed
  install meant no app at all; it is now detected at runtime.
- **Huge images are refused with a clear message** rather than freezing the
  window.
- **Text outlines** are drawn in one pass with Pillow's `stroke_width` instead
  of 25 offset draws.
- **Tiling** builds each row once and reuses it, turning `columns × rows` paste
  operations into `columns + rows`, and is capped so zero spacing cannot hang
  the app.
- **Nothing overwrites your originals by accident** — an output path that would
  land on its own source is renamed.
- Settings, colour choices and positions are no longer trapped behind a chain of
  modal pop-ups; the image stays on screen and every control updates it live.

### Changed

- The application moved into the `watermark` package; `watermark_app.py` remains
  as a launcher.
- Bundled fonts moved to `watermark/assets/fonts/` so they survive a
  `pip install`. PyInstaller's `sys._MEIPASS` and the old top-level `fonts/`
  folder are still checked at runtime.
- `minified.py`, an obfuscated duplicate of the 1.x script, was removed.
