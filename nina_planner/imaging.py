from __future__ import annotations

import csv
import math
import os
import re
import sys
from datetime import UTC, datetime
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


def read_weather_csv(root: Path) -> list[dict[str, Any]]:
    """Read per-exposure weather samples from every WeatherData.csv in ``root``.

    Mirrors ``read_imaging_csv``: keys are snake-cased and each row is stamped
    with the ``date`` of its enclosing date folder. Rows share the exposure
    timestamps used by ``ImageMetaData.csv`` so they can be joined to frames.
    """
    rows: list[dict[str, Any]] = []
    for meta_path in root.rglob("WeatherData.csv"):
        date = _find_date_ancestor(meta_path)
        for row in _read_csv(meta_path):
            enriched = dict(row)
            enriched.update(
                {"date": date, "frame_type": "WEATHER", "source": "weather"}
            )
            rows.append(enriched)
    return rows


# Per-frame identity columns, kept once on each wide output row (they keep
# their raw CSV names and anchor the metrics in that row).
_ID_VARS = (
    "file_path",
    "date",
    "exposure_number",
    "exposure_start_utc",
    "duration",
    "filter_name",
)

# Output name for the UTC timestamp identity column: the CSV's ExposureStartUTC
# is the only time-of-exposure column we keep (normalized to seconds-precision
# UTC with a Z suffix), and the naive local ExposureStart is dropped entirely.
_OUTPUT_ID_NAME = {
    "exposure_start_utc": "exposure_start",
}

# Source columns that never surface in the output (superseded by
# exposure_start_utc).
_DROPPED_ID_VARS = ("exposure_start",)

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

# Weather measurement columns merged from WeatherData.csv (the exposure
# identity column exposure_number/exposure_start_utc are the join keys, not
# metrics, so they are excluded here).
_WEATHER_METRICS = (
    "temperature",
    "dew_point",
    "humidity",
    "pressure",
    "wind_speed",
    "wind_direction",
    "wind_gust",
    "cloud_cover",
    "sky_temperature",
    "sky_brightness",
    "sky_quality",
)

# Namespace prefix applied to each metric name in the wide output.
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
_METRIC_GROUP.update({c: "weather" for c in _WEATHER_METRICS})


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


def _summary_name(column: str) -> str:
    """Name used in summary.constants/unpopulated.

    Weather columns are namespaced (weather.temperature) to match their metric
    names and avoid ambiguity; existing columns keep their raw CSV names.
    """
    if column in _WEATHER_METRICS:
        return f"weather.{column}"
    return column


def _norm_ts(value: Any) -> str | None:
    """Canonicalize a timestamp to seconds-precision UTC ISO-8601 with a Z
    suffix (e.g. ``2026-09-22T02:16:25Z``).

    Timezone-bearing inputs are shifted to UTC and microseconds are dropped.
    Naive inputs (no timezone marker) are treated as already-UTC, matching the
    ExposureStartUTC column this function normalizes. Strings that cannot be
    parsed fall back to the raw text (exact match) so join keys stay stable.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row.get(key)] = out.get(row.get(key), 0) + 1
    return out


def widen_imaging_metadata(
    rows: list[dict[str, Any]],
    weather_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reshape wide metadata rows into a wide table with one row per exposure.

    Identity fields (file_path, date, exposure_number, exposure_start,
    duration, filter_name, ...) keep their names; exposure_start is the
    ExposureStartUTC value normalized to seconds-precision UTC ISO-8601 with a
    Z suffix, and the naive local ExposureStart column is dropped. Metric
    columns are namespaced by group (quality.hfr, guiding.rms,
    background.adu_mean, ...) and only genuinely varying metrics appear as
    columns: columns whose cleaned values are all identical (constants) move
    to summary.constants, and columns with no populated values at all (ASCOM
    NaN/-1, empty, n/a, and the 0 sentinel NINA writes for unmeasured
    quality/guiding/CCD metrics) move to summary.unpopulated.

    When ``weather_rows`` is given, per-exposure weather samples from
    WeatherData.csv are merged onto each frame by its UTC exposure timestamp
    (exposure_start_utc) and surface as weather.* columns in the same row
    (weather.temperature, ...), with weather constants/unpopulated listed
    under their namespaced weather.* names.
    """
    if not rows:
        return {"summary": {"count": 0}, "rows": []}

    weather_by_ts: dict[str, dict[str, Any]] = {}
    if weather_rows:
        for w in weather_rows:
            key = _norm_ts(w.get("exposure_start_utc"))
            if key is None:
                continue
            weather_by_ts.setdefault(key, w)

    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        merged = dict(row)
        ts = row.get("exposure_start_utc")
        weather = weather_by_ts.get(_norm_ts(ts)) if (ts and weather_by_ts) else None
        for c in _WEATHER_METRICS:
            merged[c] = weather.get(c) if weather else None
        merged_rows.append(merged)
    rows = merged_rows

    columns = [k for k in rows[0] if k not in ("image_type", "source")]
    cleaned = {c: [_clean_metric(c, row.get(c)) for row in rows] for c in columns}

    id_columns = [c for c in _ID_VARS if c in rows[0]]
    present_metrics: list[str] = []
    constants: dict[str, Any] = {}
    unpopulated: list[str] = []
    for c in columns:
        if c in _ID_VARS or c in _DROPPED_ID_VARS:
            continue
        name = _summary_name(c)
        present = [v for v in cleaned[c] if v is not None]
        if not present:
            unpopulated.append(name)
            continue
        distinct = list(dict.fromkeys(present))
        if len(distinct) == 1 and len(present) == len(rows):
            constants[name] = distinct[0]
        else:
            present_metrics.append(c)

    out_rows = []
    for i, row in enumerate(rows):
        out = {}
        for k in id_columns:
            name = _OUTPUT_ID_NAME.get(k, k)
            if k == "exposure_start_utc":
                out[name] = _norm_ts(row[k])
            else:
                out[name] = row[k]
        for c in present_metrics:
            out[_metric_name(c)] = cleaned[c][i]
        out_rows.append(out)

    summary = {
        "count": len(rows),
        "by_filter": _counts(rows, "filter_name"),
        "by_date": _counts(rows, "date"),
    }
    if constants:
        summary["constants"] = constants
    if unpopulated:
        summary["unpopulated"] = sorted(unpopulated)
    return {"summary": summary, "rows": out_rows}
