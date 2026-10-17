import os
from typing import Any, cast

from .models.observatory import ObservatoryEquipment
from .models.plan import ObservationPlan
from .models.pointing import Pointing
from .models.profile import ObservatoryProfile

NINA_FLATS_ALTITUDE = float(os.environ.get("NINA_FLATS_ALTITUDE", "80"))
NINA_FLATS_AZIMUTH_DAWN = float(os.environ.get("NINA_FLATS_AZIMUTH_DAWN", "270"))
NINA_FLATS_AZIMUTH_DUSK = float(os.environ.get("NINA_FLATS_AZIMUTH_DUSK", "90"))


def _fix_provider(data: dict[str, Any]) -> dict[str, Any]:
    store: dict[str, str] = {}

    def fn(data: dict[str, Any]) -> None:
        for k, d in data.items():
            if isinstance(d, dict):
                if k == "SelectedProvider":
                    ptype = d["$type"]
                    if ptype in store:
                        data[k] = {"$ref": store[ptype]}
                    else:
                        store[ptype] = d["$id"]
                else:
                    fn(d)
            elif isinstance(d, list):
                for item in d:
                    fn(item)

    fn(data)
    return data


def _add_refs(data: dict[str, Any]) -> dict[str, Any]:
    ident = 0

    def fn(data: dict[str, Any], parent: dict[str, Any] | None = None) -> None:
        nonlocal ident
        if "$id" in data:
            ident += 1
            data["$id"] = str(ident)
            if "Parent" in data:
                data["Parent"] = parent
                if "Instructions" not in data:
                    parent = {"$ref": str(ident)}
        for d in data.values():
            if isinstance(d, dict):
                fn(d, parent)
            elif isinstance(d, list):
                for item in d:
                    fn(item, parent)

    fn(data)
    return data


def _child(ctype: str) -> dict[str, Any]:
    return {"$id": None, "$type": ctype, "Parent": None}


def _values(ctype: str, values: list[Any] | None = None) -> dict[str, Any]:
    if values is None:
        values = []
    return {"$id": None, "$type": ctype, "$values": values}


def _to_minutes(deg: float) -> int:
    return int(abs(deg) % 1 * 60)


def _to_seconds(deg: float) -> int:
    return int(round((abs(deg) * 60 % 1) * 60, 4))


def _radec_coordinates(ra: float, dec: float) -> dict[str, Any]:
    negative = dec < 0
    abs_dec = abs(dec)
    return {
        "$id": None,
        "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry",
        "RAHours": int(ra),
        "RAMinutes": _to_minutes(ra),
        "RASeconds": _to_seconds(ra),
        "DecDegrees": int(abs_dec),
        "DecMinutes": _to_minutes(abs_dec),
        "DecSeconds": _to_seconds(abs_dec),
        "NegativeDec": negative,
    }


def _azalt_coordinates(az: float, alt: float) -> dict[str, Any]:
    return {
        "$id": None,
        "$type": "NINA.Astrometry.InputTopocentricCoordinates, NINA.Astrometry",
        "AzDegrees": int(az),
        "AzMinutes": _to_minutes(az),
        "AzSeconds": _to_seconds(az),
        "AltDegrees": int(alt),
        "AltMinutes": _to_minutes(alt),
        "AltSeconds": _to_seconds(alt),
    }


def _round_robin(
    exposures: list[Any], batch_size: int = 0, reverse: bool = False
) -> list[tuple[int, float, str | None]]:
    current_count = 0
    result = []
    while True:
        done = True
        for d in exposures:
            filter_name = d.filter_name if hasattr(d, "filter_name") else None
            exposure_time_seconds = (
                d.exposure_time_seconds if hasattr(d, "exposure_time_seconds") else 0
            )
            count = min(d.total_count - current_count, batch_size)
            if batch_size == 0 or len(exposures) == 1:
                result.append((d.total_count, exposure_time_seconds, filter_name))
            elif count > 0:
                result.append((count, exposure_time_seconds, filter_name))
                done = False
        if done:
            break
        current_count += batch_size
    return list(reversed(result)) if reverse else result


### containers


def _container_base(
    ctype: str,
    name: str | None = None,
    conditions: list[Any] | None = None,
    triggers: list[Any] | None = None,
    instructions: list[Any] | None = None,
) -> dict[str, Any]:
    if conditions is None:
        conditions = []
    if triggers is None:
        triggers = []
    if instructions is None:
        instructions = []
    strategy = (
        "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
    )
    return _child(ctype) | {
        "Strategy": _child(strategy),
        "Name": name,
        "Conditions": _values(
            "System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.Conditions.ISequenceCondition, NINA.Sequencer]], System.ObjectModel",
            conditions,
        ),
        "Items": _values(
            "System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.SequenceItem.ISequenceItem, NINA.Sequencer]], System.ObjectModel",
            instructions,
        ),
        "Triggers": _values(
            "System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.Trigger.ISequenceTrigger, NINA.Sequencer]], System.ObjectModel",
            triggers,
        ),
    }


def container_root(instructions: list[Any] | None = None) -> dict[str, Any]:
    if instructions is None:
        instructions = []
    return _fix_provider(
        _add_refs(
            _container_base(
                ctype="NINA.Sequencer.Container.SequenceRootContainer, NINA.Sequencer",
                name="Root Sequence",
                instructions=instructions,
            )
        )
    )


def container_start(instructions: list[Any] | None = None) -> dict[str, Any]:
    if instructions is None:
        instructions = []
    return _container_base(
        ctype="NINA.Sequencer.Container.StartAreaContainer, NINA.Sequencer",
        name="Start Sequence",
        instructions=instructions,
    )


def container_target(instructions: list[Any] | None = None) -> dict[str, Any]:
    if instructions is None:
        instructions = []
    return _container_base(
        ctype="NINA.Sequencer.Container.TargetAreaContainer, NINA.Sequencer",
        name="Target Sequence",
        instructions=instructions,
    )


def container_end(instructions: list[Any] | None = None) -> dict[str, Any]:
    if instructions is None:
        instructions = []
    return _container_base(
        ctype="NINA.Sequencer.Container.EndAreaContainer, NINA.Sequencer",
        name="End Sequence",
        instructions=instructions,
    )


def container_sequential(
    name: str | None = None,
    conditions: list[Any] | None = None,
    triggers: list[Any] | None = None,
    instructions: list[Any] | None = None,
) -> dict[str, Any]:
    if conditions is None:
        conditions = []
    if triggers is None:
        triggers = []
    if instructions is None:
        instructions = []
    return _container_base(
        ctype="NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
        name=name,
        conditions=conditions,
        triggers=triggers,
        instructions=instructions,
    )


def container_deepsky(
    name: str,
    target: str,
    ra: float,
    dec: float,
    posang: float | None = None,
    triggers: list[Any] | None = None,
    conditions: list[Any] | None = None,
    instructions: list[Any] | None = None,
) -> dict[str, Any]:
    if conditions is None:
        conditions = []
    if triggers is None:
        triggers = []
    if instructions is None:
        instructions = []
    return _container_base(
        ctype="NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer",
        name=name,
        triggers=triggers,
        conditions=conditions,
        instructions=instructions,
    ) | {
        "ExposureInfoList": _values(
            "NINA.Core.Utility.AsyncObservableCollection`1[[NINA.Sequencer.Utility.ExposureInfo, NINA.Sequencer]], NINA.Core"
        ),
        "Target": _child("NINA.Astrometry.InputTarget, NINA.Astrometry")
        | {
            "TargetName": target,
            "PositionAngle": posang,
            "InputCoordinates": _radec_coordinates(ra, dec),
        },
    }


### triggers


def trigger_meridian_flip() -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.Trigger.MeridianFlip.MeridianFlipTrigger, NINA.Sequencer"
    )


def trigger_autofocus_after_filter_change() -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.Trigger.Autofocus.AutofocusAfterFilterChange, NINA.Sequencer"
    )


def trigger_autofocus_after_temperature_change(degrees: float) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.Trigger.Autofocus.AutofocusAfterTemperatureChangeTrigger, NINA.Sequencer"
    ) | {"Amount": degrees}


def trigger_autofocus_after_exposures(exposures: int) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.Trigger.Autofocus.AutofocusAfterExposures, NINA.Sequencer"
    ) | {"AfterExposures": exposures}


def trigger_autofocus_after_hfr_increase(samples: int, amount: float) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.Trigger.Autofocus.AutofocusAfterHFRIncreaseTrigger, NINA.Sequencer"
    ) | {"SampleSize": samples, "Amount": amount}


def trigger_center_after_drift(
    after_exposures: int, distance_arcmin: float
) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.Trigger.Platesolving.CenterAfterDriftTrigger, NINA.Sequencer"
    ) | {
        "AfterExposures": after_exposures,
        "DistanceArcMinutes": distance_arcmin,
    }


def trigger_restore_guiding() -> dict[str, Any]:
    return _child("NINA.Sequencer.Trigger.Guider.RestoreGuiding, NINA.Sequencer")


### instructions


def connect_all_equipment() -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Connect.ConnectAllEquipment, NINA.Sequencer"
    )


def unpark_scope() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Telescope.UnparkScope, NINA.Sequencer")


def park_scope() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Telescope.ParkScope, NINA.Sequencer")


def home_scope() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Telescope.FindHome, NINA.Sequencer")


def warm_camera() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Camera.WarmCamera, NINA.Sequencer") | {
        "Duration": 0,
    }


def cool_camera(temperature: float) -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Camera.CoolCamera, NINA.Sequencer") | {
        "Temperature": temperature,
        "Duration": 0,
    }


def start_guiding(calibration: bool = False) -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Guider.StartGuiding, NINA.Sequencer") | {
        "ForceCalibration": calibration
    }


def stop_guiding() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Guider.StopGuiding, NINA.Sequencer")


def set_tracking(mode: int) -> dict[str, Any]:
    # ascom mode: 0=Sidereal, 1=Lunar, 2=Solar, 3=King
    return _child(
        "NINA.Sequencer.SequenceItem.Telescope.SetTracking, NINA.Sequencer"
    ) | {
        "TrackingMode": mode,
    }


def slew_and_center() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Platesolving.Center, NINA.Sequencer") | {
        "Inherited": True,
    }


def slew_center_and_rotate() -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Platesolving.CenterAndRotate, NINA.Sequencer"
    ) | {
        "Inherited": True,
    }


def slew_to_azalt(az: float, alt: float) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Telescope.SlewScopeToAltAz, NINA.Sequencer"
    ) | {"Coordinates": _azalt_coordinates(az, alt)}


def set_readout(mode: int) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Camera.SetReadoutMode, NINA.Sequencer"
    ) | {
        "Mode": mode,
    }


def run_autofocus() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Autofocus.RunAutofocus, NINA.Sequencer")


def annotation() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Utility.Annotation, NINA.Sequencer") | {
        "Text": None
    }


def switch_filter(
    # Callers with no slot to select don't call this at all — see
    # smart_exposure, which omits the switch instead of passing None.
    filter_name: str | None,
    filter_position: int | None,
) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.FilterWheel.SwitchFilter, NINA.Sequencer"
    ) | {
        "Filter": _child("NINA.Core.Model.Equipment.FilterInfo, NINA.Core")
        | {"_name": filter_name, "_position": filter_position}
    }


def close_cover() -> dict[str, Any]:
    # NINA validates the flat device is connected and can open/close.
    return _child("NINA.Sequencer.SequenceItem.FlatDevice.CloseCover, NINA.Sequencer")


def open_cover() -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.FlatDevice.OpenCover, NINA.Sequencer")


def sky_flats(count: int, filter_name: str, filter_position: int) -> dict[str, Any]:
    return _container_base(
        ctype="NINA.Sequencer.SequenceItem.FlatDevice.SkyFlat, NINA.Sequencer",
        name="Twilight Sky Flats",
        instructions=[
            annotation(),
            annotation(),
            switch_filter(filter_name, filter_position),
            annotation(),
            _container_base(
                ctype="NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
                conditions=[loop_for_iterations(count)],
                instructions=[take_exposure(0.0, "FLAT")],
            ),
        ],
    ) | {
        "MinExposure": 0.0,
        "MaxExposure": 10.0,
        "HistogramTargetPercentage": 0.5,
        "HistogramTolerancePercentage": 0.1,
        "ShouldDither": False,
        "DitherPixels": 5.0,
        "DitherSettleTime": 10.0,
    }


### wait


def wait_until_safe() -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.SafetyMonitor.WaitUntilSafe, NINA.Sequencer"
    )


def wait_until_above_horizon(degrees: float) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Utility.WaitUntilAboveHorizon, NINA.Sequencer"
    ) | {
        "HasDsoParent": True,
        "Data": _child(
            "NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer"
        )
        | {"Offset": degrees, "Comparator": 3},
    }


def wait_until_above_altitude(degrees: float) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Utility.WaitForAltitude, NINA.Sequencer"
    ) | {
        "HasDsoParent": True,
        "AboveOrBelow": ">",
        "Data": _child(
            "NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer"
        )
        | {"Offset": degrees, "Comparator": 3},
    }


def wait_until_time(source: str) -> dict[str, Any]:
    return _child("NINA.Sequencer.SequenceItem.Utility.WaitForTime, NINA.Sequencer") | {
        "MinutesOffset": 0,
        "SelectedProvider": _child(source),
    }


def wait_until_dusk() -> dict[str, Any]:
    return wait_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.DuskProvider, NINA.Sequencer"
    )


def wait_until_dawn() -> dict[str, Any]:
    return wait_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.DawnProvider, NINA.Sequencer"
    )


def wait_until_sunset() -> dict[str, Any]:
    return wait_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.SunsetProvider, NINA.Sequencer"
    )


def wait_if_sun_altitude(degrees: float, comparator: int) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Utility.WaitForSunAltitude, NINA.Sequencer"
    ) | {
        "Data": _child(
            "NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer"
        )
        | {"Offset": degrees, "Comparator": comparator}
    }


def wait_if_sun_altitude_above(degrees: float) -> dict[str, Any]:
    return wait_if_sun_altitude(degrees, 3)


def wait_if_sun_altitude_below(degrees: float) -> dict[str, Any]:
    return wait_if_sun_altitude(degrees, 1)


def wait_for_timespan(seconds: float) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Utility.WaitForTimeSpan, NINA.Sequencer"
    ) | {
        "Time": int(seconds),
    }


### conditions


def loop_until_sun_altitude(degrees: float, comparator: int) -> dict[str, Any]:
    return _child("NINA.Sequencer.Conditions.SunAltitudeCondition, NINA.Sequencer") | {
        "Data": _child(
            "NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer"
        )
        | {"Offset": degrees, "Comparator": comparator}
    }


def loop_until_sun_altitude_above(degrees: float) -> dict[str, Any]:
    return loop_until_sun_altitude(degrees, 3)


def loop_until_sun_altitude_below(degrees: float) -> dict[str, Any]:
    return loop_until_sun_altitude(degrees, 1)


def loop_until_time(source: str) -> dict[str, Any]:
    return _child("NINA.Sequencer.Conditions.TimeCondition, NINA.Sequencer") | {
        "MinutesOffset": 0,
        "SelectedProvider": _child(source),
    }


def loop_until_sunrise() -> dict[str, Any]:
    return loop_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.SunriseProvider, NINA.Sequencer"
    )


def loop_until_dawn() -> dict[str, Any]:
    return loop_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.DawnProvider, NINA.Sequencer"
    )


def loop_until_meridian() -> dict[str, Any]:
    return loop_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.MeridianProvider, NINA.Sequencer"
    )


def loop_while_safe() -> dict[str, Any]:
    return _child("NINA.Sequencer.Conditions.SafetyMonitorCondition, NINA.Sequencer")


def loop_while_above_horizon(degrees: float) -> dict[str, Any]:
    return _child("NINA.Sequencer.Conditions.AboveHorizonCondition, NINA.Sequencer") | {
        "HasDsoParent": True,
        "Data": _child(
            "NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer"
        )
        | {"Offset": degrees, "Comparator": 3},
    }


def loop_while_above_altitude(degrees: float) -> dict[str, Any]:
    return _child("NINA.Sequencer.Conditions.AltitudeCondition, NINA.Sequencer") | {
        "HasDsoParent": True,
        "Data": _child(
            "NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer"
        )
        | {"Offset": degrees, "Comparator": 3},
    }


def loop_for_iterations(count: int) -> dict[str, Any]:
    return _child("NINA.Sequencer.Conditions.LoopCondition, NINA.Sequencer") | {
        "CompletedIterations": 0,
        "Iterations": count,
    }


def dither_after_exposures(exposures: int) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.Trigger.Guider.DitherAfterExposures, NINA.Sequencer"
    ) | {"AfterExposures": exposures}


def take_exposure(exposure: float, image_type: str, count: int = 0) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer"
    ) | {
        "ImageType": image_type,
        "ExposureTime": exposure,
        "Gain": -1,
        "Offset": -1,
        "ExposureCount": count,
        "Binning": _child("NINA.Core.Model.Equipment.BinningMode, NINA.Core")
        | {"X": 1, "Y": 1},
    }


def smart_exposure(
    count: int,
    exposure: float,
    image_type: str,
    filter_name: str | None = None,
    filter_position: int | None = None,
    dither: int | None = None,
) -> dict[str, Any]:
    return _container_base(
        ctype="NINA.Sequencer.SequenceItem.Imaging.SmartExposure, NINA.Sequencer",
        name="Smart Exposure",
        conditions=[loop_for_iterations(count)],
        triggers=[dither_after_exposures(dither)] if dither is not None else [],
        # No filter_name means leave the wheel untouched: covered darks cut
        # the light with the cover, so there is no SwitchFilter at all.
        instructions=(
            [switch_filter(filter_name, filter_position)]
            if filter_name is not None
            else []
        )
        + [take_exposure(exposure, image_type)],
    )


### ninaapi


def send_event(text: str) -> dict[str, Any]:
    return _child("ninaAPI.SequenceItems.SendEventInstruction, ninaAPI") | {
        "Message": text
    }


def _step_word(count: int) -> str:
    return f"{count} step" if count == 1 else f"{count} steps"


def run_starting_event(step_count: int) -> dict[str, Any]:
    """Announce the composed run, before its first step runs.

    Lives in the Start area, so the Target area keeps holding the steps and
    nothing else. Not emitted for an empty run (the stow).
    """
    return send_event(f"RUN-STARTING: {_step_word(step_count)}")


def run_finished_event(step_count: int) -> dict[str, Any]:
    """Announce that the run is exhausted and the End area is about to run.

    Emitted from the head of the End area — the one place N.I.N.A. reaches
    only when the Target area has nothing left, including steps skipped as
    already complete and steps whose window has passed. So unlike a segment's
    ``ACQUISITION-FINISHED``, this one really does mean "no step behind it":
    the scope is idling before park/warm, which is the night loop's
    re-selection point. It fires before the bounded "On Safe" wait and before
    parking, while there is still night left to plan with.

    An empty request (the stow) has no steps and never emits it, so a stow
    can never be mistaken for a finished run.
    """
    return send_event(f"RUN-FINISHED: {_step_word(step_count)} complete")


### plugin


def end_instruction(name: str) -> dict[str, Any]:
    return _child(
        "NINA.Plugin.SequencerPlus.EndInstructionSet, NINA.Plugin.SequencerPlus"
    ) | {"InstructionSetName": name}


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
    name is what ``$$TARGETNAME$$`` writes into the file path, and square
    brackets are mangled there (which is why ``attribution_token`` uses
    parentheses), so a tagged name would split the frame folder.
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


def sequence_root(
    equipment: ObservatoryEquipment, instructions: list[Any] | None = None
) -> dict[str, Any]:
    steps = [] if instructions is None else instructions
    # These callers build exactly one step (one request entry) — or none, for
    # the stow.
    return _sequence_with_steps(equipment, steps, 1 if steps else 0)


## TEARDOWN
#
# The plan-less whole sequence: an empty target area makes the End area run
# straight away, so it completes within one bounded "On Safe" wait instead of
# idling on the enclosure until sunrise (see test_end_area_waits_three_minutes_*).


def build_sequence_stow(equipment: ObservatoryEquipment) -> dict[str, Any]:
    """Close down: park (or home) the mount, then warm the camera.

    The target area is empty, so the End area runs immediately: one bounded
    3-minute "On Safe" pass from ``sequence_end``, then both halves — park,
    then warm. This is what an empty ``request`` list loads. With no steps
    there is no run to bracket, so it emits neither ``RUN-STARTING`` nor
    ``RUN-FINISHED`` — a stow must never read as a finished run.
    """
    return sequence_root(equipment)


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


def build_sequence_darks(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    bias: bool = False,
    variant: str = "wheel",
) -> dict[str, Any]:
    return sequence_root(
        equipment=equipment,
        instructions=target_payload_darks(plan, equipment, profile, bias, variant),
    )


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


def build_sequence_lights(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    *,
    pointing_index: int = 1,
) -> dict[str, Any]:
    return sequence_root(
        equipment=equipment,
        instructions=target_payload_lights(
            plan, equipment, profile, pointing_index=pointing_index
        ),
    )


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


def build_sequence_flats(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    dusk: bool = False,
) -> dict[str, Any]:
    return sequence_root(
        equipment=equipment,
        instructions=target_payload_flats(plan, equipment, profile, dusk=dusk),
    )
