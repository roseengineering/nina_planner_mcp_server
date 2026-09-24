from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_snake(name: str) -> str:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.replace(" ", "_").lower()


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


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [
            {_to_snake(k): v for k, v in row.items()} for row in csv.DictReader(f)
        ]


def _find_date_ancestor(path: Path) -> str | None:
    for parent in path.parents:
        if _DATE_RE.match(parent.name):
            return parent.name
    return None


def _resolve_image_path(file_path: str | None) -> str | None:
    if not file_path:
        return None
    return str(windows_to_local(file_path))


def _resolve_existing_file(file_path: str | None) -> str | None:
    if not file_path:
        return None
    resolved = _resolve_image_path(file_path)
    if Path(resolved).is_file():
        return resolved
    return None


def read_imaging_csv(root: Path, image_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    image_type_upper = image_type.upper()
    for meta_path in root.rglob("ImageMetaData.csv"):
        csv_dir = meta_path.parent
        date = _find_date_ancestor(meta_path)
        for row in _read_csv(meta_path):
            row_image_type = (row.get("image_type") or "").upper()
            if row_image_type != image_type_upper:
                continue
            raw_path = row.get("file_path")
            resolved = _resolve_existing_file(raw_path)
            if raw_path and resolved is None:
                continue
            if resolved:
                row["file_path"] = resolved
            enriched = dict(row)
            enriched.update(
                {"date": date, "frame_type": row_image_type, "source": "image_metadata"}
            )
            rows.append(enriched)
    return rows
