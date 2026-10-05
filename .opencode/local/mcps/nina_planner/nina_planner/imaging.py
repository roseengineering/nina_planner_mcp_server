from __future__ import annotations

import csv
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from .nina_utils import to_snake


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


def read_imaging_csv(root: Path, image_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    image_type_upper = image_type.upper()
    for meta_path in root.rglob("ImageMetaData.csv"):
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
                continue
            if resolved:
                row["file_path"] = resolved
            enriched = dict(row)
            enriched.update({"frame_type": row_image_type, "source": "image_metadata"})
            rows.append(enriched)
    return rows


def read_weather_csv(root: Path) -> list[dict[str, Any]]:
    """Read per-exposure weather samples from every WeatherData.csv in ``root``.

    Mirrors ``read_imaging_csv``: keys are snake-cased. Rows share the exposure
    timestamps used by ``ImageMetaData.csv`` so they can be joined to frames.
    """
    rows: list[dict[str, Any]] = []
    for meta_path in root.rglob("WeatherData.csv"):
        for row in _read_csv(meta_path):
            enriched = dict(row)
            enriched.update({"frame_type": "WEATHER", "source": "weather"})
            rows.append(enriched)
    return rows


# Per-frame identity columns, kept once on each wide output row (they keep
# their raw CSV names and anchor the metrics in that row).
_ID_VARS = (
    "file_path",
    "exposure_number",
    "exposure_start_utc",
    "duration",
    "filter_name",
)

# Output name for the UTC timestamp identity column: the CSV's ExposureStartUTC
# is the only time-of-exposure column we keep (normalized to milliseconds-precision
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
    """Normalize ASCOM sentinel values (NaN, empty, n/a) to None.

    Single source of truth for ASCOM sentinel normalization. Used by
    :func:`_clean_metric` for ImageMetaData.csv values, by weather
    metadata loading, and indirectly by progress-quality filtering.

    Sentinels collapsed to None:
    - None itself.
    - Empty or whitespace-only strings.
    - Strings ``"nan"`` / ``"NaN"`` / ``"n/a"`` (case-insensitive, after strip).
    - NaN floats (``float('nan')``).

    Real values pass through: integers, finite floats, non-sentinel strings
    (including negative numbers, "0", "-1", etc.).
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        # Empty / whitespace-only / "nan" / "n/a" / "na" → None.
        if not stripped or stripped.lower() in ("nan", "n/a"):
            return None
        return value
    if isinstance(value, float):
        # NaN (float("nan")) → None. All other floats preserved.
        if math.isnan(value):
            return None
        return value
    # ints, bools, and anything else: preserve unchanged.
    return value


def _clean_metric(column: str, value: Any) -> Any:
    value = _ascom_none(value)
    if value is None:
        return None
    # Quality-metric-only sentinel: NINA's ImageMetaData.csv serializes
    # unmeasured ASCOM values as 0 (because the CSV layer coerces NaN to 0).
    # For quality metrics (HFR, RMS, stars, etc.) 0 carries no information
    # — it's a placeholder for "not measured", not a real reading. So we
    # filter it. For non-quality columns (e.g. temperature) 0 IS a real
    # value (legitimately cold) and is preserved.
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
    """Canonicalize a timestamp to milliseconds-precision UTC ISO-8601 with a Z
    suffix (e.g. ``2026-09-22T02:16:25.123Z``).

    Timezone-bearing inputs are shifted to UTC and sub-millisecond precision is
    truncated.
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
    milliseconds = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def _weather_join_key(row: dict[str, Any]) -> tuple[str, str] | None:
    """Return the stable per-exposure weather join key, if complete."""
    exposure_number = row.get("exposure_number")
    if exposure_number is None:
        return None
    exposure_number = str(exposure_number).strip()
    timestamp = _norm_ts(row.get("exposure_start_utc"))
    if not exposure_number or timestamp is None:
        return None
    return exposure_number, timestamp


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        out[value] = out.get(value, 0) + 1
    return out


def widen_imaging_metadata(
    rows: list[dict[str, Any]],
    weather_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reshape wide metadata rows into a wide table with one row per exposure.

    Identity fields (file_path, exposure_number, exposure_start,
    duration, filter_name, ...) keep their names; exposure_start is the
    ExposureStartUTC value normalized to milliseconds-precision UTC ISO-8601
    with a Z suffix, and the naive local ExposureStart column is dropped. Metric
    columns are namespaced by group (quality.hfr, guiding.rms,
    background.adu_mean, ...) and only genuinely varying metrics appear as
    columns: columns whose cleaned values are all identical (constants) move
    to summary.constants, and columns with no populated values at all (ASCOM
    NaN/-1, empty, n/a, and the 0 sentinel NINA writes for unmeasured
    quality/guiding/CCD metrics) move to summary.unpopulated.

    When ``weather_rows`` is given, per-exposure weather samples from
    WeatherData.csv are merged onto each frame by exposure number and its
    normalized UTC exposure timestamp, and surface as weather.* columns in the
    same row (weather.temperature, ...), with weather constants/unpopulated
    listed under their namespaced weather.* names.
    """
    if not rows:
        return {"summary": {"count": 0}, "rows": []}

    weather_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    ambiguous_weather_keys: set[tuple[str, str]] = set()
    if weather_rows:
        for w in weather_rows:
            key = _weather_join_key(w)
            if key is None:
                continue
            if key in ambiguous_weather_keys:
                continue
            existing = weather_by_key.get(key)
            if existing is None:
                weather_by_key[key] = w
            elif any(
                _clean_metric(c, existing.get(c)) != _clean_metric(c, w.get(c))
                for c in _WEATHER_METRICS
            ):
                # Conflicting samples with the same identity cannot be
                # attributed safely; leave that exposure's weather blank.
                del weather_by_key[key]
                ambiguous_weather_keys.add(key)

    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        merged = dict(row)
        key = _weather_join_key(row)
        weather = weather_by_key.get(key) if key is not None else None
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
    }
    if constants:
        summary["constants"] = constants
    if unpopulated:
        summary["unpopulated"] = sorted(unpopulated)
    return {"summary": summary, "rows": out_rows}
