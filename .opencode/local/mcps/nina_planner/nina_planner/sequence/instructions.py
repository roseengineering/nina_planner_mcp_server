from typing import Any

from .nodes import _azalt_coordinates, _child, _container_base

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
        "Filter": None
        if filter_name is None
        else (
            _child("NINA.Core.Model.Equipment.FilterInfo, NINA.Core")
            | {"_name": filter_name, "_position": filter_position}
        )
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


def loop_until_time(source: str, minutes: float) -> dict[str, Any]:
    return _child("NINA.Sequencer.Conditions.TimeCondition, NINA.Sequencer") | {
        "MinutesOffset": minutes,
        "SelectedProvider": _child(source),
    }


def loop_until_sunset(minutes: float = 0) -> dict[str, Any]:
    return loop_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.SunsetProvider, NINA.Sequencer",
        minutes,
    )


def loop_until_sunrise(minutes: float = 0) -> dict[str, Any]:
    return loop_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.SunriseProvider, NINA.Sequencer",
        minutes,
    )


def loop_until_dawn(minutes: float = 0) -> dict[str, Any]:
    return loop_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.DawnProvider, NINA.Sequencer", minutes
    )


def loop_until_dusk(minutes: float = 0) -> dict[str, Any]:
    return loop_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.DuskProvider, NINA.Sequencer", minutes
    )


def loop_until_meridian(minutes: float = 0) -> dict[str, Any]:
    return loop_until_time(
        "NINA.Sequencer.Utility.DateTimeProvider.MeridianProvider, NINA.Sequencer",
        minutes,
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
    dither: int | None = 0,
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
