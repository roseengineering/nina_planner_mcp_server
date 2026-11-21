"""Build a browsable HTML gallery of every preview.png under an archive root.

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
    args = parser.parse_args()

    archive = Path(args.archive_root).expanduser()
    if not archive.is_dir():
        sys.exit(f"error: archive root not found: {archive}")
    out = Path(args.out)
    thumbs = out / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)

    entries = []
    for preview in iter_previews(archive):
        target = preview.parent
        thumb = thumbs / f"{target.name}.jpg"
        try:
            with Image.open(preview) as im:
                im = im.convert("RGB")
                im.thumbnail((args.thumb_size, args.thumb_size))
                im.save(thumb, "JPEG", quality=82, optimize=True)
        except Exception as exc:  # one unreadable file must not break the gallery
            print(f"skip {target.name}: {exc}", file=sys.stderr)
            continue
        entries.append(
            {
                "name": target.name,
                "thumb": f"thumbs/{target.name}.jpg",
                "full": preview.as_uri(),
                "stars": star_median(target),
                "state": state_of(target),
                "size_mb": round(preview.stat().st_size / 1_048_576, 1),
            }
        )

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
        cards.append(
            f'<figure class="card"><a href="{e["full"]}">'
            f'<img src="{e["thumb"]}" loading="lazy" '
            f'alt="{html.escape(e["name"])}"></a>'
            f"<figcaption><b>{html.escape(e['name'].split('_')[0])}</b>"
            f'<span class="muted">{stars} · {e["state"]} · {e["size_mb"]} MB</span>'
            f'<span class="muted full">{html.escape(e["name"])}</span>'
            f"</figcaption></figure>"
        )

    page = f"""<!doctype html>
<meta charset="utf-8">
<title>Siril previews ({len(entries)})</title>
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
  <h1>Siril previews — {len(entries)} targets</h1>
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
