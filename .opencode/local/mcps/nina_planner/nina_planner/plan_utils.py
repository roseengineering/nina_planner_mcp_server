"""Shared plan validation and discovery rules for tools and the CLI.

Each ``validate_*``/``require_*`` guard raises ``ValueError`` with an
operator-facing message before anything is sent to N.I.N.A., so a bad plan
never auto-connects a device on the way to failing. Discovery walks only
visible directories and sorts shallowest-first so an archived copy of a plan
never shadows the live one.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import anyio

from . import config
from .imaging import _walk_visible
from .models.observatory import ObservatoryEquipment
from .models.plan import ObservationPlan
from .models.profile import ObservatoryProfile

_NEW_PLAN_MODE = 0o644

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def write_text_atomic(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` via a temp sibling + ``os.replace``.

    The temp file lives in ``path``'s own folder so the rename is atomic
    (same filesystem), and a failed write leaves the original untouched and
    no stray ``.tmp`` file behind. An existing file keeps its permission
    bits; a new file gets ``0o644``, matching a plain ``open(..., "w")``.
    """
    try:
        mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        mode = _NEW_PLAN_MODE
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def sanitize_filename(s: str) -> str:
    """Replace characters forbidden in Windows filenames with ``-``.

    Also strips ASCII control characters, so the result is safe to embed in
    a frame path or archive folder name on any host.
    """
    return _INVALID_FILENAME_CHARS.sub("-", s)


async def validate_filters(plan: ObservationPlan, profile: ObservatoryProfile) -> None:
    """Check every filter the plan references exists in the profile.

    Covers light and flat frames plus the autofocus reference filter; a
    profile with no filters can only pass a plan that names none.

    Raises:
        ValueError: The plan names a filter the profile does not define.
    """
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
    """Enforce the plan's rotator rules across all of its pointings.

    ``position_angle_deg`` (degrees, 0–360 as written) must be set on every
    pointing or none, and a nonzero value requires the profile to report a
    rotator — 0 and ``None`` both mean "no rotation" and always validate and
    emit the same way.

    Raises:
        ValueError: The plan mixes set/unset angles, or requests a rotation
            the rig has no rotator to perform.
    """
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
    # with the emission side (sequence/targets.py picks CenterAndRotate by
    # it), so 0 and None can never validate one way and emit another.
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
    """Check a 1-based ``pointing_index`` names a pointing this plan has.

    Raises:
        ValueError: The index is below 1 or past the plan's last pointing.
    """
    if pointing_index < 1 or pointing_index > len(plan.pointings):
        raise ValueError(
            f"pointing_index={pointing_index} out of range (plan has "
            f"{len(plan.pointings)} pointings; valid 1..{len(plan.pointings)})"
        )


def require_parkable(equipment: ObservatoryEquipment) -> None:
    """Refuse a sequence the mount could not close at the end (``can_park``).

    THE ONE AUTHORITY for the park guard: ``load_sequence`` and
    ``scripts/gen_sequence.py`` both call this, so the refusal — and its
    exact wording — can only change in one place. Every loaded list ends
    with the End-area park and its wait stages park first, so a mount that
    cannot park would sit unparked around an announcement that does nothing.
    A park that silently does nothing is worse than an error.

    The builders keep their own conditional emission (``sequence_park_scope``
    and friends return ``[]``) as a defensive default for callers that build
    payloads directly (tests, inspection): they never raise — raising is
    this function's job, at the boundary, before anything is sent.
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
    in ``load_sequence``), so this only names the action and each missing
    capability.
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


def resolve_plan_dir(project_dir: str | None) -> Path:
    """The directory plan files are discovered in.

    A relative path resolves against the project directory; ``None`` selects
    the project directory itself. No I/O — the directory need not exist yet.
    """
    plan_dir = Path(project_dir) if project_dir else config.PROJECT_DIR
    if not plan_dir.is_absolute():
        plan_dir = config.PROJECT_DIR / plan_dir
    return plan_dir


def plan_files(plan_dir: Path) -> list[Path]:
    """All ``*.json`` files under ``plan_dir``, recursively, shallowest first.

    Plans may live in subfolders (``plans/``, ``archive/``, per-month dirs),
    so discovery recurses — but only through visible, ordinary directories
    (the ``_walk_visible`` prune: hidden dirs and ``node_modules`` /
    ``__pycache__`` are never entered, so a project's package JSONs are left
    alone). Directory symlinks are NOT followed (``os.walk`` default), so a
    symlink cycle can never hang an unattended audit.

    Results are sorted (depth, name): if two files carry the same plan id —
    an archived copy next to the live plan — the top-level canonical file
    wins the claiming pass deterministically instead of whatever plain
    ``sorted()`` would pick alphabetically (where ``archive/`` sorts before
    ``m47.json``).
    """
    found: list[Path] = []
    for directory, filenames in _walk_visible(plan_dir):
        found.extend(directory / name for name in filenames if name.endswith(".json"))
    found.sort(key=lambda p: (len(p.relative_to(plan_dir).parts), p.name))
    return found


async def load_plan(file_path: str) -> ObservationPlan:
    """Load and validate a plan JSON file into an ``ObservationPlan``.

    Relative paths resolve against the project directory. A plan whose mosaic
    block was never expanded (zero pointings) is refused rather than read as
    complete with nothing to shoot.

    Raises:
        OSError: The file cannot be read.
        ValueError: The JSON is malformed, fails validation, or has no
            pointings.
    """
    path = Path(file_path)
    if not path.is_absolute():
        path = config.PROJECT_DIR / path
    async with await anyio.open_file(path, "r", encoding="utf-8") as f:
        data = json.loads(await f.read())
    plan = ObservationPlan.model_validate(data)
    # On-disk invariant: a written plan always carries its pointings — the
    # one shape that validates with zero (mosaic + empty pointings) is
    # write_plan_file's INPUT form, never its output, so finding it here
    # means the file was hand-edited and never expanded. Refuse rather than
    # let it read as complete with nothing to shoot.
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
