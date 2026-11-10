import json
import re
from pathlib import Path
from typing import Any

import anyio

from . import config
from .models.plan import ObservationPlan
from .models.profile import ObservatoryProfile

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def sanitize_filename(s: str) -> str:
    return _INVALID_FILENAME_CHARS.sub("-", s)


async def validate_filters(plan: ObservationPlan, profile: ObservatoryProfile) -> None:
    profile_filter_names = {f.name for f in profile.filters}
    plan_filter_names: set[str] = set()
    for light in plan.light_frames:
        if light.filter_name:
            plan_filter_names.add(light.filter_name)
    for flat in plan.flat_frames:
        if flat.filter_name:
            plan_filter_names.add(flat.filter_name)
    if plan.autofocus.reference_filter_name:
        plan_filter_names.add(plan.autofocus.reference_filter_name)
    unknown = plan_filter_names - profile_filter_names
    if unknown:
        raise ValueError(
            f"Filter(s) not found in profile '{profile.profile_name}': "
            f"{sorted(unknown)}. "
            f"Available filters: {sorted(profile_filter_names)}"
        )


def validate_position_angle(plan: ObservationPlan, profile: ObservatoryProfile) -> None:
    set_idxs = [
        i
        for i, p in enumerate(plan.pointings, start=1)
        if p.position_angle_deg is not None
    ]
    unset_idxs = [
        i for i, p in enumerate(plan.pointings, start=1) if p.position_angle_deg is None
    ]
    if set_idxs and unset_idxs:
        raise ValueError(
            f"Plan mixes position_angle_deg: pointings {set_idxs} set it but "
            f"pointings {unset_idxs} do not. Either set position_angle_deg on "
            "every pointing or omit it from all."
        )
    nonzero_idxs = [
        i
        for i, p in enumerate(plan.pointings, start=1)
        if p.position_angle_deg is not None and p.position_angle_deg != 0
    ]
    if not profile.equipment.has_rotator and nonzero_idxs:
        labeled = [
            f"pointing {i} ({p.label})" if p.label else f"pointing {i}"
            for i, p in enumerate(plan.pointings, start=1)
            if p.position_angle_deg is not None and p.position_angle_deg != 0
        ]
        raise ValueError(
            f"Plan sets a nonzero position_angle_deg on {', '.join(labeled)} but "
            f"profile '{profile.profile_name}' has no rotator. Without a rotator "
            "every pointing must have position_angle_deg 0 (or omit it). Either "
            "add a rotator to the profile or use position_angle_deg 0."
        )


def check_pointing_index(plan: ObservationPlan, pointing_index: int) -> None:
    if pointing_index < 1 or pointing_index > len(plan.pointings):
        raise ValueError(
            f"pointing_index={pointing_index} out of range (plan has "
            f"{len(plan.pointings)} pointings; valid 1..{len(plan.pointings)})"
        )


async def load_plan(file_path: str) -> ObservationPlan:
    path = Path(file_path)
    if not path.is_absolute():
        path = config.PROJECT_DIR / path
    async with await anyio.open_file(path, "r", encoding="utf-8") as f:
        data = json.loads(await f.read())
    plan = ObservationPlan.model_validate(data)
    # On-disk invariant: a plan file always carries its pointings. The one
    # shape that validates with zero — `mosaic` + empty pointings — is
    # write_plan_file's INPUT form, never its output (it expands before
    # writing), so finding it here means the file was hand-edited and never
    # expanded. Refuse every consumer rather than let the oddities through:
    # get_plan_progress would read it as complete:true with nothing to
    # shoot, audit_plans would report no work at all, and only a light load
    # would fail (calibration never indexes pointings).
    if not plan.pointings:
        raise ValueError(
            f"{file_path}: plan has no pointings — its mosaic block was "
            "never expanded; re-run write_plan_file with this mosaic to "
            "regenerate the file"
        )
    return plan


def quality_kwargs(plan: ObservationPlan) -> dict[str, Any]:
    """The plan's light-frame quality thresholds as ``plan_progress`` kwargs.

    The single place a plan's ``quality`` section becomes counting rules, so
    every tool that counts this plan's frames (``load_sequence`` in remaining
    mode, ``get_plan_progress``) applies exactly the same thresholds without
    a caller repeating them — and so a *new* threshold field can never be
    wired into one tool and forgotten in another.

    Callers that must NOT filter — ``audit_plans`` and the archive, which
    answer "what is on disk" rather than "what counts" — simply do not call
    this.
    """
    q = plan.quality
    return {
        "max_hfr": q.max_hfr,
        "max_hfr_st_dev": q.max_hfr_st_dev,
        "min_detected_stars": q.min_detected_stars,
        "max_guiding_rms_arcsec": q.max_guiding_rms_arcsec,
    }
