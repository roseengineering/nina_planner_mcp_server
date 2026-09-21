from __future__ import annotations

import csv
import os
import re
from pathlib import Path, PureWindowsPath
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def imaging_root() -> Path:
    raw = os.environ.get("NINA_IMAGING_DIR")
    if not raw:
        raise RuntimeError(
            "NINA_IMAGING_DIR is not set. Point it at the mounted N.I.N.A imaging "
            "directory (e.g. /mnt/Users/george/Documents/N.I.N.A)."
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"NINA_IMAGING_DIR '{raw}' is not a reachable directory.")
    return root


def windows_to_local(windows_path: str, drive_mount: str) -> Path:
    win = PureWindowsPath(windows_path)
    if not win.drive:
        return Path(windows_path)
    return Path(drive_mount).joinpath(*win.parts[1:])


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _date_folders(root: Path, date: str | None) -> list[Path]:
    folders = [p for p in root.iterdir() if p.is_dir() and _DATE_RE.match(p.name)]
    folders.sort(key=lambda p: p.name)
    if date is not None:
        folders = [p for p in folders if p.name == date]
        if not folders:
            raise ValueError(f"No imaging date folder '{date}' found under {root}.")
    return folders


def _merge_enrich(row: dict[str, Any], acquisition: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    for key, value in acquisition.items():
        if key not in enriched or not (enriched[key] or "").strip():
            enriched[key] = value
    return enriched


def _read_acquisition_details(session_dir: Path) -> dict[str, Any]:
    path = session_dir / "AcquisitionDetails.csv"
    if not path.is_file():
        return {}
    rows = _read_csv(path)
    if not rows:
        return {}
    return rows[0]


def read_imaging_csv(
    date: str | None, root: Path, image_type: str
) -> list[dict[str, Any]]:
    root = root or imaging_root()
    rows: list[dict[str, Any]] = []
    for folder in _date_folders(root, date):
        frame_dirs = [p for p in folder.iterdir() if p.is_dir()]
        frame_dirs = [p for p in frame_dirs if p.name == image_type.upper()]
        frame_dirs.sort(key=lambda p: p.name)
        if not frame_dirs:
            continue

        for frame_dir in frame_dirs:
            frame = frame_dir.name.upper()
            path = frame_dir / "ImageMetaData.csv"
            if not path.is_file():
                continue
            acquisition = _read_acquisition_details(frame_dir)
            for row in _read_csv(path):
                row_image_type = (row.get("ImageType") or "").upper()
                if row_image_type and row_image_type != frame:
                    continue
                rows.append(
                    {
                        "Date": folder.name,
                        "FrameType": frame,
                        "Source": "image_metadata",
                        **_merge_enrich(row, acquisition),
                    }
                )
    return rows
