from __future__ import annotations

import csv
import os
import sys
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any, Literal, overload

from .nina_utils import to_snake

# Tool/cache trees that must never be searched for frame or plan data:
# dependency dirs (npm/uv) and byte-code caches hold hundreds of unrelated
# files. Hidden dirs (.git, .opencode, .venv — and on SMB mounts .snapshot
# and .Trashes) are excluded by _walk_visible itself: their copied trees
# would otherwise duplicate CSV rows (double attribution) and inflate frame
# counts with snapshot/trash copies.
_PRUNED_DIR_NAMES = frozenset({"node_modules", "__pycache__"})


def _prunable_dir(name: str) -> bool:
    """True when a directory must be skipped by every tree walk."""
    return name.startswith(".") or name in _PRUNED_DIR_NAMES


def _walk_visible(root: Path) -> Iterator[tuple[Path, list[str]]]:
    """``os.walk`` over ``root`` that never enters hidden or tool directories.

    Yields ``(directory, sorted file names)`` for every *visible* directory,
    depth-first. Directories are pruned in place, so a share's snapshot or
    trash copies (``.snapshot``, ``.Trashes``) can never be mistaken for
    fresh frames — on SMB mounts those copies would otherwise duplicate CSV
    rows and double-count attribution. Directory symlinks are not followed
    (``os.walk`` default), so a symlink cycle cannot hang an unattended
    audit. Hidden *files* are yielded as-is; only directory descent is
    restricted.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _prunable_dir(d))
        yield Path(dirpath), sorted(filenames)


def visible_rglob(root: Path, name: str) -> list[Path]:
    """Files named ``name`` anywhere under ``root``, hidden/tool dirs skipped.

    ``root.rglob(name)`` equivalent with the ``_walk_visible`` prune rule —
    a ``.snapshot``/``.Trashes`` copy of ``ImageMetaData.csv`` must never be
    read alongside the live one, or every frame in it would be counted twice.
    """
    return [
        directory / name for directory, names in _walk_visible(root) if name in names
    ]


def windows_to_local(windows_path: str, drive_mount: str | None = None) -> Path:
    if sys.platform == "win32":
        return Path(windows_path)
    win = PureWindowsPath(windows_path)
    if not win.drive:
        return Path(windows_path)
    if drive_mount is None:
        drive_mount = os.environ.get("NINA_DRIVE_MOUNT")
    mount = drive_mount or f"/mnt/{win.drive[0].lower()}"
    return Path(mount).joinpath(*win.parts[1:])


def read_weather_rows(type_dir: Path) -> dict[str, dict[str, Any]]:
    """Read per-exposure weather samples from a directory's ``WeatherData.csv``.

    Returns a dict mapping ``exposure_number`` (as a string) to the snake_cased
    weather row. If ``WeatherData.csv`` is absent, empty, or unreadable, returns
    an empty dict so callers degrade cleanly rather than failing an archive.
    """
    path = type_dir / "WeatherData.csv"
    if not path.is_file():
        return {}
    try:
        rows = _read_csv(path)
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        exp_nr = str(r.get("exposure_number", "")).strip()
        if exp_nr:
            out[exp_nr] = r
    return out


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [{to_snake(k): v for k, v in row.items()} for row in csv.DictReader(f)]


def _resolve_image_path(
    file_path: str | None,
    bases: tuple[Path, ...] = (),
) -> str | None:
    if not file_path:
        return None
    p = Path(windows_to_local(file_path))
    if p.is_absolute():
        return str(p)
    for base in bases:
        candidate = Path(base) / p
        if candidate.is_file():
            return str(candidate)
    return str(p)


def _resolve_existing_file(
    file_path: str | None,
    bases: tuple[Path, ...] = (),
) -> str | None:
    if not file_path:
        return None
    resolved = _resolve_image_path(file_path, bases)
    if resolved and Path(resolved).is_file():
        return resolved
    return None


def _require_root(root: Path) -> None:
    """Raise when ``root`` is missing, rather than rglob silently matching nothing.

    A missing root (unmounted drive, wrong NINA_DRIVE_MOUNT, stale profile
    path) must not masquerade as "no frames yet": callers would report an
    empty plan and re-acquire everything. Hidden/tool directories are never
    searched (see ``visible_rglob``), so a snapshot/trash copy of a CSV
    cannot double-count a session.
    """
    if root.is_dir():
        return
    raise FileNotFoundError(
        f"imaging root does not exist or is not a directory: {root} "
        "(check the site profile's image save path and, on non-Windows "
        "hosts, NINA_DRIVE_MOUNT)"
    )


@overload
def read_imaging_csv(
    root: Path, image_type: str, *, report_dropped: Literal[False] = False
) -> list[dict[str, Any]]: ...


@overload
def read_imaging_csv(
    root: Path, image_type: str, *, report_dropped: Literal[True]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...


def read_imaging_csv(
    root: Path,
    image_type: str,
    *,
    report_dropped: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rows of ``image_type`` from every visible ImageMetaData.csv under root.

    Rows whose recorded ``file_path`` no longer exists on disk are DROPPED:
    the view must never credit a frame nobody can open. That includes N.I.N.A.
    rejects — it renames the file to ``BAD_<name>`` while the CSV keeps the
    pre-rename name, so a rejected frame disappears from this view entirely
    (the rejection itself is only visible on disk; ``audit_plans`` reports it
    as ``bad``).

    ``report_dropped=True`` returns ``(rows, dropped)`` instead: each dropped
    row keeps its raw fields plus a best-effort ``file_path`` (Windows→local
    mapping applied, existence NOT required), so the caller can classify the
    loss — probe for a ``BAD_`` twin, or report the record as gone. Default
    is the plain list every existing caller expects; only opted-in callers
    see the tuple.
    """
    _require_root(root)
    rows: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    image_type_upper = image_type.upper()
    for meta_path in visible_rglob(root, "ImageMetaData.csv"):
        csv_dir = meta_path.parent
        parent_dir = csv_dir.parent
        bases = (csv_dir, parent_dir, root)
        for row in _read_csv(meta_path):
            row_image_type = (row.get("image_type") or "").upper()
            if row_image_type != image_type_upper:
                continue
            raw_path = row.get("file_path")
            resolved = _resolve_existing_file(raw_path, bases=bases)
            if raw_path and resolved is None:
                if report_dropped:
                    record = dict(row)
                    record["file_path"] = _resolve_image_path(raw_path, bases)
                    dropped.append(record)
                continue
            if resolved:
                row["file_path"] = resolved
            enriched = dict(row)
            enriched.update({"frame_type": row_image_type, "source": "image_metadata"})
            rows.append(enriched)
    if report_dropped:
        return rows, dropped
    return rows
