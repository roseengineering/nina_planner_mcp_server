from typing import Any

from ..models.observatory import ObservatoryEquipment
from ..models.plan import ObservationPlan
from ..models.pointing import Pointing
from .instructions import (
    connect_all_equipment,
    cool_camera,
    end_instruction,
    home_scope,
    loop_for_iterations,
    loop_until_dusk,
    loop_until_sunrise,
    loop_while_safe,
    park_scope,
    run_finished_event,
    run_starting_event,
    send_event,
    unpark_scope,
    wait_for_timespan,
    wait_until_safe,
    wait_until_sunset,
    warm_camera,
)
from .nodes import (
    container_deepsky,
    container_end,
    container_root,
    container_sequential,
    container_start,
    container_target,
)

####################################


def acquisition_container(
    *,
    name: str,
    target: str,
    ra: float,
    dec: float,
    posang: float,
    tag: str,
    instructions: list[Any],
) -> dict[str, Any]:
    """A deep-sky container bracketed by self-describing acquisition events.

    ``ACQUISITION-STARTING`` / ``ACQUISITION-FINISHED`` bracket one plan
    segment — one ``load_sequence`` entry. Every message carries its kind tag
    as the first token after the colon — ``[light]``, ``[dark]``, ``[bias]``,
    ``[dawn flats]``, ``[dusk flats]`` — so the log says what N.I.N.A. is
    working on right now. ``-N`` in the token after the target marks a mosaic
    pane; a bare token is a single-pointing plan or plan-level calibration.

    These are telemetry: a segment finishing says nothing about whether the
    run has anything left behind it, so they are never a reason to stop a
    sequence. Re-selection belongs to ``run_finished_event``, which fires
    from the End area instead.

    The tag lives only in the event text, never in ``target``: the target
    name is what ``$$TARGETNAME$$`` writes into the file path, so a tagged
    name would bake the frame kind into every folder and file it touches.
    """
    return container_deepsky(
        name=name,
        target=target,
        ra=ra,
        dec=dec,
        posang=posang,
        instructions=[
            send_event(f"ACQUISITION-STARTING: [{tag}] {target}"),
            connect_all_equipment(),
            *instructions,
            send_event(f"ACQUISITION-FINISHED: [{tag}] {target}"),
        ],
    )


def _validated_pointing(plan: ObservationPlan, pointing_index: int) -> Pointing:
    """Resolve a caller-supplied 1-based ``pointing_index`` to its pointing.

    The one place ``plan.pointings`` is indexed by an index we do not
    control: a bare ``plan.pointings[pointing_index - 1]`` surfaces as a
    bare ``IndexError`` (or, on an empty list, an inscrutable one), so both
    ``sequence_deepsky`` and ``target_payload_lights`` go through here and
    get a ``ValueError`` naming the index, the pointing count, and the valid
    range instead. Plan-level calibration never calls this: it does not take
    a ``pointing_index`` at all (see ``sequence_calibration``).
    """
    if pointing_index < 1 or pointing_index > len(plan.pointings):
        raise ValueError(
            f"pointing_index={pointing_index} out of range "
            f"(plan has {len(plan.pointings)} pointings; "
            f"valid 1..{len(plan.pointings)})"
        )
    return plan.pointings[pointing_index - 1]


def sequence_deepsky(
    plan: ObservationPlan, instructions: list, pointing_index: int = 1
) -> dict[str, Any]:
    """Wrap one pointing's light instructions in an acquisition container.

    Tagged ``[light]`` so the event text tells lights from calibration
    without touching the target name — informational only, like every
    ``ACQUISITION-*`` segment event (see ``acquisition_container``).
    """
    pointing = _validated_pointing(plan, pointing_index)
    prefix = f"{plan.target} - {pointing.label}" if pointing.label else plan.target
    target = f"{prefix} {plan.attribution_token(pointing_index)}"
    posang = 0 if pointing.position_angle_deg is None else pointing.position_angle_deg
    return acquisition_container(
        name="Deep Sky Target Sequence",
        target=target,
        ra=pointing.ra_hours,
        dec=pointing.dec_deg,
        posang=posang,
        tag="light",
        instructions=instructions,
    )


def sequence_calibration(
    plan: ObservationPlan, instructions: list, kind: str, tag: str
) -> dict[str, Any]:
    """Wrap calibration instructions (darks/bias/flats) in a container.

    Calibration is plan-level, not per-pointing: this never indexes
    ``plan.pointings``, so an empty pointings list is fine and no pane
    attribution is claimed. The target name carries the bare plan token —
    for a single-pointing plan that is also the lights token, so both share
    one ``$$TARGETNAME$$`` folder; a mosaic's panes keep their own ``-N``
    folders. ``kind`` names the step in the NINA UI ("Dark Sequence", ...)
    and deliberately stays out of the target name, where ``$$TARGETNAME$$``
    would put it in the file path and split the folder again.

    ``tag`` ("dark", "bias", "dawn flats", "dusk flats") is ``kind``'s
    event-only counterpart: it reaches the ACQUISITION messages through
    ``acquisition_container`` and marks the step as plan-level calibration,
    which is what tells the night loop not to re-select on it.

    The coordinates are decorative (the container schema requires them):
    darks park and never slew, and flats slew explicitly to the flat-panel
    azimuth. The plan's primary pointing is shown when it exists, else 0/0.
    """
    target = f"{plan.target} {plan.plan_token()}"
    if plan.pointings:
        primary = plan.pointings[0]
        ra = primary.ra_hours
        dec = primary.dec_deg
        posang = 0 if primary.position_angle_deg is None else primary.position_angle_deg
    else:
        ra, dec, posang = 0.0, 0.0, 0
    return acquisition_container(
        name=kind,
        target=target,
        ra=ra,
        dec=dec,
        posang=posang,
        tag=tag,
        instructions=instructions,
    )


def sequence_warm_camera(equipment: ObservatoryEquipment) -> list[dict[str, Any]]:
    return (
        [warm_camera()]
        if equipment.camera and equipment.camera.thermal.has_cooler
        else []
    )


def sequence_cool_camera(
    plan: ObservationPlan, equipment: ObservatoryEquipment
) -> list[dict[str, Any]]:
    return (
        [cool_camera(plan.cooler.setpoint_celsius) if plan.cooler.on else warm_camera()]
        if equipment.camera and equipment.camera.thermal.has_cooler
        else []
    )


def sequence_park_scope(equipment: ObservatoryEquipment) -> list[dict[str, Any]]:
    return (
        [park_scope()]
        if equipment.mount and equipment.mount.can_park
        else (
            [home_scope()] if equipment.mount and equipment.mount.can_find_home else []
        )
    )


def sequence_unpark_scope(equipment: ObservatoryEquipment) -> list[dict[str, Any]]:
    return [unpark_scope()] if equipment.mount and equipment.mount.can_park else []


def sequence_wait_until_sunset(equipment: ObservatoryEquipment) -> dict[str, Any]:
    name = "Wait For Sunset"
    return container_sequential(
        name=name,
        conditions=[loop_until_dusk()],
        instructions=[
            container_sequential(
                name="On Safe",
                conditions=[loop_while_safe()],
                instructions=[wait_until_sunset(), end_instruction(name)],
            )
        ]
        + sequence_park_scope(equipment)
        + [
            wait_until_safe(),
        ],
    )


def sequence_safetynet(
    name: str,
    equipment: ObservatoryEquipment,
    instructions: list[Any] | None = None,
    conditions: list[Any] | None = None,
    triggers: list[Any] | None = None,
) -> dict[str, Any]:
    if instructions is None:
        instructions = []
    if conditions is None:
        conditions = []
    if triggers is None:
        triggers = []
    return container_sequential(
        name=name,
        triggers=triggers,
        conditions=[loop_until_sunrise()] + conditions,
        instructions=[
            container_sequential(
                name="On Safe",
                conditions=[loop_while_safe()],
                instructions=instructions + [end_instruction(name)],
            )
        ]
        + sequence_park_scope(equipment)
        + [
            wait_until_safe(),
        ],
    )


def sequence_end(
    equipment: ObservatoryEquipment, *, step_count: int = 0
) -> dict[str, Any]:
    # close observatory for the night:
    # wait out one bounded "On Safe" pass, then stow the telescope and warm
    # the camera. The wait is a single 3-minute iteration gated on the safety
    # monitor, so it is skipped entirely when the enclosure is already closed
    # and never idles until sunrise (see test_end_area_waits_three_minutes_*).
    #
    # With steps behind it, the very first instruction announces the finished
    # run: the End area is reached only when the Target area has nothing
    # left, so this fires before the wait and before park — while the night
    # can still be re-planned. An empty request passes step_count=0 and stays
    # silent, so a stow never reads as a finished run.
    delay_minutes = 3
    head = [run_finished_event(step_count)] if step_count else []
    return container_end(
        [
            container_sequential(
                name="Stow Telescope",
                instructions=(
                    head
                    + [
                        container_sequential(
                            name="On Safe",
                            conditions=[loop_while_safe(), loop_for_iterations(1)],
                            instructions=[wait_for_timespan(delay_minutes * 60)],
                        ),
                        connect_all_equipment(),
                    ]
                    + sequence_park_scope(equipment)
                    + sequence_warm_camera(equipment)
                ),
            )
        ]
    )


def _sequence_with_steps(
    equipment: ObservatoryEquipment, steps: list[Any], step_count: int
) -> dict[str, Any]:
    """Assemble Start / Target / End around an ordered run of steps.

    ``step_count`` (> 0) brackets the run: ``RUN-STARTING`` in the Start
    area and ``RUN-FINISHED`` at the head of the End area. Zero — the stow,
    or a request list whose entries all produced no steps — emits neither.
    The Target area holds the steps and nothing else, so its contents stay
    exactly the run's steps.
    """
    return container_root(
        [
            container_start([run_starting_event(step_count)] if step_count else []),
            container_target(steps),
            sequence_end(equipment, step_count=step_count),
        ]
    )
