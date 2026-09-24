from __future__ import annotations

import csv
import math
import os
import re
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

from .nina_utils import to_snake

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
            {to_snake(k): v for k, v in row.items()} for row in csv.DictReader(f)
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


# Per-frame identity columns, listed once per frame rather than on every
# melted metric row (keeps output compact and makes frame references stable).
_ID_VARS = (
    "file_path",
    "date",
    "exposure_number",
    "exposure_start",
    "exposure_start_utc",
    "duration",
    "filter_name",
)

# Columns where NINA writes 0 when the underlying ASCOM value was NaN
# (i.e. the measurement was not taken), so 0 carries no information there.
_QUALITY_METRICS = {
    "camera_temp",
    "camera_target_temp",
    "detected_stars",
    "hfr",
    "hfr_st_dev",
    "fwhm",
    "eccentricity",
    "guiding_rms",
    "guiding_rms_arc_sec",
    "guiding_rmsra",
    "guiding_rmsra_arc_sec",
    "guiding_rmsdec",
    "guiding_rmsdec_arc_sec",
}

# Namespace prefix applied to each metric name in the narrow output.
_METRIC_GROUP = {
    "camera_temp": "ccd",
    "camera_target_temp": "ccd",
    "detected_stars": "quality",
    "hfr": "quality",
    "hfr_st_dev": "quality",
    "fwhm": "quality",
    "eccentricity": "quality",
    "guiding_rms": "guiding",
    "guiding_rms_arc_sec": "guiding",
    "guiding_rmsra": "guiding",
    "guiding_rmsra_arc_sec": "guiding",
    "guiding_rmsdec": "guiding",
    "guiding_rmsdec_arc_sec": "guiding",
    "adu_st_dev": "background",
    "adu_mean": "background",
    "adu_median": "background",
    "adu_min": "background",
    "adu_max": "background",
    "focuser_position": "focus",
    "focuser_temp": "focus",
    "rotator_position": "focus",
    "airmass": "pointing",
    "mount_ra": "pointing",
    "mount_dec": "pointing",
    "pier_side": "pointing",
    "binning": "config",
    "gain": "config",
    "offset": "config",
}


def _ascom_none(value: Any) -> Any:
    """Normalize ASCOM sentinel values (NaN, -1, empty, n/a) to None."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in ("nan", "n/a"):
            return None
        return value
    if isinstance(value, float):
        if math.isnan(value) or value == -1.0:
            return None
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return None if value == -1 else value
    return value


def _clean_metric(column: str, value: Any) -> Any:
    value = _ascom_none(value)
    if value is None:
        return None
    if column in _QUALITY_METRICS and value in (0, 0.0, "0"):
        return None
    if isinstance(value, str):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _metric_name(column: str) -> str:
    group = _METRIC_GROUP.get(column, "meta")
    return f"{group}.{column}"


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row.get(key)] = out.get(row.get(key), 0) + 1
    return out


def melt_imaging_metadata(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Reshape wide metadata rows into a compact narrow (melted) table.

    Dead columns are dropped dynamically per call: columns whose cleaned
    values are all identical (constants) move to summary.constants, and
    columns with no populated values at all (ASCOM NaN/-1, empty, n/a, and
    the 0 sentinel NINA writes for unmeasured quality/guiding/CCD metrics)
    move to summary.unpopulated. Only genuinely varying metrics become rows
    in the narrow `metrics` table, namespaced by group (quality.hfr, ...).
    """
    if not rows:
        return {"summary": {"count": 0}, "frames": [], "metrics": []}

    columns = [k for k in rows[0] if k not in ("image_type", "source")]
    cleaned = {c: [_clean_metric(c, row.get(c)) for row in rows] for c in columns}

    present_metrics: list[str] = []
    constants: dict[str, Any] = {}
    unpopulated: list[str] = []
    for c in columns:
        if c in _ID_VARS:
            continue
        present = [v for v in cleaned[c] if v is not None]
        if not present:
            unpopulated.append(c)
            continue
        distinct = list(dict.fromkeys(present))
        if len(distinct) == 1 and len(present) == len(rows):
            constants[c] = distinct[0]
        else:
            present_metrics.append(c)

    frames = []
    for i, row in enumerate(rows):
        frame = {"index": i}
        for k in _ID_VARS:
            if k in row:
                frame[k] = row[k]
        frames.append(frame)

    metrics = []
    for i in range(len(rows)):
        for c in present_metrics:
            value = cleaned[c][i]
            if value is not None:
                metrics.append({"frame": i, "metric": _metric_name(c), "value": value})

    summary = {
        "count": len(rows),
        "by_filter": _counts(rows, "filter_name"),
        "by_date": _counts(rows, "date"),
    }
    if constants:
        summary["constants"] = constants
    if unpopulated:
        summary["unpopulated"] = sorted(unpopulated)
    return {"summary": summary, "frames": frames, "metrics": metrics}
