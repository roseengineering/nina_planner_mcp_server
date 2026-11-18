"""Sequence MCP tools: load, start, stop and inspect N.I.N.A. sequences."""

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
    # The empty list is the API (it means park) and is never mutated.
    request: list[SequenceRequest] = [],  # noqa: B006
) -> str:
    """Loads a sequence with safety guardrails — it never starts it; call start_sequence() to begin acquisition.

    `request` is a LIST of SequenceRequest objects composing a whole night into a single N.I.N.A. sequence. It defaults to an empty list.

    The list composes ONE sequence: a single Start area, one Target area holding each step's containers in list order, and one End area that always parks — one bounded 3-minute "On Safe" wait, then park the mount and warm the camera. An EMPTY list (the default) is therefore the PARK: an empty Target area plus that closing End area. There is no `park`, `warm`, or `unpark` action — every action is plan-backed, and `load_sequence()` with no arguments is the teardown.

    Every entry is one `request` object: `action`, plus `variant` (how the action runs), `plan` (path to the observation-plan JSON file), `pointing_index`, and `mode`. Quality thresholds are NOT request fields — they live in the plan file's `quality` section, so this tool and `get_plan_progress` always count the same frames.

    `action` decides WHAT gets built from `request.plan`, which every action requires:
    - `light` / `dark` / `bias` / `flat` build that frame sequence. `pointing_index` (1-based, optional, default 1 when omitted) selects which pointing of the plan to load; it applies to lights only. Calibration (dark/bias/flat) is plan-level: its remaining is computed for the whole plan, not per pointing, so a calibration request must omit `pointing_index` — passing one is rejected. Lights embed the attribution token in the target name so each pointing's frames are attributed independently: `_{base_plan_id}_` for a single-pointing plan (shared with the plan's calibration), `_{base_plan_id}-{pointing_index}_` per pane for a mosaic. Light loads require `$$TARGETNAME$$` in the active profile's FilePattern for attribution. In the default `remaining` mode the sequence only acquires frames still needed (total minus frames already attributed to this plan/pointing in the imaging metadata); if nothing remains it reports the plan as complete and loads nothing — except when a calibration window has expired with frames still missing, where it reports the expired deficit (and still loads nothing, since no frame shot now could be credited) rather than "complete". The plan's `quality` section (`max_hfr`, `max_hfr_st_dev`, `min_detected_stars`, `max_guiding_rms_arcsec` — all optional, defaulting to no filtering) excludes light frames failing those thresholds from the acquired count, fail-closed: a frame missing an enabled metric does not count. Pass `mode="full"` to acquire the entire plan again. A full-mode load of a group that is already complete appends a warning naming the frame count, because nothing downstream can verify the outcome afterwards: `get_plan_progress` reports the plan `complete: true` whether the re-shoot delivered every frame or a handful, since its acquired counts are cumulative across runs. Check what actually landed with `audit_plans` and the per-date frame folders after a full-mode run.

    `variant` decides HOW an action runs and is scoped to it — it is validated against the action, and a bad pairing fails naming the entry:
    - `flat` REQUIRES `dawn` (morning twilight — waits for dawn) or `dusk` (evening twilight — waits for sunset).
    - `dark` and `bias` take `wheel` (default — park the wheel on the profile's `Dark` filter slot), `cover` (close the motorized cover — e.g. a flip-flat — around the exposures, then reopen it so a later step never shoots through a closed cover), or `shutter` (expose without moving the filter wheel, relying on the camera's shutter). The variant is capability-checked at load: `wheel` needs a configured filter wheel with a `Dark` slot, `cover` a configured flat device reporting open/close support, `shutter` a camera reporting HasShutter. A variant the rig cannot perform fails naming the reason and the variants it supports — the default is checked the same way, so an omitted variant never silently picks another method.
    - `light` takes no variant.

    Steps run in order as the sequence reaches them; a step carrying its own wait conditions (lights, flats) waits, or is skipped when its window has passed, without blocking the steps behind it, so list order controls timing. Entries already complete in `remaining` mode are skipped and reported; if every entry is complete, nothing is sent to N.I.N.A. Profile, equipment, and metadata are fetched once per call, one POST carries the whole sequence, and a validation failure — or a plan file that cannot be read — names the entry's 0-based position (`request[1]: ...`). A mount that cannot park — or no mount at all — is rejected as soon as a sequence would load: the End area could not park it, and the wait stages that park first would leave it unparked. An all-complete list sends nothing and so needs no park, and loads without that error. A `light` entry is additionally rejected when the profile reports no plate solver, no guider or no focuser (its payload emits slew/center, guiding and autofocus unconditionally), and a `light`/`flat` entry when N.I.N.A. has no `Sequencer+` plugin (its payload carries Sequencer+ nodes that would deserialize to nothing). See the server instructions for the full park/home/safety policy.

    Fails if a sequence is already running — NINA's own error is reported; call stop_sequence() first.
    """
    if not isinstance(request, list):
        raise ValueError(
            "`request` must be a list of SequenceRequest objects "
            f"(it defaults to an empty list = park), got {type(request).__name__}."
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
    attributed, and nothing downstream can tell "acquire more" from
    "acquire again" afterwards (``get_plan_progress`` reads ``complete:
    true`` either way, its counts being cumulative across runs). Loading is
    the last point where the operator can still revisit the decision — so
    say it here. Lights only: calibration is pool-credited across plans and
    would read "already complete" on nearly every plan.

    A metadata read failure yields no warning rather than an error: this is
    an advisory line and must never block the load.
    """
    if group != "light":
        return ""
    try:
        # Light rows only — one scan of the imaging root, not four.
        rows = await metadata.read_metadata("light")
    # Best-effort by contract: any read/parse failure (profile fetch, dead
    # mount, bad UTF-8, surplus CSV cells) returns no warning instead of
    # aborting the load over a courtesy line.
    except Exception:
        return ""
    # Same thresholds as the remaining-mode count beside this warning (the
    # plan's `quality` section), so "already complete" means exactly what
    # remaining mode would have concluded.
    progress = plan_progress(
        plan,
        rows,
        pointing_index=pointing_index,
        **plan_utils.quality_kwargs(plan),
        now=now,
    )
    if any(item["remaining_count"] > 0 for item in progress[group]):
        return ""
    return (
        f" ⚠️ already complete — mode=full re-acquires all {total} frames on "
        "top of what is already attributed. get_plan_progress will still read "
        "complete:true afterwards, so verify what actually landed with "
        "audit_plans and the per-date frame folders."
    )


# Display names the Sequencer+ plugin may carry in N.I.N.A.'s
# ``/application/plugins`` list: README.md calls it "Sequencer+" while the
# plugin's csproj installs it as "Sequencer Plus". Both spellings are
# accepted (case-insensitively); a readable list containing neither fails
# the load and prints the list it saw, so a third spelling shows itself the
# first time the guard fires. Verified against the live rig with
# ``GET /v2/api/application/plugins`` before trusting this.
SEQUENCER_PLUS_PLUGIN_NAMES = ("Sequencer+", "Sequencer Plus")


async def _require_sequencer_plus() -> None:
    """Refuse a light/flat load when the Sequencer+ plugin is missing.

    ``target_payload_lights`` and every safetynet (flats included) emit
    ``NINA.Plugin.SequencerPlus.*`` nodes. Without the plugin N.I.N.A.
    deserializes them into nothing while the run still loads, still reaches
    its End area and still fires ``RUN-FINISHED``: a night that reports
    success and shoots zero frames.

    ninaAPI (behind every ``RUN-*``/``PARK-*`` event) is deliberately NOT
    checked: it is the "Advanced API" plugin this REST API is served by, so
    reaching that endpoint already proves it is installed.

    Verification is best effort: an unreadable plugin list logs a warning
    and lets the load go ahead; a list we CAN read is authoritative — a
    light/flat payload without the plugin fails here, naming it and the
    installed list.
    """
    try:
        plugins = await client.api_get("/application/plugins")
    except Exception as e:  # advisory by contract: never block on the read
        client.log_payload("_require_sequencer_plus:", f"cannot read plugin list: {e}")
        return
    if not isinstance(plugins, list) or not all(
        isinstance(name, str) for name in plugins
    ):
        client.log_payload(
            "_require_sequencer_plus:",
            f"unexpected plugin list shape ({type(plugins).__name__}): "
            f"{plugins!r} — cannot verify Sequencer+, loading anyway",
        )
        return
    installed = {name.strip().casefold() for name in plugins}
    if any(
        wanted in installed for wanted in map(str.casefold, SEQUENCER_PLUS_PLUGIN_NAMES)
    ):
        return
    wanted = " or ".join(repr(name) for name in SEQUENCER_PLUS_PLUGIN_NAMES)
    raise ValueError(
        f"the N.I.N.A. plugin {wanted} is not installed, and `light` and "
        "`flat` sequences need it — the lights countdown array and the "
        "safetynet's End Instruction Set are Sequencer+ nodes, and without "
        "the plugin they fail to deserialize while every exposure group "
        f"empties. Installed plugins: {sorted(plugins)!r}. Install "
        "Sequencer+ (or correct SEQUENCER_PLUS_PLUGIN_NAMES in "
        "nina_planner/tools/sequence.py if it is installed under another "
        "name) or load a `dark`/`bias` step instead — nothing was loaded."
    )


async def _load_many(requests: list[SequenceRequest]) -> str:
    """Compose ``requests`` into one sequence and load it with one POST.

    Each entry is validated on its own; profile, equipment, metadata, and the
    reference clock are fetched once for the whole list; and a failure names
    the 0-based position of the entry that caused it (``request[1]: ...``).
    Entries already complete in ``remaining`` mode contribute no payload, so
    a fully-complete list never POSTs. An empty list is the park: no steps at
    all, just the park End area. Every list's End area parks (then warms), so
    the night ends parked no matter what ran (see load_sequence) — which is
    why a mount that cannot park is rejected at the point a sequence would
    load: that park would silently do nothing.
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

        # Same guard, lights only: the lights payload emits slew/center,
        # start/stop guiding and run_autofocus unconditionally, so a rig
        # without a plate solver, a guider or a focuser would fail at run
        # time inside N.I.N.A. instead of here. Darks/bias/flats never emit
        # those instructions and are not checked. See
        # plan_utils.require_light_capabilities.
        if action == "light":
            plan_utils.require_light_capabilities(site_profile)

        if request.mode == "remaining":
            progress = plan_progress(
                plan,
                await rows(),
                pointing_index=pointing_index,
                **plan_utils.quality_kwargs(plan),
                now=await now(),
            )
            items = progress[group]
            if all(item["remaining_count"] == 0 for item in items):
                total = sum(item["total_count"] for item in items)
                # remaining_count == 0 is not proof of completion — read the
                # shared rule (progress.group_verdict): a closed calibration
                # window with frames missing must say expired, not complete,
                # since no frame shot now could be credited either way.
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

    lines: list[str] = []
    payloads: list[list[dict[str, Any]]] = []
    for i, request in enumerate(requests):
        try:
            payload, note = await step(request)
        except (ValueError, OSError) as e:
            # ValueError is the validation family (pydantic ValidationError
            # and every explicit check in step); OSError is a plan file that
            # cannot be READ (load_plan's FileNotFoundError above all) — both
            # get the `request[i]:` position prefix.
            raise ValueError(f"request[{i}]: {e}") from e
        payloads.append(payload)
        lines.append(f"request[{i}]: {note}")

    if requests:
        lines.append("")
    # The empty list is the park: no steps, but the End area still parks then
    # warms, so it always loads.
    if any(payloads) or not requests:
        # Checked at the point a sequence really is sent (every load — the
        # empty park included — ends with the End-area park, and
        # sequence_park_scope emits nothing for a mount that cannot park, so
        # the wait stages would sit unparked around a no-op announcement).
        # Checked here rather than up front so a fully-complete list stays a
        # clean "Nothing to load". The refusal itself is ONE shared function
        # (plan_utils.require_parkable), also used by scripts/gen_sequence.py.
        eq = await equipment()
        plan_utils.require_parkable(eq)

        # Same placement rule as the park guard: only for the two actions
        # whose payloads carry Sequencer+ nodes (lights and flats) — the
        # empty park and darks/bias read nothing.
        if any(
            request.action in ("light", "flat") and payload
            for request, payload in zip(requests, payloads, strict=True)
        ):
            await _require_sequencer_plus()

        await client.load_sequence_payload(build_sequence_many(eq, payloads))
        if requests:
            lines.append(
                f"Sequence loaded ({sum(1 for p in payloads if p)} of "
                f"{len(requests)} step(s)) — call start_sequence() to begin."
            )
        else:
            lines.append(
                "`park` sequence loaded (empty request list) — call "
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
    """Stops the currently running NINA sequence immediately, leaving the telescope where it currently is — it does not park the scope. Use for an urgent halt, to interrupt a stuck/looping sequence, or when the running sequence isn't what you wanted. If you then want to park for the night, load `load_sequence()` (empty list = park) and start it. `load_sequence` refuses to load while a sequence is running, so call this first and wait — it returns once NINA reports the sequencer has actually stopped. Avoid stopping an in-progress teardown during the park maneuver unless safety requires it, since interrupting mid-slew can leave the scope in an unsafe position."""
    await client.stop_running_sequence_and_wait()
    return "Sequence stopped."


@mcp.tool()
async def start_sequence() -> str:
    """Starts the currently loaded NINA sequence immediately. Use this after load_sequence(), which only loads a sequence and never starts it.

    It returns once the sequencer has ACTUALLY started — every node has left `CREATED` — mirroring stop_sequence()'s "returns once NINA reports the sequencer has stopped" contract. N.I.N.A. answers `GET /sequence/start` the moment it accepts the request, and a start can still abort in its first milliseconds (a start-time validation failure leaves every node `CREATED`, no RUN-STARTING and nothing logged), so the state is polled afterwards instead of trusting the 200. A sequence that starts and finishes before the first read (a fast park) is NOT a failure: N.I.N.A. leaves FINISHED nodes behind, and leaving `CREATED` is the signal. If nothing leaves `CREATED` within the budget the call raises with a status summary — check get_logs() and get_sequence_state() before retrying.
    """
    await client.api_get(config.SEQUENCE_START_PATH)
    await client.wait_until_sequence_started()
    return "Sequence started."


@mcp.tool()
async def get_sequence_state(detail: str = "summary") -> Any:
    """Returns the loaded sequence and the status of its steps. Use it to determine whether a sequence is loaded, running, completed, failed, or waiting. Read-only.

    detail:
      "summary" (default) - compact per-step view: `target_name`, `plan_id` / `pointing_index` (attribution), `frame_type`, `status`, running `phase`, `exposure` (seconds, gain, offset, binning, filter, `completed`/`total` frames — always the FIRST of the step's groups), `exposure_groups` (same shape, one entry per exposure group, since a batched step owes its frames across several), `horizon` (`current_altitude`, `min_altitude`, `expected_time`; null if unavailable), and `fired_events` (SendEvent messages whose node is FINISHED). `issues` lists errors on FAILED nodes only (per step and overall); the top-level `preflight_issues` lists validation notes such as "Focuser not connected" that do not mean failure. The top-level `parse_error` is true only when a non-empty payload carried no `Target Sequence_Container` — an area name this projection does not recognise — which is reported in `issues` rather than raised, and is distinct from an empty sequencer (`step_count: 0` with `parse_error: false`).
      "raw" - full unfiltered /sequence/state payload (per-node `Issues`, expanded Smart Exposure children, no `$type`). Its node-level `Iterations` is the completed count; the loop total is in `Conditions[].Iterations`.
    """
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
