from __future__ import annotations

import re
from typing import Any

from .models.plan import ObservationPlan

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


def _light_target_matches(plan: ObservationPlan, row: dict[str, Any]) -> bool:
    path = (row.get("file_path") or "").strip()
    if not path:
        return False
    return _embedded_plan_id(path) == plan.effective_plan_id()


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


def _count_matching(
    rows: list[dict[str, Any]],
    image_type: str,
    *,
    filter_name: str | None = None,
    exposure: float | None = None,
    plan: ObservationPlan | None = None,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
) -> int:
    count = 0
    for row in rows:
        if (row.get("image_type") or row.get("frame_type") or "").upper() != image_type:
            continue
        if filter_name is not None and row.get("filter_name") != filter_name:
            continue
        if not _exposure_matches(row, exposure):
            continue
        if image_type == "LIGHT":
            if plan is not None and not _light_target_matches(plan, row):
                continue
            if not _quality_accepted(
                row, max_hfr, min_detected_stars, max_guiding_rms_arcsec
            ):
                continue
        count += 1
    return count


def plan_progress(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    *,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
) -> dict[str, list[dict[str, Any]]]:
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
                max_hfr=max_hfr,
                min_detected_stars=min_detected_stars,
                max_guiding_rms_arcsec=max_guiding_rms_arcsec,
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
