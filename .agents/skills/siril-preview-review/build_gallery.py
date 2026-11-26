"""Build a browsable HTML gallery of raw or denoised previews under an archive root.

The full-resolution previews Siril writes are ~11 MB each (1080x1920 16-bit
PNG), so the gallery creates small JPEG thumbnails and links each one back to
the original for full-size viewing.

Walks top-down and prunes each target directory on discovery -- the same rule
siril-mcp's list_working_directories uses -- so lights/, process/ and the rest
are never descended looking for files that cannot be there.

Requires Pillow (present as `python3` on this machine).
"""

import argparse
import csv
import html
import json
import os
import re
import statistics
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - environment problem, not logic
    sys.exit("error: Pillow is required (run with the python3 that has PIL)")


def iter_previews(archive: Path):
    """Yield every preview.png under ``archive``, pruning targets as found."""
    for current, dirs, files in os.walk(archive, topdown=True):
        here = Path(current)
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if "preview.png" not in files:
            continue
        yield here / "preview.png"
        dirs[:] = []  # a target holds no nested previews


def _stack_outputs(target: Path) -> list[Path]:
    """Return root deliverable FITS files, excluding denoised siblings."""
    outputs = []
    for path in sorted(target.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".fit", ".fits", ".fts"}:
            continue
        stem = path.stem.lower()
        if stem == "result_denoised" or stem.startswith("result_denoised_"):
            continue
        if stem.endswith("_denoised"):
            continue
        outputs.append(path)
    return outputs


def iter_stack_targets(archive: Path):
    """Yield targets with root stacks, pruning intermediates and child folders."""
    internal_dirs = {"biases", "darks", "flats", "lights", "masters", "process"}
    for current, dirs, _files in os.walk(archive, topdown=True):
        here = Path(current)
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and d.lower() not in internal_dirs
        )
        stacks = _stack_outputs(here)
        if not stacks:
            continue
        yield here, stacks
        dirs[:] = []  # a target holds no nested output stacks


def _denoised_stem(source: Path) -> str:
    """Mirror siril-mcp's sibling naming for a selected source FITS."""
    stem = source.stem
    if stem == "result":
        return "result_denoised"
    if stem.startswith("result_"):
        return "result_denoised_" + stem[len("result_") :]
    return f"{stem}_denoised"


def _denoised_pairs(target: Path, stacks: list[Path]):
    """Yield fresh rendered denoised sidecars and report invalid pairs."""
    by_preview_name: dict[str, list[Path]] = {}
    for stack in stacks:
        by_preview_name.setdefault(f"{_denoised_stem(stack)}.png", []).append(stack)

    for preview_name, sources in sorted(by_preview_name.items()):
        if len(sources) > 1:
            source_names = ", ".join(source.name for source in sources)
            print(
                f"skip {target.name}: ambiguous denoised preview {preview_name} "
                f"matches {source_names}",
                file=sys.stderr,
            )
            continue
        source = sources[0]
        denoised_stem = Path(preview_name).stem
        denoised_stacks = sorted(
            path
            for path in target.glob(f"{denoised_stem}.*")
            if path.is_file() and path.suffix.lower() in {".fit", ".fits", ".fts"}
        )
        if not denoised_stacks:
            print(
                f"skip {target.name}/{source.name}: missing denoised FITS "
                f"{target / (denoised_stem + '.fit')}",
                file=sys.stderr,
            )
            continue
        if len(denoised_stacks) > 1:
            print(
                f"skip {target.name}/{source.name}: ambiguous denoised FITS "
                f"{', '.join(path.name for path in denoised_stacks)}",
                file=sys.stderr,
            )
            continue
        denoised = denoised_stacks[0]
        try:
            if denoised.stat().st_mtime_ns < source.stat().st_mtime_ns:
                print(
                    f"skip {target.name}/{source.name}: stale denoised FITS "
                    f"{denoised.name} is older than its source",
                    file=sys.stderr,
                )
                continue
        except OSError as exc:
            print(
                f"skip {target.name}/{source.name}: cannot check denoised FITS "
                f"freshness: {exc}",
                file=sys.stderr,
            )
            continue

        preview = target / f"{denoised.stem}.png"
        if not preview.is_file():
            print(
                f"skip {target.name}/{sources[0].name}: missing denoised preview "
                f"{preview}",
                file=sys.stderr,
            )
            continue
        try:
            if preview.stat().st_mtime_ns < denoised.stat().st_mtime_ns:
                print(
                    f"skip {target.name}/{source.name}: stale denoised preview "
                    f"{preview.name} is older than its FITS",
                    file=sys.stderr,
                )
                continue
        except OSError as exc:
            print(
                f"skip {target.name}/{source.name}: cannot check denoised preview "
                f"freshness: {exc}",
                file=sys.stderr,
            )
            continue
        yield source, preview


def _thumb_filename(target: Path, archive: Path, source: Path | None) -> str:
    """Create a stable distinct thumbnail name for every denoised stack."""
    if source is None:
        # Preserve the established raw-gallery filenames.
        return f"{target.name}.jpg"
    relative = target.relative_to(archive)
    identity = "__".join((*relative.parts, source.stem))
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", identity)
    return f"{safe}.jpg"


def star_median(target: Path) -> float | None:
    """Median detected_stars from NINA's per-frame telemetry, if present."""
    csv_path = target / "lights" / "ImageMetaData.csv"
    if not csv_path.is_file():
        return None
    try:
        with csv_path.open(newline="") as handle:
            values = [
                float(row["detected_stars"])
                for row in csv.DictReader(handle)
                if row.get("detected_stars") not in (None, "", "NaN", "nan")
            ]
    except (OSError, KeyError, ValueError):
        return None
    return statistics.median(values) if values else None


def state_of(target: Path) -> str:
    """Classify a target as succeeded, failed_partial or unprocessed."""
    outputs = [target / "process" / "result.fit", *target.glob("result_*.fit")]
    if any(p.is_file() and p.stat().st_size > 0 for p in outputs):
        return "succeeded"
    if (target / "process").is_dir() or (target / "masters").is_dir():
        return "failed_partial"
    return "unprocessed"


def main() -> int:
    """Parse arguments, write the gallery, and report what was produced."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive-root",
        default=os.environ.get("ARCHIVE_ROOT", str(Path.home() / "landing")),
        help="root holding observatory/target folders (default: ARCHIVE_ROOT)",
    )
    parser.add_argument(
        "--out",
        default=str(
            Path("/private/var/folders/rx/b7znqd3d0ts9gn2lb151n4300000gn/T/opencode")
            / "gallery"
        ),
        help="directory to write index.html and thumbs/ into",
    )
    parser.add_argument(
        "--sort",
        choices=("stars", "name", "state"),
        default="stars",
        help=(
            "stars = weakest first (default), name = alphabetical, "
            "state = failures first"
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="only the first N (0 = all)"
    )
    parser.add_argument(
        "--thumb-size", type=int, default=420, help="thumbnail long edge"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the entry list as JSON"
    )
    parser.add_argument(
        "--denoised",
        action="store_true",
        help=(
            "use rendered denoised-preview sidecars only; "
            "never fall back to preview.png"
        ),
    )
    args = parser.parse_args()

    archive = Path(args.archive_root).expanduser()
    if not archive.is_dir():
        sys.exit(f"error: archive root not found: {archive}")
    out = Path(args.out)
    thumbs = out / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)

    entries = []

    def add_entry(target: Path, preview: Path, source: Path | None = None) -> None:
        thumb_name = _thumb_filename(target, archive, source)
        thumb = thumbs / thumb_name
        try:
            with Image.open(preview) as im:
                im = im.convert("RGB")
                im.thumbnail((args.thumb_size, args.thumb_size))
                im.save(thumb, "JPEG", quality=82, optimize=True)
        except Exception as exc:  # one unreadable file must not break the gallery
            print(f"skip {target.name}: {exc}", file=sys.stderr)
            return
        entry = {
            "name": target.name,
            "thumb": f"thumbs/{thumb_name}",
            "full": preview.as_uri(),
            "stars": star_median(target),
            "state": state_of(target),
            "size_mb": round(preview.stat().st_size / 1_048_576, 1),
        }
        if source is not None:
            entry["stack"] = source.name
        entries.append(entry)

    if args.denoised:
        for target, stacks in iter_stack_targets(archive):
            for _source, preview in _denoised_pairs(target, stacks):
                add_entry(target, preview, _source)
    else:
        for preview in iter_previews(archive):
            add_entry(preview.parent, preview)

    if args.sort == "stars":
        # Weakest first: the ones most worth a human eye sort to the top.
        entries.sort(key=lambda e: (e["stars"] is None, e["stars"] or 0))
    elif args.sort == "name":
        entries.sort(key=lambda e: e["name"])
    else:
        order = {"failed_partial": 0, "unprocessed": 1, "succeeded": 2}
        entries.sort(key=lambda e: (order.get(e["state"], 3), e["name"]))
    if args.limit:
        entries = entries[: args.limit]

    if args.json:
        print(json.dumps(entries, indent=2))
        return 0

    cards = []
    for e in entries:
        stars = "—" if e["stars"] is None else f"{e['stars']:.0f}★"
        label = (
            f"{e['name']} · {e['stack']}" if args.denoised else e["name"].split("_")[0]
        )
        cards.append(
            f'<figure class="card"><a href="{e["full"]}">'
            f'<img src="{e["thumb"]}" loading="lazy" '
            f'alt="{html.escape(e["name"])}"></a>'
            f"<figcaption><b>{html.escape(label)}</b>"
            f'<span class="muted">{stars} · {e["state"]} · {e["size_mb"]} MB</span>'
            f'<span class="muted full">{html.escape(e["name"])}</span>'
            f"</figcaption></figure>"
        )

    preview_kind = "denoised previews" if args.denoised else "previews"
    item_label = "images" if args.denoised else "targets"
    page = f"""<!doctype html>
<meta charset="utf-8">
<title>Siril {preview_kind} ({len(entries)})</title>
<style>
 body {{ font: 14px/1.45 system-ui, sans-serif; margin:0; background:#111;
 color:#ddd; }}
 header {{ position:sticky; top:0; background:#111; padding:14px 20px;
 border-bottom:1px solid #333; }}
 h1 {{ margin:0 0 4px; font-size:17px; }}
 .muted {{ color:#888; font-size:12px; display:block; }}
 .full {{ font-size:10px; color:#555; word-break:break-all; }}
 .grid {{ display:grid; gap:14px; padding:18px;
 grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); }}
 .card {{ margin:0; background:#1a1a1a; border:1px solid #2c2c2c;
 border-radius:8px; overflow:hidden; }}
 .card img {{ width:100%; display:block; background:#000;
 aspect-ratio:1080/1920; object-fit:cover; }}
 .card figcaption {{ padding:8px 10px 10px; }}
 .card b {{ color:#fff; font-size:14px; }}
 a {{ color:#7ab8ff; text-decoration:none; }}
</style>
<header>
  <h1>Siril {preview_kind} — {len(entries)} {item_label}</h1>
  <span class="muted">Sorted by {args.sort}. Click any image for full resolution.</span>
</header>
<div class="grid">
{chr(10).join(cards)}
</div>
"""
    (out / "index.html").write_text(page, encoding="utf-8")
    total = sum(p.stat().st_size for p in thumbs.glob("*.jpg"))
    print(f"targets: {len(entries)}")
    print(
        f"thumbs : {len(list(thumbs.glob('*.jpg')))} files, {total / 1_048_576:.1f} MB"
    )
    print(f"gallery: {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
