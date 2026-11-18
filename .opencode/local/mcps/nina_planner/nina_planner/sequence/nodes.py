import os
from typing import Any

from ..models.plan import LightExposurePlan

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
    """Number every node and point each ``Parent`` at the node that holds it.

    Two invariants, both hard-won against N.I.N.A.'s JSON.NET deserializer:

    * Every node keeps its ``Parent`` (Sequencer+ ones included) — it is not
      cosmetic. ``GetArray.Execute`` resolves its result variable through
      ``Symbol.FindSymbol(name, Parent)``, so without it the countdown never
      advances: ``LoopWhile(n > 0)`` stays false, groups are skipped and zero
      exposures are taken. An *unattached* ``SetVariable`` also makes
      ``Validate()`` return true without setting ``Issues``, which N.I.N.A.'s
      start-time ``issues.AddRange(v.Issues)`` then dereferences into an
      ``ArgumentNullException`` that aborts the start in ~20 ms.
    * **Ordering:** a leaf (no ``Items``/``Conditions``) has ``Parent``
      re-inserted AFTER its own properties — JSON.NET applies keys in document
      order, and a Parent-sensitive setter mis-stores its value when Parent
      arrives first (``Symbol.Identifier`` never stored the countdown name,
      which is what emptied every exposure group). Containers keep ``Parent``
      BEFORE ``Items``: their children must attach against a complete upward
      chain.
    """
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
                if "Items" not in data and "Conditions" not in data:
                    # Leaf: own properties first, Parent last (JSON.NET
                    # document order; see the docstring). Containers keep
                    # Parent before Items.
                    data["Parent"] = data.pop("Parent")
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
    exposures: list[LightExposurePlan], batch_size: int = 0
) -> list[tuple[int, float, str]]:
    """Split light frames into interleaved batches: L5/R5 at ``batch_size=2``
    → L2 R2 L2 R2 L1 R1.

    Lights only — the one caller (``batch_size`` decides how many exposures
    of one filter are shot before moving to the next). Darks, bias and flats
    have nothing to batch and read their plan entries directly, one group
    each (see ``target_payload_darks`` / ``target_payload_flats``).
    """
    groups: list[tuple[int, float, str]] = []
    current_count = 0
    while True:
        done = True
        for d in exposures:
            count = min(d.total_count - current_count, batch_size)
            if batch_size == 0 or len(exposures) == 1:
                groups.append((d.total_count, d.exposure_time_seconds, d.filter_name))
            elif count > 0:
                groups.append((count, d.exposure_time_seconds, d.filter_name))
                done = False
        if done:
            break
        current_count += batch_size
    return groups


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
    return _container_base(
        ctype="NINA.Sequencer.Container.StartAreaContainer, NINA.Sequencer",
        name="Start Sequence",
        instructions=instructions,
    )


def container_target(instructions: list[Any] | None = None) -> dict[str, Any]:
    return _container_base(
        ctype="NINA.Sequencer.Container.TargetAreaContainer, NINA.Sequencer",
        name="Target Sequence",
        instructions=instructions,
    )


def container_end(instructions: list[Any] | None = None) -> dict[str, Any]:
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
