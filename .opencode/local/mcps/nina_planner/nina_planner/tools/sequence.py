from datetime import UTC, datetime
from typing import Any, cast

from .. import client, config, metadata, plan_utils
from ..app import mcp
from ..models.observatory import ObservatoryEquipment
from ..models.profile import ObservatoryProfile
from ..models.request import SequenceRequest
from ..progress import plan_progress, plan_with_remaining
from ..sequence import (
    build_sequence_many,
    target_payload_darks,
    target_payload_flats,
    target_payload_lights,
)
from . import equipment as equipment_tools


@mcp.tool()
async def load_sequence(
    # The empty list is the API (it means stow) and is never mutated.
    request: list[SequenceRequest] = [],  # noqa: B006
) -> str:
    """Loads one ordered sequence from a list of plan-backed steps; it never starts it. Call `start_sequence()` to run it. An empty request is the stow sequence, and every sequence ends by stowing. Default `remaining` mode skips completed work; use `mode="full"` to repeat the plan. A running sequence must be stopped before loading another. See the README for request options and variants."""
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
            note = f"{sum(f.total_count for f in frames)} frame(s), mode=full"
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
    """Stops the running sequence and waits until NINA reports it has stopped. This does not stow the scope; load and start an empty sequence to stow. Avoid stopping an in-progress stow unless safety requires it."""
    await client.stop_running_sequence_and_wait()
    return "Sequence stopped."


@mcp.tool()
async def start_sequence() -> str:
    """Starts the currently loaded NINA sequence immediately. Use this after load_sequence(), which only loads a sequence and never starts it."""
    await client.api_get(config.SEQUENCE_START_PATH)
    return "Sequence started."


@mcp.tool()
async def get_sequence_state() -> Any:
    """Returns the loaded sequence and its current status (for example, running, completed, failed, or waiting). Read-only."""
    return await client.api_get("/sequence/json")
