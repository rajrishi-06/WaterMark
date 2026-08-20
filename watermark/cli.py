"""Command-line interface.

Everything the GUI can do is scriptable, which is what makes the app usable
from a build step, a cron job or a Finder/Explorer "send to" action::

    watermark apply photo.jpg --text "© 2026 Me" -o out.jpg
    watermark batch ~/Photos -o ~/Watermarked --preset "Signature" --recursive
    watermark compress *.png --format webp --target-size 300k -o small/
    watermark info photo.jpg
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import List, Optional, Sequence

from . import APP_NAME, __version__
from .core import batch as batch_module
from .core import export, imageio, logs, presets
from .core.errors import WatermarkError
from .core.models import (
    Anchor,
    ConflictPolicy,
    ImageFormat,
    JobSettings,
    ResizeMode,
    TileSource,
)

_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([kmg]?)b?\s*$", re.IGNORECASE)


def parse_crop(text: str) -> List[float]:
    """Parse ``L,T,R,B`` percentages trimmed off each edge into fractions."""
    parts = [piece.strip() for piece in text.replace(" ", ",").split(",") if piece.strip()]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"--crop expects four percentages, LEFT,TOP,RIGHT,BOTTOM (got '{text}')"
        )
    try:
        values = [float(piece.rstrip("%")) / 100.0 for piece in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"--crop values must be numbers (got '{text}')") from None
    if any(value < 0 for value in values):
        raise argparse.ArgumentTypeError("--crop percentages cannot be negative")
    if values[0] + values[2] >= 1 or values[1] + values[3] >= 1:
        raise argparse.ArgumentTypeError("--crop would remove the whole image")
    return values


def parse_size(text: str) -> int:
    """Parse ``500k``, ``1.5M``, ``250000`` into kilobytes.

    A bare number means kilobytes, which is what people mean when they say
    "keep it under 500".
    """
    match = _SIZE_RE.match(text)
    if not match:
        raise argparse.ArgumentTypeError(f"'{text}' is not a size like 500k or 2M")
    value = float(match.group(1))
    multiplier = {"": 1.0, "k": 1.0, "m": 1024.0, "g": 1024.0 * 1024.0}[match.group(2).lower()]
    kilobytes = int(round(value * multiplier))
    if kilobytes < 1:
        raise argparse.ArgumentTypeError("size must be at least 1 KB")
    return kilobytes


# --------------------------------------------------------------------------- #
# Argument wiring
# --------------------------------------------------------------------------- #


def add_watermark_options(parser: argparse.ArgumentParser) -> None:
    """Options that describe the watermark itself."""
    group = parser.add_argument_group("watermark")
    group.add_argument("--preset", help="start from a named preset")
    group.add_argument("--text", help="watermark text; supports {year}, {filename}, {date} …")
    group.add_argument("--no-text", action="store_true", help="disable the text watermark")
    group.add_argument("--font", help="font family or path to a .ttf/.otf file")
    group.add_argument("--size", type=float, metavar="PCT",
                       help="text size as a %% of the image (default 5)")
    group.add_argument("--color", help="text colour, e.g. '#ffffff' or 'red'")
    group.add_argument("--opacity", type=int, metavar="PCT", help="text opacity 0-100")
    group.add_argument("--rotate-text", type=float, metavar="DEG", help="rotate the text")
    group.add_argument("--outline", type=float, metavar="PCT",
                       help="outline thickness as a %% of the text size (0 disables)")
    group.add_argument("--shadow", action="store_true", help="add a drop shadow to the text")
    group.add_argument("--backdrop", action="store_true", help="draw a plate behind the text")
    group.add_argument("--anchor", choices=[a.value for a in Anchor],
                       help="where to place the watermark")
    group.add_argument("--margin", type=float, metavar="PCT", help="margin from the edge")
    group.add_argument("--logo", metavar="FILE", help="image to stamp as a watermark")
    group.add_argument("--logo-scale", type=float, metavar="PCT",
                       help="logo width as a %% of the image width")
    group.add_argument("--logo-opacity", type=int, metavar="PCT", help="logo opacity 0-100")
    group.add_argument("--tile", action="store_true", help="repeat the watermark over the image")
    group.add_argument("--tile-from", choices=[s.value for s in TileSource],
                       help="build the pattern from the text or the logo")
    group.add_argument("--tile-angle", type=float, metavar="DEG", help="pattern angle")
    group.add_argument("--tile-opacity", type=int, metavar="PCT", help="pattern opacity 0-100")
    group.add_argument("--tile-spacing", type=float, metavar="PCT",
                       help="gap between pattern stamps, as a %% of the image")


def add_transform_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("geometry")
    group.add_argument("--rotate", type=float, metavar="DEG", help="rotate the image")
    group.add_argument("--flip-horizontal", action="store_true")
    group.add_argument("--flip-vertical", action="store_true")
    group.add_argument("--crop", type=parse_crop, metavar="L,T,R,B",
                       help="trim these percentages off each edge, e.g. --crop 10,0,10,0")


def add_export_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("export")
    group.add_argument("--format", dest="image_format",
                       choices=[f.value for f in ImageFormat],
                       help="output format (default: keep the original)")
    group.add_argument("--quality", type=int, metavar="N", help="encoder quality 1-100")
    group.add_argument("--lossless", action="store_true", help="lossless WebP/AVIF")
    group.add_argument("--max-dimension", type=int, metavar="PX",
                       help="shrink so neither side exceeds this")
    group.add_argument("--scale", type=float, metavar="PCT", help="scale by percentage")
    group.add_argument("--exact", metavar="WxH", help="crop and resize to exactly WxH")
    group.add_argument("--target-size", type=parse_size, metavar="SIZE",
                       help="compress until the file fits (e.g. 500k, 2M)")
    group.add_argument("--png-colors", type=int, metavar="N",
                       help="quantize PNG output to N palette colours")
    group.add_argument("--keep-metadata", action="store_true",
                       help="preserve EXIF/GPS instead of stripping it")
    group.add_argument("--drop-icc", action="store_true", help="discard the colour profile")
    group.add_argument("--dpi", type=int, metavar="N",
                       help="stamp this DPI on the output (default: keep the source's)")


def build_settings(args: argparse.Namespace) -> JobSettings:
    """Fold the command-line options onto a preset (or the defaults)."""
    if getattr(args, "preset", None):
        settings = presets.load_preset(args.preset)
    else:
        settings = JobSettings()

    text = settings.text
    if getattr(args, "no_text", False):
        text.enabled = False
    if getattr(args, "text", None) is not None:
        text.text = args.text
        text.enabled = True
    for attribute, option in (
        ("font", "font"), ("size_percent", "size"), ("color", "color"),
        ("opacity", "opacity"), ("rotation", "rotate_text"),
        ("outline_width_percent", "outline"),
    ):
        value = getattr(args, option, None)
        if value is not None:
            setattr(text, attribute, value)
    if getattr(args, "shadow", False):
        text.shadow.enabled = True
    if getattr(args, "backdrop", False):
        text.backdrop.enabled = True
    if getattr(args, "anchor", None):
        anchor = Anchor(args.anchor)
        text.placement.anchor = anchor
        settings.image.placement.anchor = anchor
    if getattr(args, "margin", None) is not None:
        text.placement.margin_percent = args.margin
        settings.image.placement.margin_percent = args.margin

    if getattr(args, "logo", None):
        settings.image.enabled = True
        settings.image.path = args.logo
    if getattr(args, "logo_scale", None) is not None:
        settings.image.scale_percent = args.logo_scale
    if getattr(args, "logo_opacity", None) is not None:
        settings.image.opacity = args.logo_opacity

    if getattr(args, "tile", False):
        settings.tile.enabled = True
    if getattr(args, "tile_from", None):
        settings.tile.source = TileSource(args.tile_from)
        settings.tile.enabled = True
    if getattr(args, "tile_angle", None) is not None:
        settings.tile.angle = args.tile_angle
    if getattr(args, "tile_opacity", None) is not None:
        settings.tile.opacity = args.tile_opacity
    if getattr(args, "tile_spacing", None) is not None:
        settings.tile.spacing_x_percent = args.tile_spacing
        settings.tile.spacing_y_percent = args.tile_spacing

    if getattr(args, "rotate", None) is not None:
        settings.transform.rotation = args.rotate
    if getattr(args, "flip_horizontal", False):
        settings.transform.flip_horizontal = True
    if getattr(args, "flip_vertical", False):
        settings.transform.flip_vertical = True
    if getattr(args, "crop", None):
        settings.transform.crop = list(args.crop)

    export_settings = settings.export
    if getattr(args, "image_format", None):
        export_settings.image_format = ImageFormat(args.image_format)
    if getattr(args, "quality", None) is not None:
        export_settings.quality = max(1, min(100, args.quality))
    if getattr(args, "lossless", False):
        export_settings.lossless = True
    if getattr(args, "max_dimension", None) is not None:
        export_settings.resize_mode = ResizeMode.FIT_WITHIN
        export_settings.max_dimension = args.max_dimension
    if getattr(args, "scale", None) is not None:
        export_settings.resize_mode = ResizeMode.PERCENT
        export_settings.scale_percent = args.scale
    if getattr(args, "exact", None):
        try:
            width, height = (int(part) for part in re.split(r"[x×]", args.exact.lower()))
        except ValueError as exc:
            raise WatermarkError(f"--exact expects WxH, got '{args.exact}'") from exc
        export_settings.resize_mode = ResizeMode.EXACT
        export_settings.exact_width, export_settings.exact_height = width, height
    if getattr(args, "target_size", None) is not None:
        export_settings.target_size_kb = args.target_size
    if getattr(args, "png_colors", None) is not None:
        export_settings.png_palette_colors = args.png_colors
    if getattr(args, "keep_metadata", False):
        export_settings.strip_metadata = False
    if getattr(args, "drop_icc", False):
        export_settings.keep_icc_profile = False
    if getattr(args, "dpi", None):
        export_settings.dpi = args.dpi
    return settings


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def command_apply(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    source_path = args.image

    if args.output:
        settings.output.directory = os.path.dirname(os.path.abspath(args.output)) or "."
        settings.output.filename_pattern = os.path.basename(args.output)
        settings.output.conflict = ConflictPolicy.OVERWRITE
        inferred = export.format_for_path(args.output)
        if inferred and settings.export.image_format is ImageFormat.KEEP:
            settings.export.image_format = inferred
    else:
        settings.output.directory = os.path.dirname(os.path.abspath(source_path))

    item = batch_module.process_file(source_path, settings)
    if item.status is batch_module.ItemStatus.ERROR:
        print(f"error: {item.message}", file=sys.stderr)
        return 1
    if item.status is batch_module.ItemStatus.SKIPPED:
        print(f"skipped: {item.message}")
        return 0

    print(
        f"{item.output}  "
        f"{item.size[0] if item.size else '?'}×{item.size[1] if item.size else '?'}  "
        f"{export.human_size(item.input_bytes)} → {export.human_size(item.output_bytes)}"
    )
    if item.message:
        print(f"  note: {item.message}")
    return 0


def command_batch(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    files = batch_module.collect_inputs(args.paths, recursive=args.recursive)
    if not files:
        print("No supported images found.", file=sys.stderr)
        return 1

    settings.output.directory = args.output or ""
    if args.pattern:
        settings.output.filename_pattern = args.pattern
    settings.output.conflict = ConflictPolicy(args.on_conflict)

    if not settings.output.directory and not args.in_place:
        print(
            "Refusing to write next to your originals without --in-place.\n"
            "Pass -o/--output DIR, or add --in-place to accept it.",
            file=sys.stderr,
        )
        return 2

    total = len(files)
    quiet = args.quiet

    def on_progress(done: int, _total: int, path: str) -> None:
        if not quiet:
            print(f"[{done}/{total}] {os.path.basename(path)}", flush=True)

    runner = batch_module.BatchRunner(
        files, settings, workers=args.workers,
        on_progress=on_progress, dry_run=args.dry_run,
    )
    report = runner.run()

    for item in report.failed:
        print(f"failed: {os.path.basename(item.source)}: {item.message}", file=sys.stderr)
    print(report.summary())
    if args.json:
        print(json.dumps(
            {
                "processed": len(report.succeeded),
                "skipped": len(report.skipped),
                "failed": len(report.failed),
                "input_bytes": report.input_bytes,
                "output_bytes": report.output_bytes,
                "elapsed_seconds": round(report.elapsed, 3),
                "items": [
                    {
                        "source": item.source, "output": item.output,
                        "status": item.status.value, "message": item.message,
                        "input_bytes": item.input_bytes, "output_bytes": item.output_bytes,
                    }
                    for item in report.items
                ],
            },
            indent=2,
        ))
    return 1 if report.failed else 0


def command_compress(args: argparse.Namespace) -> int:
    """Batch with the watermark switched off — the common "just shrink it" case."""
    args.no_text = True
    args.preset = getattr(args, "preset", None)
    # No watermark is added, so a "_wm" suffix would be a lie; keep the name.
    if not args.pattern:
        args.pattern = "{name}{ext}"
    return command_batch(args)


def command_info(args: argparse.Namespace) -> int:
    records = []
    status = 0
    for path in batch_module.collect_inputs(args.paths, recursive=args.recursive):
        try:
            source = imageio.load_image(path)
        except WatermarkError as exc:
            print(f"error: {exc}", file=sys.stderr)
            status = 1
            continue
        info = source.describe()
        records.append(info)
        source.image.close()
        if not args.json:
            print(f"{info['path']}")
            print(f"  {info['width']} × {info['height']} px ({info['megapixels']} MP), "
                  f"{info['mode']}, {info['format']}")
            print(f"  {export.human_size(info['file_size'])}"
                  f"{'  · alpha' if info['has_alpha'] else ''}"
                  f"{'  · EXIF' if info['has_exif'] else ''}"
                  f"{'  · ICC' if info['has_icc'] else ''}")
    if args.json:
        print(json.dumps(records, indent=2))
    return status


def command_presets(args: argparse.Namespace) -> int:
    if args.show:
        print(json.dumps(presets.load_preset(args.show).to_json_dict(), indent=2))
        return 0
    if args.delete:
        presets.delete_preset(args.delete)
        print(f"Deleted preset '{args.delete}'.")
        return 0
    for info in presets.list_presets():
        marker = "built-in" if info.builtin else "yours   "
        print(f"{marker}  {info.name}")
        print(f"            {info.description}")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watermark",
        description=f"{APP_NAME} — watermark, resize and compress images.",
        epilog="Run without a command to open the desktop app.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug detail")
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply", help="watermark a single image")
    apply_parser.add_argument("image")
    apply_parser.add_argument("-o", "--output", help="output file (default: alongside the source)")
    add_watermark_options(apply_parser)
    add_transform_options(apply_parser)
    add_export_options(apply_parser)
    apply_parser.set_defaults(func=command_apply)

    batch_parser = subparsers.add_parser("batch", help="watermark many images or a folder")
    batch_parser.add_argument("paths", nargs="+", help="files and/or folders")
    batch_parser.add_argument("-o", "--output", help="output folder")
    batch_parser.add_argument("-r", "--recursive", action="store_true",
                              help="descend into sub-folders")
    batch_parser.add_argument("--pattern", help="output name pattern (default {name}_wm{ext})")
    batch_parser.add_argument("--on-conflict", choices=[p.value for p in ConflictPolicy],
                              default=ConflictPolicy.RENAME.value)
    batch_parser.add_argument("--workers", type=int, default=4)
    batch_parser.add_argument("--in-place", action="store_true",
                              help="write results beside the originals")
    batch_parser.add_argument("--dry-run", action="store_true",
                              help="do all the work but write nothing")
    batch_parser.add_argument("--json", action="store_true", help="print a machine-readable report")
    batch_parser.add_argument("-q", "--quiet", action="store_true")
    add_watermark_options(batch_parser)
    add_transform_options(batch_parser)
    add_export_options(batch_parser)
    batch_parser.set_defaults(func=command_batch)

    compress_parser = subparsers.add_parser(
        "compress", help="shrink images without adding a watermark"
    )
    compress_parser.add_argument("paths", nargs="+")
    compress_parser.add_argument("-o", "--output", help="output folder")
    compress_parser.add_argument("-r", "--recursive", action="store_true")
    compress_parser.add_argument("--pattern", help="output name pattern")
    compress_parser.add_argument("--on-conflict", choices=[p.value for p in ConflictPolicy],
                                 default=ConflictPolicy.RENAME.value)
    compress_parser.add_argument("--workers", type=int, default=4)
    compress_parser.add_argument("--in-place", action="store_true")
    compress_parser.add_argument("--dry-run", action="store_true")
    compress_parser.add_argument("--json", action="store_true")
    compress_parser.add_argument("-q", "--quiet", action="store_true")
    add_transform_options(compress_parser)
    add_export_options(compress_parser)
    compress_parser.set_defaults(func=command_compress)

    info_parser = subparsers.add_parser("info", help="describe images")
    info_parser.add_argument("paths", nargs="+")
    info_parser.add_argument("-r", "--recursive", action="store_true")
    info_parser.add_argument("--json", action="store_true")
    info_parser.set_defaults(func=command_info)

    presets_parser = subparsers.add_parser("presets", help="list and inspect presets")
    presets_parser.add_argument("--show", metavar="NAME", help="print a preset as JSON")
    presets_parser.add_argument("--delete", metavar="NAME", help="delete one of your presets")
    presets_parser.set_defaults(func=command_presets)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    logs.configure(verbose=args.verbose)
    try:
        return int(args.func(args))
    except WatermarkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
