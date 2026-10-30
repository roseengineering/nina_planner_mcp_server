"""nina-archive — copy completed observation plans into a portable folder.

Archives a completed plan's frames plus its plan JSON into one
self-contained directory, ready to be moved into cloud storage and pulled
down to a workstation for calibration and stacking::

    <dest>/<plan-file-stem>__<plan-id>/
        plan.json              verbatim copy of the plan file
        manifest.json          identity, per-type counts, file list, warnings
        LIGHT/ImageMetaData.csv   the archived light frames' metadata rows
        LIGHT/*.fits.fz        attributed light frames
        FLAT/  DARK/  BIAS/    matched calibration frames (+ sidecar CSVs)

Frame selection reuses the exact attribution matchers behind
``get_plan_progress`` / ``audit_plans`` (``progress.filter_metadata_rows``),
so "archived" means "credited to this plan": lights by the underscore- or
bracket-delimited plan-id token in their recorded path (every pointing of a
mosaic), calibration pooled by type+filter / type+exposure / type alone
inside ``calibration_window_days`` of the earliest attributed light —
unioned across pointings so a mosaic shot over several nights carries each
night's calibration.

Decisions encoded here (see PROGRESS.md): originals are always COPIED, never
moved; a plan is archivable when every frame group's ``remaining_count`` is 0
(the same test ``get_plan_progress`` reports as ``complete``); frames outside
the credited set are never archived.  ``--all`` overrides the completeness
gate, ``--force`` rebuilds an existing folder, ``--dry-run`` writes nothing.

Copy semantics: each folder is assembled in a hidden ``.<name>.partial``
sibling and atomically renamed into place, so an interrupted run can never
leave a folder that looks complete.  Every file's size is verified after
copying (``--checksum`` adds sha256).  ``<dest>/archive-index.jsonl`` is an
append-only log of archived plans.

The destination must live OUTSIDE the imaging root: archived folders carry
their own ``ImageMetaData.csv`` copies, and a later attribution pass reading
them from inside the root would double-count every archived frame.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config, metadata, plan_utils
from .imaging import read_imaging_csv
from .models.plan import ObservationPlan
from .progress import filter_metadata_rows, plan_progress
from .tools.plans import _plan_files

_ARCHIVE_TYPES = ("LIGHT", "FLAT", "DARK", "BIAS")
_INDEX_NAME = "archive-index.jsonl"
_MANIFEST_NAME = "manifest.json"
_PLAN_KEYS = ("pointings", "light_frames", "plan_id")


class ArchiveError(Exception):
    """A plan could not be archived; the message is user-facing."""


# ---------------------------------------------------------------- helpers


def _human_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"  # pragma: no cover - unreachable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _looks_like_plan_file(path: Path) -> bool:
    """True when a JSON file carries plan-shaped keys.

    Used to separate a *broken plan* (report it) from unrelated project JSON
    such as opencode configs (ignore silently) — the distinction
    ``audit_plans`` draws between load ``errors`` and ignored config files.
    An unparseable file answers False: with no evidence it is a plan, it is
    nobody's problem yet, mirroring ``_probe_plan_id``.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and any(key in data for key in _PLAN_KEYS)


def _folder_name(plan_file: Path, plan: ObservationPlan) -> str:
    """Unique, greppable folder name: ``<plan stem>__<plan id>``."""
    stem = plan_utils.sanitize_filename(plan_file.stem)
    plan_id = plan._base_plan_id()
    if plan_id and plan_id in stem:
        return stem
    if plan_id:
        return f"{stem}__{plan_utils.sanitize_filename(plan_id)}"
    return stem


def _write_image_metadata_csv(
    path: Path, rows: list[dict[str, Any]], rel_paths: dict[str, str]
) -> None:
    """Write one ``ImageMetaData.csv`` for the archived frames of a type.

    ``rows`` are the read-side metadata dicts (snake_cased keys);
    ``rel_paths`` maps each row's original absolute ``file_path`` to the
    archive-relative path (the added ``archive_path`` column), so downstream
    tools can join manifest and CSV without re-deriving names.
    """
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if "archive_path" not in fieldnames:
        fieldnames.append("archive_path")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, restval="")
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["archive_path"] = rel_paths.get(str(row.get("file_path")), "")
            writer.writerow(out)


# ------------------------------------------------------------ collection


def _progress_facts(
    plan: ObservationPlan, rows: list[dict[str, Any]], now: datetime
) -> tuple[bool, dict[str, dict[str, int]]]:
    """Completeness + expected/credited counts per frame type.

    Mirrors ``get_plan_progress``: a plan is complete when no group of any
    pointing has a nonzero ``remaining_count``.  Light counts sum across
    pointings (each pane asks its own set); calibration is plan-level and
    read from pointing 1 exactly as ``audit_plans`` does — the manifest's
    separate ``archived`` count (added by the caller from the selection)
    carries the union actually copied.
    """
    complete = True
    light_expected = 0
    light_acquired = 0
    plan_level: dict[str, list[dict[str, Any]]] | None = None
    for index in range(1, len(plan.pointings) + 1):
        progress = plan_progress(plan, rows, pointing_index=index, now=now)
        if index == 1:
            plan_level = progress
        for items in progress.values():
            for item in items:
                if item["remaining_count"]:
                    complete = False
        light_expected += sum(g["total_count"] for g in progress["light"])
        light_acquired += sum(g["acquired_count"] for g in progress["light"])
    assert plan_level is not None  # plans always have >= 1 pointing
    counts: dict[str, dict[str, int]] = {
        "light": {"expected": light_expected, "acquired": light_acquired}
    }
    for image_type in ("flat", "dark", "bias"):
        groups = plan_level[image_type]
        counts[image_type] = {
            "expected": sum(g["total_count"] for g in groups),
            "acquired": sum(g["acquired_count"] for g in groups),
        }
    return complete, counts


def _select_frames(
    plan: ObservationPlan, rows: list[dict[str, Any]], now: datetime
) -> dict[str, list[dict[str, Any]]]:
    """Rows to archive per frame type: exactly what the plan is credited.

    Lights: every row attributed to any pointing (any filter/exposure the
    plan shot, surplus included).  Calibration: the pooled matchers,
    unioned across pointings' session windows and deduped by path.  Rows
    with no recorded file path are dropped — they can never be copied.
    """
    selected: dict[str, dict[str, dict[str, Any]]] = {
        t.lower(): {} for t in _ARCHIVE_TYPES
    }

    def add(image_type: str, row: dict[str, Any]) -> None:
        file_path = str(row.get("file_path") or "").strip()
        if file_path:
            selected[image_type][file_path] = row

    for index in range(1, len(plan.pointings) + 1):
        for row in filter_metadata_rows(plan, rows, "LIGHT", pointing_index=index):
            add("light", row)
        for image_type in ("FLAT", "DARK", "BIAS"):
            for row in filter_metadata_rows(
                plan,
                rows,
                image_type,
                pointing_index=index,
                max_age_days=plan.calibration_window_days,
                now=now,
            ):
                add(image_type.lower(), row)
    return {
        image_type: sorted(bucket.values(), key=lambda r: str(r.get("file_path")))
        for image_type, bucket in selected.items()
    }


# -------------------------------------------------------------- copying


def _copy_file(src: Path, dst: Path, *, checksum: bool) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    src_size = src.stat().st_size
    if dst.stat().st_size != src_size:
        raise ArchiveError(f"size mismatch after copy: {src} -> {dst}")
    if checksum and _sha256(src) != _sha256(dst):
        raise ArchiveError(f"checksum mismatch after copy: {src} -> {dst}")
    return src_size


def _archive_one(
    plan_file: Path,
    plan: ObservationPlan,
    selected: dict[str, list[dict[str, Any]]],
    *,
    dest: Path,
    complete: bool,
    counts: dict[str, dict[str, int]],
    imaging_root: Path,
    write_metadata: bool,
    checksum: bool,
) -> dict[str, Any]:
    """Copy one plan's frames + JSON into ``dest`` (atomic per folder).

    Builds in ``dest/.<folder>.partial`` then renames, so an interrupted run
    never leaves a folder that looks complete.  Raises ``ArchiveError`` on
    any copy/verify failure, with the staging dir cleaned up first.
    """
    folder = _folder_name(plan_file, plan)
    final = dest / folder
    staging = dest / f".{folder}.partial"
    dest.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    manifest_files: list[dict[str, Any]] = []
    total_bytes = 0
    copied = 0
    try:
        for image_type in _ARCHIVE_TYPES:
            rows_for_type = selected.get(image_type.lower(), [])
            type_dir = staging / image_type
            rel_map: dict[str, str] = {}
            used_names: set[str] = set()
            for row in rows_for_type:
                src = Path(str(row.get("file_path") or ""))
                if not src.is_file():
                    raise ArchiveError(
                        f"recorded frame missing on disk: {src} "
                        "(deleted, or the imaging root moved?)"
                    )
                name = src.name
                if name in used_names:
                    # Same filename from two date folders: keep both.
                    stem, dot, ext = name.partition(".fits")
                    name = (
                        f"{stem}_dup{len(used_names)}{dot}{ext}"
                        if dot
                        else f"{name}_{len(used_names)}"
                    )
                used_names.add(name)
                dst = type_dir / name
                size = _copy_file(src, dst, checksum=checksum)
                rel_path = f"{image_type}/{name}"
                rel_map[str(row["file_path"])] = rel_path
                manifest_files.append(
                    {
                        "type": image_type,
                        "path": rel_path,
                        "source": str(src),
                        "bytes": size,
                    }
                )
                total_bytes += size
                copied += 1
            if write_metadata and rows_for_type:
                _write_image_metadata_csv(
                    type_dir / "ImageMetaData.csv", rows_for_type, rel_map
                )

        # plan.json verbatim, then manifest.json.
        shutil.copy2(plan_file, staging / "plan.json")
        warnings: list[str] = []
        if not complete:
            warnings.append(
                "archived with --all while incomplete; remaining counts were "
                "nonzero for some frame group"
            )
        for image_type, numbers in counts.items():
            if numbers["archived"] < numbers["expected"]:
                window_note = ""
                if image_type != "light":
                    window_note = (
                        " (the calibration window may be closed — frames "
                        "outside it are never credited)"
                    )
                warnings.append(
                    f"{image_type.upper()}: {numbers['archived']} archived "
                    f"of {numbers['expected']} expected{window_note}"
                )
        manifest = {
            "tool": "nina-archive",
            "version": 1,
            "archived_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "plan_file": str(plan_file),
            "plan_id": plan._base_plan_id(),
            "target": plan.target,
            "intent": plan.intent,
            "pointing_count": len(plan.pointings),
            "imaging_root": str(imaging_root),
            "complete": complete,
            "counts": counts,
            "files": manifest_files,
            "file_count": copied,
            "total_bytes": total_bytes,
            "warnings": warnings,
        }
        (staging / _MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Commit: replace any previous folder only after staging is whole.
        if final.exists():
            shutil.rmtree(final)
        staging.rename(final)
    except ArchiveError:
        # Staging must never survive as a half-built archive; the caller
        # records the message and moves on to the next plan.
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - one bad plan must not crash the run
        shutil.rmtree(staging, ignore_errors=True)
        raise ArchiveError(f"{exc}") from exc

    record = {
        "archived_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "plan_id": plan._base_plan_id(),
        "plan_file": str(plan_file),
        "folder": folder,
        "target": plan.target,
        "complete": complete,
        "counts": counts,
        "file_count": copied,
        "total_bytes": total_bytes,
    }
    with (dest / _INDEX_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


# ------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nina-archive",
        description=(
            "Archive completed observation plans (their frames + JSON) into "
            "a self-contained directory for cloud transfer and local "
            "calibration/stacking. Frames are copied, never moved."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  nina-archive --dest ~/cloud/nina-archive\n"
            "  nina-archive --dry-run\n"
            "  nina-archive plans/m15_globular-cluster_20261003T112008.json\n"
            "  nina-archive --imaging-root /mnt/c/Users/me/Documents/N.I.N.A"
        ),
    )
    parser.add_argument(
        "plans",
        nargs="*",
        metavar="PLAN.json",
        help=(
            "plan files to archive (default: every plan discovered under --project-dir)"
        ),
    )
    parser.add_argument(
        "--dest",
        default="archive",
        help="archive destination directory (default: ./archive)",
    )
    parser.add_argument(
        "--imaging-root",
        default=None,
        help=(
            "N.I.N.A. image save path to read frames from; overrides the "
            "site-profile lookup, so archiving works while NINA is down"
        ),
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="project directory holding the plan files (default: cwd)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="also archive incomplete plans (default: complete only)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild folders that already exist in the destination",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be archived; write nothing",
    )
    parser.add_argument(
        "--checksum",
        action="store_true",
        help="verify sha256 of every copied frame (slower)",
    )
    parser.add_argument(
        "--no-image-metadata",
        action="store_true",
        help="skip writing per-type ImageMetaData.csv sidecars",
    )
    return parser


async def _resolve_root(args: argparse.Namespace) -> Path:
    if args.imaging_root:
        root = Path(args.imaging_root).expanduser()
        if not root.is_dir():
            raise ArchiveError(
                f"--imaging-root does not exist or is not a directory: {root}"
            )
        return root
    try:
        return Path(await metadata.resolve_imaging_root())
    except Exception as exc:  # noqa: BLE001 - surface any NINA/profile failure
        raise ArchiveError(
            "cannot resolve the imaging root from the N.I.N.A. site profile "
            f"({exc}). Is NINA running? Pass --imaging-root to archive "
            "without NINA."
        ) from exc


async def run(args: argparse.Namespace) -> int:
    project_dir = (
        Path(args.project_dir).expanduser() if args.project_dir else config.PROJECT_DIR
    )
    if not project_dir.is_absolute():
        project_dir = config.PROJECT_DIR / project_dir
    dest = Path(args.dest).expanduser()
    if not dest.is_absolute():
        dest = Path.cwd() / dest
    explicit = bool(args.plans)

    # Discover plan files: explicit args, else the project's plans.
    if explicit:
        plan_files = []
        for entry in args.plans:
            path = Path(entry).expanduser()
            if not path.is_absolute():
                path = project_dir / path
            if not path.is_file():
                print(f"✗ plan file not found: {path}", file=sys.stderr)
                return 1
            plan_files.append(path)
    else:
        plan_files = [
            p
            for p in _plan_files(project_dir)
            # Never re-archive what an earlier run wrote into --dest: a
            # plan.json copy under dest would read as a fresh plan file,
            # and its ImageMetaData.csv copies would double-count frames.
            if not _inside(p, dest)
        ]

    if not plan_files:
        print(f"no plan files found under {project_dir}", file=sys.stderr)
        return 1

    try:
        imaging_root = await _resolve_root(args)
    except ArchiveError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    if _inside(dest, imaging_root):
        print(
            f"✗ --dest ({dest}) is inside the imaging root "
            f"({imaging_root}); archived ImageMetaData.csv copies there "
            "would double-count every frame on the next attribution pass. "
            "Pick a destination outside the image save path.",
            file=sys.stderr,
        )
        return 1

    try:
        rows: list[dict[str, Any]] = []
        for image_type in ("light", "dark", "bias", "flat"):
            rows.extend(read_imaging_csv(root=imaging_root, image_type=image_type))
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    now = datetime.now(UTC).replace(microsecond=0)
    archived: list[dict[str, Any]] = []
    skipped: list[tuple[Path, str]] = []
    errors: list[tuple[Path, str]] = []
    ignored = 0

    for plan_file in plan_files:
        try:
            plan = await plan_utils.load_plan(str(plan_file))
        except Exception as exc:  # noqa: BLE001 - report, keep sweeping
            if explicit or _looks_like_plan_file(plan_file):
                # Naming a file IS the request to archive it, so any
                # failure counts; in discovery mode only plan-shaped JSON
                # earns an error — opencode configs are ignored silently.
                errors.append((plan_file, str(exc).splitlines()[0]))
            else:
                ignored += 1
            continue
        complete, counts = _progress_facts(plan, rows, now)
        if not complete and not args.all:
            deficit = ", ".join(
                f"{t.upper()} {n['acquired']}/{n['expected']}"
                for t, n in counts.items()
                if n["acquired"] < n["expected"]
            )
            skipped.append(
                (plan_file, f"incomplete ({deficit}) — pass --all to archive")
            )
            continue

        selected = _select_frames(plan, rows, now)
        for image_type, frames in selected.items():
            counts[image_type]["archived"] = len(frames)
        file_count = sum(len(v) for v in selected.values())
        folder = _folder_name(plan_file, plan)

        if args.dry_run:
            total = 0
            for frames in selected.values():
                for row in frames:
                    src = Path(str(row["file_path"]))
                    if src.is_file():
                        total += src.stat().st_size
            print(
                f"🔍 would archive {plan_file.name} → {folder}/ "
                f"({file_count} files, {_human_bytes(total)}, "
                f"{'complete' if complete else 'incomplete, --all'})"
            )
            archived.append(
                {
                    "plan_id": plan._base_plan_id(),
                    "folder": folder,
                    "file_count": file_count,
                    "total_bytes": total,
                }
            )
            continue

        if (dest / folder).exists() and not args.force:
            skipped.append((plan_file, f"already archived at {dest / folder}"))
            continue

        if file_count == 0:
            errors.append((plan_file, "no frames on disk match this plan"))
            continue

        try:
            record = _archive_one(
                plan_file,
                plan,
                selected,
                dest=dest,
                complete=complete,
                counts=counts,
                imaging_root=imaging_root,
                write_metadata=not args.no_image_metadata,
                checksum=args.checksum,
            )
        except ArchiveError as exc:
            errors.append((plan_file, str(exc)))
            continue
        archived.append(record)
        print(
            f"📦 {plan_file.name} → {record['folder']}/ "
            f"({record['file_count']} files, "
            f"{_human_bytes(record['total_bytes'])})"
        )

    # ---------------------------------------------------------- summary
    already = sum(1 for _, r in skipped if r.startswith("already archived"))
    if args.dry_run:
        print(
            f"\n🔍 dry run: {len(archived)} plan(s) would be archived to "
            f"{dest}, {len(skipped)} skipped, {len(errors)} error(s) — "
            "nothing written"
        )
    else:
        print(
            f"\n✅ {len(archived)} plan(s) archived to {dest}, "
            f"{len(skipped)} skipped, {len(errors)} error(s)"
            + (f", {ignored} unrelated JSON file(s) ignored" if ignored else "")
        )
    for plan_file, reason in skipped:
        print(f"  ⏭  {plan_file.name}: {reason}")
    for plan_file, reason in errors:
        print(f"  ✗ {plan_file.name}: {reason}", file=sys.stderr)

    if errors:
        return 1
    if not archived and already == 0:
        # Nothing archivable: every plan incomplete (or never shot).
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
