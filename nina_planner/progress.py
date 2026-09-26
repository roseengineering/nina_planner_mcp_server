from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
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
    """Append an ISO-8601 timestamped entry to ``progress.md``.

    Matches the convention described in ``AGENTS.md`` — each significant
    action gets one entry. The block is appended as a markdown ``##`` section
    (timestamp + short title line, followed by the entry body) so both humans
    and later agent sessions can grep it by header. Existing content is
    preserved; the file always ends in a newline.

    The path to ``progress.md`` is taken from :data:`nina_planner.server.PROJECT_DIR`,
    imported lazily to avoid a module-load cycle with ``server.py``.
    """
    from .server import PROJECT_DIR

    now = datetime.now().astimezone()
    timestamp = now.isoformat(timespec="seconds")
    block = f"\n## {timestamp} — auto\n\n{text.strip()}\n"
    path = PROJECT_DIR / "progress.md"
    async with await anyio.open_file(path, "a", encoding="utf-8") as f:
        await f.write(block)


_BRACKET_RE = re.compile(r"^.*?\s*\[(?P<embedded>[^\]]+)\]")


def _embedded_plan_id(target: str) -> str:
    m = _BRACKET_RE.match(target)
    if m:
        return m.group("embedded").strip()
    return ""


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
    path = (row.get("file_path") or "").strip()
    if not path:
        return False
    return _embedded_plan_id(path) == plan.effective_plan_id(pointing_index)


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
        if hfr > max_hfr:
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
        if rms > max_guiding_rms_arcsec:
            return False
    return True


def _within_age(
    row: dict[str, Any],
    max_age_days: int | None,
    reference_date: datetime.date | None,
) -> bool:
    if max_age_days is None:
        return True
    if reference_date is None:
        return True
    raw = row.get("date")
    if not raw:
        return False
    try:
        frame_date = datetime.fromisoformat(str(raw)).date()
    except (TypeError, ValueError):
        return False
    cutoff = reference_date - timedelta(days=max_age_days)
    return frame_date >= cutoff


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
    reference_date: datetime.date | None = None,
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
    return _within_age(row, max_age_days, reference_date)


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
    reference_date: datetime.date | None = None,
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
            reference_date=reference_date,
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
    reference_date: datetime.date | None = None,
) -> list[dict[str, Any]]:
    """Rows attributed to `plan` for `image_type`.

    Lights match by the plan_id embedded in their file path (with the
    pointing_index suffix appended at load time); dark/bias/flat frames
    match the plan's exposure specs (filter too, for flats) and must fall
    within the calibration age window when one is configured.
    """
    image_type_upper = image_type.upper()
    if image_type_upper == "LIGHT":
        return [
            r
            for r in rows
            if _row_matches(r, "LIGHT", plan=plan, pointing_index=pointing_index)
        ]
    if image_type_upper == "FLAT":
        specs = [(g.filter_name, g.exposure_time_seconds) for g in plan.flat]
    elif image_type_upper == "DARK":
        specs = [(None, g.exposure_time_seconds) for g in plan.dark]
    elif image_type_upper == "BIAS":
        specs = [(None, None)]
    else:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        for filter_name, exposure in specs:
            if _row_matches(
                row,
                image_type_upper,
                filter_name=filter_name,
                exposure=exposure,
                max_age_days=max_age_days,
                reference_date=reference_date,
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
) -> dict[str, list[dict[str, Any]]]:
    reference_date = datetime.now(UTC).date()

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
                max_age_days=plan.calibration_max_age_days,
                reference_date=reference_date,
            )
            out.append(
                {
                    **data,
                    "acquired_count": acquired,
                    "remaining_count": max(total - acquired, 0),
                }
            )
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
        for g, item in zip(groups, progress[key]):
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
