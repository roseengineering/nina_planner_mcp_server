from __future__ import annotations

import os
from typing import Any

from .models.observatory import ObservatoryEquipment
from .models.plan import ObservationPlan

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
            filter_name = d.filter_name if "filter_name" in d.model_fields else None
            exposure_time_seconds = (
                d.exposure_time_seconds
                if "exposure_time_seconds" in d.model_fields
                else 0
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
                "NINA.Sequencer.Container.SequenceRootContainer, NINA.Sequencer",
                name="Root Sequence",
                instructions=instructions,
            )
        )
    )


def container_start(instructions: list[Any] | None = None) -> dict[str, Any]:
    if instructions is None:
        instructions = []
    return _container_base(
        "NINA.Sequencer.Container.StartAreaContainer, NINA.Sequencer",
        name="Start Sequence",
        instructions=instructions,
    )


def container_target(instructions: list[Any] | None = None) -> dict[str, Any]:
    if instructions is None:
        instructions = []
    return _container_base(
        "NINA.Sequencer.Container.TargetAreaContainer, NINA.Sequencer",
        name="Target Sequence",
        instructions=instructions,
    )


def container_end(instructions: list[Any] | None = None) -> dict[str, Any]:
    if instructions is None:
        instructions = []
    return _container_base(
        "NINA.Sequencer.Container.EndAreaContainer, NINA.Sequencer",
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
        "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
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
    posang: float = 0,
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
        "NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer",
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


def switch_filter(name: str, position: int) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.FilterWheel.SwitchFilter, NINA.Sequencer"
    ) | {
        "Filter": _child("NINA.Core.Model.Equipment.FilterInfo, NINA.Core")
        | {"_name": name, "_position": position}
    }


def sky_flats(count: int, filter_name: str, position: int) -> dict[str, Any]:
    return _container_base(
        "NINA.Sequencer.SequenceItem.FlatDevice.SkyFlat, NINA.Sequencer",
        name="Twilight Sky Flats",
        instructions=[
            annotation(),
            annotation(),
            switch_filter(filter_name, position),
            annotation(),
            container_sequential(
                "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
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


### wait until


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


### plugin


def loop_while(expression: str) -> dict[str, Any]:
    return _child("WhenPlugin.When.LoopWhile, WhenPlugin") | {
        "PredicateExpr": _child("WhenPlugin.When.Expr, WhenPlugin")
        | {
            "Expression": expression,
            "Type": "Any",
        },
    }


def wait_indefinitely() -> dict[str, Any]:
    return _child("WhenPlugin.When.WaitIndefinitely, WhenPlugin")


def end_instruction(name: str) -> dict[str, Any]:
    return _child("WhenPlugin.When.EndInstructionSet, WhenPlugin") | {
        "InstructionSetName": name
    }


def switch_filter_plus(position: str) -> dict[str, Any]:
    return _child("WhenPlugin.When.SwitchFilter, WhenPlugin") | {
        "FilterExpr": position,
    }


def trigger_dither_after_exposures(exposures: int) -> dict[str, Any]:
    return _child("WhenPlugin.When.DitherAfterExposures, WhenPlugin") | {
        "AfterExpr": _child("WhenPlugin.When.Expr, WhenPlugin")
        | {"Expression": exposures}
    }


def take_exposure(exposure: float, image_type: str) -> dict[str, Any]:
    return _child(
        "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer"
    ) | {
        "ImageType": image_type,
        "ExposureTime": exposure,
        "Gain": -1,
        "Offset": -1,
        "Binning": _child("NINA.Core.Model.Equipment.BinningMode, NINA.Core")
        | {"X": 1, "Y": 1},
    }


def take_exposure_plus(exposure: float, image_type: str) -> dict[str, Any]:
    return _child("WhenPlugin.When.TakeExposure, WhenPlugin") | {
        "ImageType": image_type,
        "ExposureTimeExpr": exposure,
        "GainExpr": None,
        "OffsetExpr": None,
        "Binning": _child("NINA.Core.Model.Equipment.BinningMode, NINA.Core")
        | {"X": 1, "Y": 1},
    }


def smart_exposure_plus(
    count: int,
    exposure: float,
    image_type: str,
    filter_name: str | None = None,
    dither: int | None = None,
) -> dict[str, Any]:
    return _container_base(
        "WhenPlugin.When.SmartExposure, WhenPlugin",
        name="Smart Exposure +",
        conditions=[loop_for_iterations(count)],
        triggers=[trigger_dither_after_exposures(dither)] if dither is not None else [],
        instructions=[
            switch_filter_plus(filter_name)
            if filter_name is not None
            else annotation(),
            take_exposure_plus(exposure=exposure, image_type=image_type),
        ],
    ) | {
        "IterationsExpr": count,
        "FilterExpr": filter_name,
        "DitherExpr": dither,
    }


####################################


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
        conditions=[loop_while("true")] + conditions,
        instructions=[
            container_sequential(
                name="On Safe",
                conditions=[loop_while_safe()],
                instructions=sequence_unpark_scope(equipment)
                + instructions
                + [end_instruction(name)],
            )
        ]
        + sequence_park_scope(equipment)
        + [
            wait_until_safe(),
        ],
    )


def container_end_park_when_unsafe(equipment: ObservatoryEquipment) -> dict[str, Any]:
    return container_end(
        [
            container_sequential(
                name="While Safe",
                conditions=[loop_while_safe()],
                instructions=[wait_indefinitely()],
            ),
        ]
        + sequence_park_scope(equipment)
        + sequence_warm_camera(equipment)
    )


def container_start_unpark_when_safe(equipment: ObservatoryEquipment) -> dict[str, Any]:
    return container_start(
        [sequence_safetynet(name="While Unsafe", equipment=equipment)]
    )


def container_root_standby(
    equipment: ObservatoryEquipment, instructions: list[Any] | None = None
) -> dict[str, Any]:
    return container_root(
        [
            container_start_unpark_when_safe(equipment),
            container_target(instructions),
            container_end_park_when_unsafe(equipment),
        ]
    )


#########################################


def build_sequence_standby(equipment: ObservatoryEquipment) -> dict[str, Any]:
    return container_root_standby(equipment)


def build_sequence_teardown(equipment: ObservatoryEquipment) -> dict[str, Any]:
    return container_root(
        [
            container_start(),
            container_target(),
            container_end(
                sequence_park_scope(equipment) + sequence_warm_camera(equipment)
            ),
        ]
    )


def build_sequence_darks(
    plan: ObservationPlan, equipment: ObservatoryEquipment, bias: bool = False
) -> dict[str, Any]:
    return container_root(
        [
            container_start(sequence_park_scope(equipment)),
            container_target(
                sequence_cool_camera(plan, equipment)
                + [
                    smart_exposure_plus(
                        count=d[0],
                        exposure=d[1],
                        image_type="BIAS" if bias else "DARK",
                    )
                    for d in _round_robin(plan.bias if bias else plan.dark)
                ]
            ),
            container_end_park_when_unsafe(equipment),
        ]
    )


def build_sequence_lights(
    plan: ObservationPlan, equipment: ObservatoryEquipment
) -> dict[str, Any]:
    return container_root_standby(
        equipment=equipment,
        instructions=[
            container_deepsky(
                name="Deep Sky Target Sequence",
                target=f"{plan.target} [{plan.effective_plan_id()}]",
                ra=plan.ra_hours,
                dec=plan.dec_deg,
                instructions=[
                    sequence_safetynet(
                        name="Wait For Dusk",
                        equipment=equipment,
                        instructions=[
                            wait_until_dusk(),
                        ],
                    ),
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
                        instructions=sequence_cool_camera(plan, equipment)
                        + [
                            set_tracking(0),
                            switch_filter_plus(plan.autofocus.reference_filter_name),
                            slew_and_center(),
                            run_autofocus(),
                            start_guiding(),
                        ]
                        + [
                            smart_exposure_plus(
                                count=d[0],
                                exposure=d[1],
                                filter_name=d[2],
                                dither=plan.guiding.dither_every_n_exposures,
                                image_type="LIGHT",
                            )
                            for d in _round_robin(plan.light, plan.batch_size)
                        ]
                        + [stop_guiding()],
                    ),
                ],
            )
        ],
    )


def build_sequence_flats(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: Any,
    dusk: bool = False,
) -> dict[str, Any]:
    return container_root_standby(
        equipment=equipment,
        instructions=[
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
            sequence_safetynet(
                name="Image Flats",
                equipment=equipment,
                conditions=[
                    (
                        loop_until_sun_altitude_below(-8)
                        if dusk
                        else loop_until_sun_altitude_above(0)
                    )
                ],
                instructions=sequence_cool_camera(plan, equipment)
                + [
                    set_tracking(0),
                    slew_to_azalt(
                        az=NINA_FLATS_AZIMUTH_DUSK if dusk else NINA_FLATS_AZIMUTH_DAWN,
                        alt=NINA_FLATS_ALTITUDE,
                    ),
                ]
                + [
                    sky_flats(
                        count=d[0],
                        filter_name=d[2],
                        position=next(
                            f.position for f in profile.filters if f.name == d[2]
                        ),
                    )
                    for d in _round_robin(plan.flat, reverse=dusk)
                    if d[2] is not None
                ],
            ),
        ],
    )
