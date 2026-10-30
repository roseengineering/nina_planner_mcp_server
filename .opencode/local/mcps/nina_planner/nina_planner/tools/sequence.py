from datetime import UTC, datetime
from typing import Any, cast

from .. import client, config, metadata, plan_utils
from ..app import mcp
from ..models.observatory import ObservatoryEquipment
from ..models.plan import ObservationPlan
from ..models.profile import ObservatoryProfile
from ..models.request import SequenceRequest
from ..progress import group_verdict, plan_progress, plan_with_remaining
from ..sequence import (
    build_sequence_many,
    target_payload_darks,
    target_payload_flats,
    target_payload_lights,
    validate_dark_variant,
)
from ..sequence_state import project_sequence_state
from . import equipment as equipment_tools


@mcp.tool()
async def load_sequence(
    # The empty list is the API (it means stow) and is never mutated.
    request: list[SequenceRequest] = [],  # noqa: B006
) -> str:
    """Loads a sequence with safety guardrails — it never starts it; call start_sequence() to begin acquisition.

    `request` is a LIST of SequenceRequest objects composing a whole night into a single N.I.N.A. sequence. It defaults to an empty list.

    The list composes ONE sequence: a single Start area, one Target area holding each step's containers in list order, and one End area that always stows — one bounded 3-minute "On Safe" wait, then park (or home) the mount and warm the camera. An EMPTY list (the default) is therefore the STOW: an empty Target area plus that closing End area. There is no `stow`, `park`, `warm`, or `unpark` action — every action is plan-backed, and `load_sequence()` with no arguments is the teardown.

    Every entry is one `request` object: `action`, plus `variant` (how the action runs), `plan` (path to the observation-plan JSON file), `pointing_index`, `mode`, and the optional quality thresholds.

    `action` decides WHAT gets built from `request.plan`, which every action requires:
    - `light` / `dark` / `bias` / `flat` build that frame sequence. `pointing_index` (1-based, optional, default 1 when omitted) selects which pointing of the plan to load; it applies to lights only. Calibration (dark/bias/flat) is plan-level: its remaining is computed for the whole plan, not per pointing, so a calibration request must omit `pointing_index` — passing one is rejected. Lights embed the attribution token in the target name so each pointing's frames are attributed independently: `_{base_plan_id}_` for a single-pointing plan (shared with the plan's calibration), `_{base_plan_id}-{pointing_index}_` per pane for a mosaic. Light loads require `$$TARGETNAME$$` in the active profile's FilePattern for attribution. In the default `remaining` mode the sequence only acquires frames still needed (total minus frames already attributed to this plan/pointing in the imaging metadata); if nothing remains it reports the plan as complete and loads nothing — except when a calibration window has expired with frames still missing, where it reports the expired deficit (and still loads nothing, since no frame shot now could be credited) rather than "complete". max_hfr and/or min_detected_stars exclude light frames failing quality thresholds from the acquired count. Pass `mode="full"` to acquire the entire plan again. A full-mode load of a group that is already complete appends a warning naming the frame count, because nothing downstream can verify the outcome afterwards: `get_plan_progress` reports the plan `complete: true` whether the re-shoot delivered every frame or a handful, since its acquired counts are cumulative across runs. Check what actually landed with `audit_plans` and the per-date frame folders after a full-mode run.

    `variant` decides HOW an action runs and is scoped to it — it is validated against the action, and a bad pairing fails naming the entry:
    - `flat` REQUIRES `dawn` (morning twilight — waits for dawn) or `dusk` (evening twilight — waits for sunset).
    - `dark` and `bias` take `wheel` (default — park the wheel on the profile's `Dark` filter slot), `cover` (close the motorized cover — e.g. a flip-flat — around the exposures, then reopen it so a later step never shoots through a closed cover), or `shutter` (expose without moving the filter wheel, relying on the camera's shutter). The variant is capability-checked at load: `wheel` needs a configured filter wheel with a `Dark` slot, `cover` a configured flat device reporting open/close support, `shutter` a camera reporting HasShutter. A variant the rig cannot perform fails naming the reason and the variants it supports — the default is checked the same way, so an omitted variant never silently picks another method.
    - `light` takes no variant.

    Steps run in order as the sequence reaches them; a step carrying its own wait conditions (lights, flats) waits, or is skipped when its window has passed, without blocking the steps behind it, so list order controls timing. Entries already complete in `remaining` mode are skipped and reported; if every entry is complete, nothing is sent to N.I.N.A. Profile, equipment, and metadata are fetched once per call, one POST carries the whole sequence, and a validation failure names its 0-based position (`request[1]: ...`).

    Fails if a sequence is already running — NINA's own error is reported; call stop_sequence() first."""
    if not isinstance(request, list):
        raise ValueError(
            "`request` must be a list of SequenceRequest objects "
            f"(it defaults to an empty list = stow), got {type(request).__name__}."
        )
    requests: list[SequenceRequest] = []
    for i, item in enumerate(request):
        if isinstance(item, SequenceRequest):
            requests.append(item)
            continue
        try:
            requests.append(SequenceRequest.model_validate(item))
        except ValueError as e:
            raise ValueError(f"request[{i}]: not a valid SequenceRequest: {e}") from e
    return await _load_many(requests)


async def _full_mode_note(
    plan: ObservationPlan,
    group: str,
    pointing_index: int,
    total: int,
    now: datetime,
) -> str:
    """Warning appended to a ``mode="full"`` load whose frames already exist.

    ``mode="full"`` re-acquires the whole group whatever is already
    attributed, so loading it against a complete plan silently collapses
    "acquire more" and "acquire again" into one another. Nothing downstream
    can tell them apart afterwards: ``get_plan_progress`` reports the plan
    ``complete: true, remaining: 0`` either way, because acquired counts are
    cumulative across runs. Loading is the last point where the operator can
    still revisit the decision, and the only point where the re-shoot's
    intended size is known — so say it here.

    Lights only. Calibration is pool-credited by type+filter across plans, so
    a dark/bias/flat group reads "already complete" on nearly every plan and
    warning on it would fire on every routine calibration load.

    A metadata read failure must not block the load: ``mode="full"`` is the
    mode chosen precisely when remaining counts are not to be trusted, and a
    plan file plus a full-mode request is enough to load. Any read error
    therefore yields no warning rather than an error.
    """
    if group != "light":
        return ""
    try:
        rows = await metadata.read_metadata()
    except (RuntimeError, FileNotFoundError):
        return ""
    progress = plan_progress(plan, rows, pointing_index=pointing_index, now=now)
    if any(item["remaining_count"] > 0 for item in progress[group]):
        return ""
    return (
        f" ⚠️ already complete — mode=full re-acquires all {total} frames on "
        "top of what is already attributed. get_plan_progress will still read "
        "complete:true afterwards, so verify what actually landed with "
        "audit_plans and the per-date frame folders."
    )


async def _load_many(requests: list[SequenceRequest]) -> str:
    """Compose ``requests`` into one sequence and load it with one POST.

    Each entry is validated on its own; profile, equipment, metadata, and the
    reference clock are fetched once for the whole list; and a failure names
    the 0-based position of the entry that caused it (``request[1]: ...``).
    Entries already complete in ``remaining`` mode contribute no payload, so
    a fully-complete list never POSTs. An empty list is the stow: no steps at
    all, just the stow End area. Every list's End area stows (park, then
    warm), so the night ends parked no matter what ran (see load_sequence).
    """
    state: dict[str, Any] = {}

    async def profile() -> ObservatoryProfile:
        if "profile" not in state:
            state["profile"] = await equipment_tools.get_site_profile()
        return cast(ObservatoryProfile, state["profile"])

    async def equipment() -> ObservatoryEquipment:
        if "equipment" not in state:
            state["equipment"] = await equipment_tools._get_site_equipment_status(
                await profile()
            )
        return cast(ObservatoryEquipment, state["equipment"])

    async def rows() -> list[dict[str, Any]]:
        if "rows" not in state:
            try:
                state["rows"] = await metadata.read_metadata()
            except (RuntimeError, FileNotFoundError) as e:
                raise ValueError(
                    f"Cannot compute remaining frames: {e}. Pass mode='full' to "
                    "load the whole plan regardless."
                ) from e
        return cast(list[dict[str, Any]], state["rows"])

    async def now() -> datetime:
        if "now" not in state:
            # Computed once so every step in the sequence shares one anchor.
            state["now"] = datetime.now(UTC).replace(microsecond=0)
        return cast(datetime, state["now"])

    async def step(request: SequenceRequest) -> tuple[list[dict[str, Any]], str]:
        action = request.action
        # Every action reads the plan's same-named exposure group.
        group = action
        # Reported per step so the caller sees which recipe (variant) ran.
        label = (
            f"`{action}` ({request.variant})"
            if request.variant is not None
            else f"`{action}`"
        )
        if request.plan is None:
            raise ValueError(
                f"action={action!r} requires a plan: pass `request.plan`, "
                "the path to the observation-plan JSON file to load."
            )
        plan = await plan_utils.load_plan(request.plan)
        # pointing_index selects a pane of the plan; only lights are per-
        # pane. Calibration (dark/bias/flat) is plan-level: an index there
        # is rejected, not silently ignored, so a "remaining is per pointing"
        # mental model gets corrected at the request instead of at 2 a.m.
        if action != "light" and request.pointing_index is not None:
            raise ValueError(
                f"action={action!r} is plan-level: pointing_index="
                f"{request.pointing_index} is not used — remaining "
                "calibration is computed for the whole plan, not per "
                "pointing. Remove pointing_index from this request."
            )
        pointing_index = (
            request.pointing_index if request.pointing_index is not None else 1
        )
        where = f" pointing {pointing_index}" if action == "light" else ""
        site_profile = await profile()
        await plan_utils.validate_filters(plan, site_profile)
        plan_utils.validate_position_angle(plan, site_profile)
        if action == "light":
            plan_utils.check_pointing_index(plan, pointing_index)
        eq = await equipment()

        # Capability check before anything is counted or built: a dark/bias
        # variant this rig cannot perform fails here, naming the variants it
        # supports, instead of at 2 a.m. inside N.I.N.A. It runs for the
        # default (`wheel`) exactly as for an explicit variant, and ahead of
        # the remaining-mode metadata read, so the answer never depends on
        # how many frames are left or on the imaging root being readable.
        if action in ("dark", "bias"):
            validate_dark_variant(request.variant or "wheel", eq, site_profile)

        if request.mode == "remaining":
            progress = plan_progress(
                plan,
                await rows(),
                pointing_index=pointing_index,
                max_hfr=request.max_hfr,
                min_detected_stars=request.min_detected_stars,
                max_guiding_rms_arcsec=request.max_guiding_rms_arcsec,
                now=await now(),
            )
            items = progress[group]
            if all(item["remaining_count"] == 0 for item in items):
                total = sum(item["total_count"] for item in items)
                # remaining_count == 0 is not proof of completion — the
                # decision reads the shared rule (progress.group_verdict):
                # plan_progress zeroes a calibration group's remaining once
                # its window closes, so an expired window with frames still
                # missing must read expired_with_deficit here, not complete.
                # Nothing to load either way (a frame shot now could not be
                # credited), but say which case this is — calling an expired
                # deficit "complete" invites a pointless re-shoot when the
                # real fix is a new plan or a raised calibration_window_days.
                verdicts = [group_verdict(item) for item in items]
                if "expired_with_deficit" in verdicts:
                    # An expired verdict means acquired < total for that
                    # group, so the summed deficit below is always >= 1.
                    expired_deficit = sum(
                        int(item.get("deficit_count", 0))
                        for item, verdict in zip(items, verdicts, strict=True)
                        if verdict == "expired_with_deficit"
                    )
                    return [], (
                        f"{label}{where} — calibration window EXPIRED with "
                        f"{expired_deficit} frame(s) short of {total} "
                        "total. Nothing to load: no frame shot now could be "
                        "credited. Raise the plan's calibration_window_days "
                        "or write a new plan to reopen the window."
                    )
                return [], (
                    f"{label}{where} is complete — {total} "
                    f"of {total} frames already acquired; nothing to load."
                )
            note = (
                f"{sum(item['remaining_count'] for item in items)} frame(s) to acquire"
            )
            plan = plan_with_remaining(plan, progress)
        elif request.mode == "full":
            frame_groups: dict[str, list[Any]] = {
                "light": plan.light_frames,
                "dark": plan.dark_frames,
                "bias": plan.bias_frames,
                "flat": plan.flat_frames,
            }
            frames = frame_groups[group]
            total = sum(f.total_count for f in frames)
            note = f"{total} frame(s), mode=full" + await _full_mode_note(
                plan, group, pointing_index, total, await now()
            )
        else:
            raise ValueError(
                f"Unsupported mode: {request.mode} (use 'remaining' or 'full')"
            )

        if action == "light" and "$$TARGETNAME$$" not in (
            site_profile.file_pattern or ""
        ):
            raise ValueError(
                "N.I.N.A. FilePattern must contain $$TARGETNAME$$ "
                "for light-frame plan attribution."
            )

        if action == "light":
            payload = target_payload_lights(
                plan, eq, site_profile, pointing_index=pointing_index
            )
        elif action == "bias":
            payload = target_payload_darks(
                plan,
                eq,
                site_profile,
                bias=True,
                variant=request.variant or "wheel",
            )
        elif action == "dark":
            payload = target_payload_darks(
                plan, eq, site_profile, variant=request.variant or "wheel"
            )
        else:  # flat — validated to carry a dawn/dusk variant
            payload = target_payload_flats(
                plan, eq, site_profile, dusk=request.variant == "dusk"
            )
        return payload, f"{label}{where} — {note}"

    if not requests:
        # The empty request list is the stow; it keeps the stow's guardrail —
        # a mount that can neither park nor home has nothing to stow, and a
        # stow that silently does nothing is worse than an error.
        eq = await equipment()
        mount = eq.mount
        can_park = bool(mount and mount.can_park)
        can_home = bool(mount and mount.can_find_home)
        if not (can_park or can_home):
            raise ValueError(
                "Mount can neither park nor home "
                f"(can_park={can_park}, can_find_home={can_home}) — nothing to stow."
            )

    lines: list[str] = []
    payloads: list[list[dict[str, Any]]] = []
    for i, request in enumerate(requests):
        try:
            payload, note = await step(request)
        except ValueError as e:
            raise ValueError(f"request[{i}]: {e}") from e
        payloads.append(payload)
        lines.append(f"request[{i}]: {note}")

    if requests:
        lines.append("")
    # The empty list is the stow: no steps, but the End area still parks (or
    # homes) then warms, so it always loads.
    if any(payloads) or not requests:
        # The End area always stows (park, then warm), so the empty list —
        # no steps at all — is exactly the stow.
        await client.load_sequence_payload(
            build_sequence_many(await equipment(), payloads)
        )
        if requests:
            lines.append(
                f"Sequence loaded ({sum(1 for p in payloads if p)} of "
                f"{len(requests)} step(s)) — call start_sequence() to begin."
            )
        else:
            lines.append(
                "`stow` sequence loaded (empty request list) — call "
                "start_sequence() to begin."
            )
    else:
        lines.append(
            "Nothing to load — every step is already complete; no sequence was "
            "sent to N.I.N.A."
        )
    return "\n".join(lines)


@mcp.tool()
async def stop_sequence() -> str:
    """Stops the currently running NINA sequence immediately, leaving the telescope where it currently is — it does not stow the scope. Use for an urgent halt, to interrupt a stuck/looping sequence, or when the running sequence isn't what you wanted. If you then want to stow for the night, load `load_sequence()` (empty list = stow) and start it. `load_sequence` refuses to load while a sequence is running, so call this first and wait — it returns once NINA reports the sequencer has actually stopped. Avoid stopping an in-progress teardown during the stow maneuver unless safety requires it, since interrupting mid-slew can leave the scope in an unsafe position."""
    await client.stop_running_sequence_and_wait()
    return "Sequence stopped."


@mcp.tool()
async def start_sequence() -> str:
    """Starts the currently loaded NINA sequence immediately. Use this after load_sequence(), which only loads a sequence and never starts it."""
    await client.api_get(config.SEQUENCE_START_PATH)
    return "Sequence started."


@mcp.tool()
async def get_sequence_state(detail: str = "summary") -> Any:
    """Returns the loaded sequence and the status of its steps. Use it to determine whether a sequence is loaded, running, completed, failed, or waiting. Read-only.

    detail:
      "summary" (default) - compact per-step view: `target_name`, `plan_id` / `pointing_index` (attribution), `frame_type`, `status`, running `phase`, `exposure` (seconds, gain, offset, binning, filter, `completed`/`total` frames), `horizon` (`current_altitude`, `min_altitude`, `expected_time`; null if unavailable), and `fired_events` (SendEvent messages whose node is FINISHED). `issues` lists errors on FAILED nodes only (per step and overall); the top-level `preflight_issues` lists validation notes such as "Focuser not connected" that do not mean failure.
      "raw" - full unfiltered /sequence/state payload (per-node `Issues`, expanded Smart Exposure children, no `$type`). Its node-level `Iterations` is the completed count; the loop total is in `Conditions[].Iterations`."""
    if detail not in ("summary", "raw"):
        raise ValueError('detail must be "summary" or "raw"')
    state = await client.api_get(config.SEQUENCE_STATE_PATH)
    if detail == "raw":
        return state
    try:
        json_state = await client.api_get(config.SEQUENCE_JSON_PATH)
    except Exception:
        json_state = None
    return project_sequence_state(state, json_state)
