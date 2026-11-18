import json
import re
from pathlib import Path
from typing import Any

import anyio

from . import config
from .models.observatory import ObservatoryEquipment
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
    # "Requests a rotation" is ONE rule — Pointing.requests_rotation — shared
    # with the emission side (sequence/targets.py picks CenterAndRotate by it),
    # so 0 and None can never validate one way and emit another: a plan of
    # 0-valued panes (what plan_mosaic stamps) validates as "no rotation
    # needed" and must emit a plain center slew too.
    nonzero_idxs = [
        i for i, p in enumerate(plan.pointings, start=1) if p.requests_rotation()
    ]
    if not profile.equipment.has_rotator and nonzero_idxs:
        labeled = [
            f"pointing {i} ({p.label})" if p.label else f"pointing {i}"
            for i, p in enumerate(plan.pointings, start=1)
            if p.requests_rotation()
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


def require_parkable(equipment: ObservatoryEquipment) -> None:
    """Refuse a sequence the mount could not close at the end (``can_park``).

    THE ONE AUTHORITY for the park guard: ``tools/sequence.load_sequence``
    and ``scripts/gen_sequence.py`` both call this, so the refusal — and its
    exact wording — can only ever change in one place. Every loaded list ends
    with the End-area park and the wait stages park first, so a mount that
    cannot park would sit unparked through a long wait while
    ``PARK-STARTING``/``PARK-FINISHED`` announced an instruction list that
    does nothing. A park that silently does nothing is worse than an error.

    The builders keep their own conditional emission (``sequence_park_scope``
    and friends return ``[]`` for such a mount) as a defensive default: they
    are pure functions over whatever equipment they are handed and are called
    directly by tests with a bare ``ObservatoryEquipment()``. They never raise
    — raising is this function's job, at the boundary, before anything is
    sent.
    """
    can_park = bool(equipment.mount and equipment.mount.can_park)
    if can_park:
        return
    raise ValueError(
        f"Mount cannot park (can_park={can_park}) — nothing to park: "
        "the End area would announce PARK-STARTING/PARK-FINISHED "
        "around an instruction list that does nothing. Nothing was "
        "loaded."
    )


def require_light_capabilities(profile: ObservatoryProfile) -> None:
    """Refuse a lights step whose instructions this rig cannot execute.

    ``target_payload_lights`` emits a slew/center, ``start_guiding``/
    ``stop_guiding`` and ``run_autofocus`` **unconditionally**
    (``sequence/targets.py``), so a profile reporting no plate solver, no
    guider or no focuser would only discover the gap at run time, inside
    N.I.N.A., hours into the night — with the sequence loaded and the
    operator told it had started.

    Degrading instead (omitting those instructions) is deliberately not
    offered: it would silently change what the night acquires, and every
    emitted payload would then depend on the rig. Darks, bias and flats
    never emit these instructions and are never checked here.

    Callers wrap the message with their own entry position (``request[i]:``
    in ``load_sequence``), so this only has to name the action and each
    missing capability.
    """
    missing: list[str] = []
    if not (profile.plate_solver or profile.blind_plate_solver):
        missing.append(
            "no plate solver configured (neither plate_solver "
            "(PlateSolverType) nor blind_plate_solver (BlindSolverType)) — "
            "every lights step emits a slew/center"
        )
    if not profile.equipment.has_guider:
        missing.append(
            "no guider configured (equipment.has_guider=false) — every "
            "lights step emits start_guiding/stop_guiding"
        )
    if not profile.equipment.has_focuser:
        missing.append(
            "no focuser configured (equipment.has_focuser=false) — every "
            "lights step emits run_autofocus"
        )
    if missing:
        raise ValueError(
            "action='light' needs capabilities this rig does not report: "
            + "; ".join(missing)
            + ". Configure them in the N.I.N.A. profile, or load a "
            "`dark`/`bias`/`flat` step instead — nothing was loaded."
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
