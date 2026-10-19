from typing import Any, cast

from ..models.observatory import ObservatoryEquipment
from ..models.plan import ObservationPlan
from ..models.profile import ObservatoryProfile
from .builders import (
    _sequence_with_steps,
    _validated_pointing,
    sequence_calibration,
    sequence_cool_camera,
    sequence_deepsky,
    sequence_park_scope,
    sequence_safetynet,
    sequence_unpark_scope,
    sequence_wait_until_sunset,
)
from .instructions import (
    close_cover,
    end_instruction,
    loop_until_dawn,
    loop_until_meridian,
    loop_until_sun_altitude_above,
    loop_until_sun_altitude_below,
    loop_while_above_altitude,
    loop_while_above_horizon,
    open_cover,
    run_autofocus,
    set_tracking,
    sky_flats,
    slew_and_center,
    slew_center_and_rotate,
    slew_to_azalt,
    smart_exposure,
    start_guiding,
    stop_guiding,
    switch_filter,
    wait_if_sun_altitude_above,
    wait_if_sun_altitude_below,
    wait_until_above_altitude,
    wait_until_above_horizon,
    wait_until_dawn,
    wait_until_dusk,
    wait_until_sunset,
)
from .nodes import (
    NINA_FLATS_ALTITUDE,
    NINA_FLATS_AZIMUTH_DAWN,
    NINA_FLATS_AZIMUTH_DUSK,
    _round_robin,
    container_sequential,
    trigger_autofocus_after_exposures,
    trigger_autofocus_after_filter_change,
    trigger_autofocus_after_hfr_increase,
    trigger_autofocus_after_temperature_change,
    trigger_center_after_drift,
    trigger_meridian_flip,
    trigger_restore_guiding,
)

## LISTS
#
# A `request` list composes its steps into one Target area under a single
# End area that always stows (park, then warm) — or, for an empty list, is
# the whole sequence, making the empty list the stow (see
# build_sequence_many). A non-empty run is bracketed by RUN-STARTING in
# the Start area and RUN-FINISHED at the head of the End area, the one
# event that proves every step has run (see _sequence_with_steps).


def build_sequence_many(
    equipment: ObservatoryEquipment, payloads: list[list[dict[str, Any]]]
) -> dict[str, Any]:
    """One sequence built from an ordered list of Target-area payloads.

    A single Start area, a single Target area holding every step's containers
    in the order given (from ``target_payload_*``), and a single End area
    that always stows — park, then warm — so the night ends parked no matter
    what ran, and an empty payload list is the stow itself.

    The run is bracketed by ``RUN-STARTING: N steps`` in the Start area and
    ``RUN-FINISHED: N steps complete`` at the head of the End area; ``N``
    counts the entries that produced containers, since an entry that is
    already complete contributes nothing to run.
    """
    steps = [item for payload in payloads for item in payload]
    step_count = sum(1 for payload in payloads if payload)
    return _sequence_with_steps(equipment, steps, step_count)


## DARKS


def target_payload_darks(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    bias: bool = False,
    variant: str = "wheel",
) -> list[dict[str, Any]]:
    """The Target-area content of a darks/bias step.

    Parked imaging: the container parks (or homes) the mount and sets the
    camera temperature before the exposures, and never unparks — the caller's
    End area decides how the night ends.

    ``variant`` is how the light is cut off: ``wheel`` parks the wheel on the
    profile's ``Dark`` filter slot (the default), ``cover`` closes a
    motorized cover (flip-flat), exposes with the wheel untouched, then
    reopens the cover so a later step cannot silently shoot through it, and
    ``shutter`` exposes with the wheel untouched, relying on the camera's
    shutter.
    """
    if variant not in ("wheel", "cover", "shutter"):
        raise ValueError(
            f"Unsupported dark variant: {variant!r} (use 'wheel', 'cover', or 'shutter')"
        )

    def instructions() -> list[dict[str, Any]]:
        image_type = "BIAS" if bias else "DARK"
        frames = _round_robin(plan.bias_frames if bias else plan.dark_frames)
        base = sequence_park_scope(equipment) + sequence_cool_camera(plan, equipment)
        if variant == "cover":
            return (
                base
                + [close_cover()]
                + [
                    smart_exposure(count=d[0], exposure=d[1], image_type=image_type)
                    for d in frames
                ]
                + [open_cover()]
            )
        if variant == "shutter":
            return base + [
                smart_exposure(count=d[0], exposure=d[1], image_type=image_type)
                for d in frames
            ]
        dark_filter = "Dark"
        return base + [
            smart_exposure(
                count=d[0],
                exposure=d[1],
                filter_name=dark_filter,
                filter_position=profile.filter_position(dark_filter),
                image_type=image_type,
            )
            for d in frames
        ]

    return [
        sequence_calibration(
            plan=plan,
            instructions=instructions(),
            kind="Bias Sequence" if bias else "Dark Sequence",
            tag="bias" if bias else "dark",
        )
    ]


## LIGHTS


def target_payload_lights(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    *,
    pointing_index: int = 1,
) -> list[dict[str, Any]]:
    """The Target-area content of a lights step.

    One deep-sky container carrying its dusk/object/cool safetynets, imaging
    conditions, and triggers. Each safetynet unparks as needed, so a lights
    step follows a parked gap (or parked darks) without extra help.
    """
    pointing = _validated_pointing(plan, pointing_index)
    reference_filter_name = plan.autofocus.reference_filter_name
    reference_filter_position = profile.filter_position(reference_filter_name)
    name_park = "Park Unless in View"

    return [
        sequence_deepsky(
            plan=plan,
            pointing_index=pointing_index,
            instructions=[
                # park if object not in view
                container_sequential(
                    name=name_park,
                    instructions=[
                        # in view: bail out of this container without parking
                        container_sequential(
                            name="Break If in View",
                            conditions=[
                                loop_while_above_horizon(
                                    plan.constraints.horizon_offset_degrees
                                ),
                                loop_while_above_altitude(
                                    plan.constraints.min_altitude
                                ),
                            ],
                            instructions=[end_instruction(name_park)],
                        ),
                    ]
                    # out of view: park (or home) until the safetynets unpark
                    + sequence_park_scope(equipment),
                ),
                # wait for sunset first
                sequence_wait_until_sunset(equipment),
                # wait for dusk before imaging
                sequence_safetynet(
                    name="Wait For Dusk",
                    equipment=equipment,
                    instructions=[
                        wait_until_dusk(),
                    ],
                ),
                # wait for object to be high enough
                sequence_safetynet(
                    name="Wait For Object",
                    equipment=equipment,
                    conditions=[
                        loop_until_dawn(),
                        loop_until_meridian(),
                    ],
                    instructions=[
                        wait_until_above_horizon(
                            plan.constraints.horizon_offset_degrees
                        ),
                        wait_until_above_altitude(plan.constraints.min_altitude),
                    ],
                ),
                # image object if still in view
                sequence_safetynet(
                    name="Image Object",
                    equipment=equipment,
                    conditions=[
                        loop_until_dawn(),
                        loop_while_above_horizon(
                            plan.constraints.horizon_offset_degrees
                        ),
                        loop_while_above_altitude(plan.constraints.min_altitude),
                    ],
                    triggers=[
                        trigger_meridian_flip(),
                        trigger_center_after_drift(
                            after_exposures=plan.guiding.check_drift_every_n_exposures,
                            distance_arcmin=plan.guiding.max_drift_arcmin,
                        ),
                        trigger_autofocus_after_filter_change(),
                        trigger_autofocus_after_exposures(
                            plan.autofocus.every_n_exposures
                        ),
                        trigger_autofocus_after_temperature_change(
                            plan.autofocus.threshold_celsius
                        ),
                        trigger_autofocus_after_hfr_increase(
                            samples=plan.autofocus.hfr_increase_sample_size,
                            amount=plan.autofocus.hfr_increase_threshold_percent,
                        ),
                        trigger_restore_guiding(),
                    ],
                    instructions=(
                        sequence_cool_camera(plan, equipment)
                        + sequence_unpark_scope(equipment)
                    )
                    + [
                        set_tracking(0),
                        switch_filter(reference_filter_name, reference_filter_position),
                    ]
                    + [
                        slew_and_center()
                        if pointing.position_angle_deg is None
                        else slew_center_and_rotate()
                    ]
                    + [
                        run_autofocus(),
                        start_guiding(),
                    ]
                    + [
                        smart_exposure(
                            count=d[0],
                            exposure=d[1],
                            filter_name=d[2],
                            # plan.light_frames' filter_name is required by
                            # the model; _round_robin types it optional
                            # because it also serves darks/bias, which have
                            # none.
                            filter_position=profile.filter_position(cast(str, d[2])),
                            dither=plan.guiding.dither_every_n_exposures,
                            image_type="LIGHT",
                        )
                        for d in _round_robin(plan.light_frames, plan.batch_size)
                    ]
                    + [stop_guiding()],
                ),
            ],
        )
    ]


## FLATS


def target_payload_flats(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    dusk: bool = False,
) -> list[dict[str, Any]]:
    """The Target-area content of a dawn/dusk flats step: slew to the flat
    panel azimuth and take the flats while the sun is on the right side of
    the horizon, or nothing when the window has passed."""

    return [
        sequence_calibration(
            plan=plan,
            kind="Dusk Flats" if dusk else "Dawn Flats",
            tag="dusk flats" if dusk else "dawn flats",
            instructions=[
                sequence_wait_until_sunset(equipment),
                # wait until start time
                sequence_safetynet(
                    name="Wait For Time",
                    equipment=equipment,
                    instructions=[
                        (wait_until_sunset() if dusk else wait_until_dawn()),
                        (
                            wait_if_sun_altitude_above(0)
                            if dusk
                            else wait_if_sun_altitude_below(-8)
                        ),
                    ],
                ),
                # image flats if before stop time
                sequence_safetynet(
                    name="Image Flats",
                    equipment=equipment,
                    conditions=[
                        loop_until_sun_altitude_below(-8)
                        if dusk
                        else loop_until_sun_altitude_above(0)
                    ],
                    instructions=sequence_cool_camera(plan, equipment)
                    + sequence_unpark_scope(equipment)
                    + [
                        slew_to_azalt(
                            az=NINA_FLATS_AZIMUTH_DUSK
                            if dusk
                            else NINA_FLATS_AZIMUTH_DAWN,
                            alt=NINA_FLATS_ALTITUDE,
                        ),
                    ]
                    + [
                        sky_flats(
                            count=d[0],
                            filter_name=d[2],
                            filter_position=profile.filter_position(d[2]),
                        )
                        for d in _round_robin(plan.flat_frames, reverse=dusk)
                        if d[2] is not None
                    ],
                ),
            ],
        )
    ]
