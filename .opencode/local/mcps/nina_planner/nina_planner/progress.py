from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import anyio

from .models.plan import ObservationPlan

__all__ = [
    "filter_metadata_rows",
    "plan_progress",
    "plan_with_remaining",
    "append_progress_entry",
]


async def append_progress_entry(text: str) -> None:
    """Append an ISO-8601 timestamped entry to ``PROGRESS.md``.

    Matches the convention described in ``AGENTS.md`` — each significant
    action gets one entry. The block is appended as a markdown ``##`` section
    (timestamp + short title line, followed by the entry body) so both humans
    and later agent sessions can grep it by header. Existing content is
    preserved; the file always ends in a newline.

    The path to ``PROGRESS.md`` is taken from :data:`nina_planner.server.PROJECT_DIR`,
    imported lazily to avoid a module-load cycle with ``server.py``.
    """
    from .server import PROJECT_DIR

    now = datetime.now().astimezone()
    timestamp = now.isoformat(timespec="seconds")
    block = f"\n## {timestamp} — auto\n\n{text.strip()}\n"
    path = PROJECT_DIR / "PROGRESS.md"
    async with await anyio.open_file(path, "a", encoding="utf-8") as f:
        await f.write(block)


def _path_components(path: str) -> list[str]:
    """Split a metadata path independent of its Windows/POSIX separators."""
    return [part for part in path.replace("\\", "/").split("/") if part]


def _exposure_matches(row: dict[str, Any], exposure: float | None) -> bool:
    if exposure is None:
        return True
    raw = row.get("duration")
    if raw is None or raw == "":
        return False
    try:
        return abs(float(raw) - exposure) < 0.05
    except (TypeError, ValueError):
        return False


def _light_target_matches(
    plan: ObservationPlan,
    row: dict[str, Any],
    *,
    pointing_index: int = 1,
) -> bool:
    """True when the row's recorded path carries this pointing's plan-id token.

    The token is unique to a plan pointing, so any single path segment may
    carry it: N.I.N.A. puts `$$TARGETNAME$$` wherever the profile's
    `FilePattern` places it — in the filename, in the directory under LIGHT,
    or in an ancestor directory above the date folder.
    """
    path = (row.get("file_path") or "").strip()
    if not path:
        return False

    components = _path_components(path)
    expected_token = plan.attribution_token(pointing_index)

    return any(expected_token in component for component in components)


def _quality_accepted(
    row: dict[str, Any],
    max_hfr: float | None,
    min_detected_stars: int | None,
    max_guiding_rms_arcsec: float | None = None,
) -> bool:
    if max_hfr is not None:
        raw_hfr = row.get("hfr")
        if raw_hfr is None or raw_hfr == "":
            return False
        try:
            hfr = float(raw_hfr)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(hfr) or hfr > max_hfr:
            return False
    if min_detected_stars is not None:
        raw_stars = row.get("detected_stars")
        if raw_stars is None or raw_stars == "":
            return False
        try:
            stars = int(raw_stars)
        except (TypeError, ValueError):
            return False
        if stars < min_detected_stars:
            return False
    if max_guiding_rms_arcsec is not None:
        raw_rms = row.get("guiding_rms_arc_sec")
        if raw_rms is None or raw_rms == "":
            return False
        try:
            rms = float(raw_rms)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(rms) or rms > max_guiding_rms_arcsec:
            return False
    return True


def _session_start(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    pointing_index: int,
) -> datetime | None:
    """Earliest light exposure attributed to the pointing, or ``None``.

    This is the anchor for the calibration window: a calibration frame counts
    only when shot within ``calibration_window_days`` of this instant, on
    either side. Anchoring to the plan's own (fixed) light session rather
    than a moving "now" means calibration acquired for the session stays
    valid, instead of expiring and being re-acquired on every later run.
    Timezone offsets are dropped, matching :func:`_within_age`.
    """
    stamps: list[datetime] = []
    for row in rows:
        if (row.get("image_type") or row.get("frame_type") or "").upper() != "LIGHT":
            continue
        if not _light_target_matches(plan, row, pointing_index=pointing_index):
            continue
        raw = row.get("exposure_start")
        if raw is None or raw == "":
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).strip())
        except (TypeError, ValueError):
            continue
        stamps.append(stamp.replace(tzinfo=None) if stamp.tzinfo else stamp)
    return min(stamps) if stamps else None


def _within_age(
    row: dict[str, Any],
    max_age_days: int | None,
    session_start: datetime | None,
) -> bool:
    """True when a calibration frame was shot inside the plan's window.

    The frame counts when it was shot within ``max_age_days`` of the plan's
    session anchor (the earliest attributed light), on either side:

    ``session_start - max_age_days <= shot_at <= session_start + max_age_days``

    Both bounds are inclusive. There is deliberately no asymmetry here: a dark
    shot a week before the first light counts, one shot a week after it
    counts, and one shot a month after it does not.

    Both times are on N.I.N.A.'s clock; any timezone offset is dropped rather
    than converted, so the comparison is host-timezone independent.
    """
    if max_age_days is None:
        return True
    if session_start is None:
        return True
    raw = row.get("exposure_start")
    if raw is None or raw == "":
        return False
    try:
        shot_at = datetime.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return False
    if shot_at.tzinfo is not None:
        shot_at = shot_at.replace(tzinfo=None)
    if session_start.tzinfo is not None:
        session_start = session_start.replace(tzinfo=None)
    window = timedelta(days=max_age_days)
    return session_start - window <= shot_at <= session_start + window


def _row_matches(
    row: dict[str, Any],
    image_type: str,
    *,
    filter_name: str | None = None,
    exposure: float | None = None,
    plan: ObservationPlan | None = None,
    pointing_index: int = 1,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
    max_age_days: int | None = None,
    session_start: datetime | None = None,
) -> bool:
    if (row.get("image_type") or row.get("frame_type") or "").upper() != image_type:
        return False
    if filter_name is not None and row.get("filter_name") != filter_name:
        return False
    if not _exposure_matches(row, exposure):
        return False
    if image_type == "LIGHT":
        if plan is not None and not _light_target_matches(
            plan, row, pointing_index=pointing_index
        ):
            return False
        return _quality_accepted(
            row, max_hfr, min_detected_stars, max_guiding_rms_arcsec
        )
    return _within_age(row, max_age_days, session_start)


def _count_matching(
    rows: list[dict[str, Any]],
    image_type: str,
    *,
    filter_name: str | None = None,
    exposure: float | None = None,
    plan: ObservationPlan | None = None,
    pointing_index: int = 1,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
    max_age_days: int | None = None,
    session_start: datetime | None = None,
) -> int:
    count = 0
    for row in rows:
        if _row_matches(
            row,
            image_type,
            filter_name=filter_name,
            exposure=exposure,
            plan=plan,
            pointing_index=pointing_index,
            max_hfr=max_hfr,
            min_detected_stars=min_detected_stars,
            max_guiding_rms_arcsec=max_guiding_rms_arcsec,
            max_age_days=max_age_days,
            session_start=session_start,
        ):
            count += 1
    return count


def filter_metadata_rows(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    image_type: str,
    *,
    pointing_index: int = 1,
    max_age_days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Rows attributed to `plan` for `image_type`.

    Lights match by the plan_id embedded in their file path (with the
    pointing_index suffix appended at load time); dark/bias/flat frames match
    the plan's exposure specs (filter too, for flats). Calibration is anchored
    to the plan's light session: a frame counts when it was shot within
    ``max_age_days`` of ``session_start`` on either side, where
    ``session_start`` is the earliest attributed light exposure (falling back
    to ``now`` when the plan has no lights yet). This selects the frames that
    are *attributable*; whether the plan still *demands* more of them is a
    separate question answered by :func:`plan_progress` (a window that has
    closed around ``now`` reports zero remaining).
    """
    image_type_upper = image_type.upper()
    if image_type_upper == "LIGHT":
        return [
            r
            for r in rows
            if _row_matches(r, "LIGHT", plan=plan, pointing_index=pointing_index)
        ]
    specs: list[tuple[str | None, float | None]]
    if image_type_upper == "FLAT":
        specs = [(g.filter_name, g.exposure_time_seconds) for g in plan.flat]
    elif image_type_upper == "DARK":
        specs = [(None, g.exposure_time_seconds) for g in plan.dark]
    elif image_type_upper == "BIAS":
        specs = [(None, None)]
    else:
        return []
    session_start = _session_start(plan, rows, pointing_index) or now or datetime.now()
    out: list[dict[str, Any]] = []
    for row in rows:
        for filter_name, exposure in specs:
            if _row_matches(
                row,
                image_type_upper,
                filter_name=filter_name,
                exposure=exposure,
                max_age_days=max_age_days,
                session_start=session_start,
            ):
                out.append(row)
                break
    return out


def plan_progress(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    *,
    pointing_index: int = 1,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Acquired/remaining counts per exposure group for one pointing.

    Lights count by attribution (plan id in the file path) plus the optional
    quality thresholds. Calibration (flat/dark/bias) is anchored to the
    earliest attributed light, and is reported in two parts:

    * ``acquired_count`` — frames shot inside ``calibration_window_days`` of
      that anchor, on either side of it. Always truthful, whether or not the
      window is still open.
    * ``remaining_count`` — what the plan still demands. While ``now`` sits
      inside the window this is ``total - acquired``; once ``now`` falls
      outside it, no frame shot now could ever be credited, so remaining is
      reported as ``0`` rather than an unsatisfiable deficit (which would
      otherwise re-trigger acquisition on every later run).

    Calibration group items therefore also carry ``window_open`` (bool) and
    ``window_end`` (naive ISO timestamp of ``anchor + window`` on N.I.N.A.'s
    local clock), so a zero remaining can be told apart from a finished plan.
    Light items carry neither.
    """
    reference_now = now or datetime.now()
    if reference_now.tzinfo is not None:
        reference_now = reference_now.replace(tzinfo=None)
    session_start = _session_start(plan, rows, pointing_index) or reference_now
    window = timedelta(days=plan.calibration_window_days)
    window_open = session_start - window <= reference_now <= session_start + window
    window_end = session_start + window

    def group(image_type: str, groups: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for g in groups:
            data = g.model_dump()
            filter_name = data.get("filter_name")
            exposure = data.get("exposure_time_seconds")
            total = data["total_count"]
            acquired = _count_matching(
                rows,
                image_type,
                filter_name=filter_name,
                exposure=exposure,
                plan=plan if image_type == "LIGHT" else None,
                pointing_index=pointing_index,
                max_hfr=max_hfr,
                min_detected_stars=min_detected_stars,
                max_guiding_rms_arcsec=max_guiding_rms_arcsec,
                max_age_days=plan.calibration_window_days,
                session_start=session_start,
            )
            item: dict[str, Any] = {
                **data,
                "acquired_count": acquired,
                "remaining_count": max(total - acquired, 0),
            }
            if image_type != "LIGHT":
                item["window_open"] = window_open
                item["window_end"] = window_end.isoformat(timespec="seconds")
                if not window_open:
                    item["remaining_count"] = 0
            out.append(item)
        return out

    return {
        "light": group("LIGHT", plan.light),
        "flat": group("FLAT", plan.flat),
        "dark": group("DARK", plan.dark),
        "bias": group("BIAS", plan.bias),
    }


def plan_with_remaining(
    plan: ObservationPlan, progress: dict[str, list[dict[str, Any]]]
) -> ObservationPlan:
    def rebuild(groups: list[Any], key: str) -> list[Any]:
        out: list[Any] = []
        for g, item in zip(groups, progress[key], strict=True):
            remaining = item["remaining_count"]
            if remaining <= 0:
                continue
            out.append(type(g)(**{**g.model_dump(), "total_count": remaining}))
        return out

    return plan.model_copy(
        update={
            "light": rebuild(plan.light, "light"),
            "flat": rebuild(plan.flat, "flat"),
            "dark": rebuild(plan.dark, "dark"),
            "bias": rebuild(plan.bias, "bias"),
        }
    )
