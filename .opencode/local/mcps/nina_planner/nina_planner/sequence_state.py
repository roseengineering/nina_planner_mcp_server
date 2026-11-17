"""Compact projection of N.I.N.A.'s ``/sequence/state``.

Horizon data is merged in from ``/sequence/json``.
"""

import re
from collections.abc import Iterator
from typing import Any

_ATTRIBUTION = re.compile(r"_(plan-[a-f0-9]{12})(?:-(\d+))?_")
_TARGET_AREA = "Target Sequence_Container"


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _find(node: Any, name: str) -> dict[str, Any] | None:
    return next((n for n in _walk(node) if n.get("Name") == name), None)


def _target_area(payload: Any) -> dict[str, Any] | None:
    return _find(payload, _TARGET_AREA)


def _issues(node: Any) -> tuple[list[str], list[str]]:
    """Split a subtree's ``Issues`` into (failed, preflight).

    Non-empty ``Issues`` on a not-yet-run node is pre-flight validation
    (e.g. "Focuser not connected"), not an execution error; only issues on a
    FAILED node are real errors.
    """
    failed: list[str] = []
    preflight: list[str] = []
    for n in _walk(node):
        for issue in n.get("Issues") or []:
            text = f"{n.get('Name')}: {issue}"
            bucket = failed if n.get("Status") == "FAILED" else preflight
            if text not in bucket:
                bucket.append(text)
    return failed, preflight


def _children(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """A container's direct children, in either payload shape.

    ``/sequence/state`` carries ``Items`` as a list; ``/sequence/json`` wraps
    it in ``{"$values": [...]}``.
    """
    items = node.get("Items")
    if isinstance(items, dict):
        items = items.get("$values")
    for item in items or []:
        if isinstance(item, dict):
            yield item


def _counts_exposures(node: dict[str, Any]) -> bool:
    """True for a container whose loop condition counts this step's exposures.

    Only exposure groups carry ``LoopCondition.Iterations`` (or the
    ``CompletedIterations`` spelling), so within a step the structural test is
    unambiguous: the other condition types — ``TimeCondition``,
    ``AboveHorizonCondition``, ``AltitudeCondition``,
    ``SafetyMonitorCondition`` — never match.

    The exposure has to be a DIRECT child, not merely somewhere below. A
    looping wrapper around a group carries ``Iterations`` too and contains a
    ``Take Exposure`` somewhere in it, and ``_walk`` yields the wrapper
    before its children — so an ancestor search let a 2-iteration wrapper
    around a 5-frame group be collected first and report ``total: 2``. See
    ``test_a_looping_wrapper_is_not_mistaken_for_the_group_it_holds``.
    """
    return (
        isinstance(node.get("Conditions"), list)
        and any(
            "Iterations" in c or "CompletedIterations" in c
            for c in node["Conditions"]
            if isinstance(c, dict)
        )
        and any(c.get("Name") == "Take Exposure" for c in _children(node))
    )


def _exposure_groups(step: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node whose loop condition counts this step's exposures, in walk order.

    N.I.N.A. names it after the container — ``Smart Exposure_Container`` for
    the SmartExposure instruction, ``Exposure Group_Container`` for a
    countdown group (``sequence_count_exposure``) — so the name alone only
    covers one of them: the named container is tried first, then *all*
    structural matches are collected rather than the first one, because a
    step built with ``batch_size`` owes its frames across several groups
    (``L×5 + LP×3`` at batch 2 is five groups, not one).
    """
    named = _find(step, "Smart Exposure_Container")
    structural = [node for node in _walk(step) if _counts_exposures(node)]
    if named is None:
        return structural
    return [named] + [node for node in structural if node is not named]


def _exposure(
    step: dict[str, Any], group: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """The exposure view of one group; ``group=None`` reads the step's own.

    The ``take`` fallback below is what keeps ``exposure`` meaningful for a
    step whose group was never expanded (or has none): it is deliberately
    step-wide, so callers wanting a *particular* group must pass it in.
    """
    take = _find(group, "Take Exposure") if group else None
    take = take or _find(step, "Take Exposure")
    if group is None and take is None:
        return None
    # Node-level Iterations means "completed" here but "total" in /sequence/json;
    # the loop condition is the only unambiguous source.
    loop: dict[str, Any] = next(
        (
            c
            for c in (group or {}).get("Conditions") or []
            if "Iterations" in c or "CompletedIterations" in c
        ),
        {},
    )
    flt = (_find(group, "Switch Filter") or {}).get("Filter") if group else None
    binning = (take or {}).get("Binning") or {}
    return {
        "seconds": (take or {}).get("ExposureTime"),
        "gain": (take or {}).get("Gain"),
        "offset": (take or {}).get("Offset"),
        "binning": f"{binning['X']}x{binning['Y']}" if binning else None,
        "filter": flt.get("_name") if isinstance(flt, dict) else None,
        "completed": loop.get("CompletedIterations"),
        "total": loop.get("Iterations", (take or {}).get("ExposureCount")),
    }


def _horizon(json_step: Any) -> dict[str, Any] | None:
    wait = _find(json_step, "Wait for Altitude")
    if wait is None:
        return None
    return {
        "current_altitude": wait.get("CurrentAltitude"),
        "min_altitude": wait.get("Altitude"),
        "expected_time": wait.get("ExpectedTime"),
    }


def project_sequence_state(state: Any, json_state: Any = None) -> dict[str, Any]:
    """Build the ``summary`` view. ``json_state`` (``/sequence/json``) is optional.

    ``parse_error`` is True when a non-empty payload carried no target area:
    that is a *projection* failure (an area name this code does not
    recognise), not an empty sequencer, so it is surfaced in ``issues``
    instead of being raised — a caller reading the summary still gets a dict,
    and one reading ``issues`` sees why the step list is empty.
    """
    area = _target_area(state)
    parse_error = area is None and bool(state)
    issues: list[str] = []
    if parse_error:
        issues.append(f"target area '{_TARGET_AREA}' not found in /sequence/state")
    area = area or {}
    json_steps = (_target_area(json_state) or {}).get("Items") or []
    state_steps = area.get("Items") or []
    # Horizon data is merged from the other endpoint by index, and neither
    # endpoint can pair it any better: /sequence/json steps carry no Target
    # key, so TargetName pairing is impossible, and the step names repeat
    # (seven identical Deep Sky containers), so name-matching cannot
    # disambiguate either. The one thing that does hold when the two agree
    # end to end is the order — so verify the full Name sequence and, on any
    # mismatch, fail safe to a missing horizon rather than a wrong one.
    paired = [s.get("Name") for s in json_steps] == [s.get("Name") for s in state_steps]
    steps: list[dict[str, Any]] = []
    for i, step in enumerate(state_steps):
        name = ((step.get("Target") or {}).get("TargetName")) or None
        match = _ATTRIBUTION.search(name or "")
        take = _find(step, "Take Exposure")
        running = next(
            (
                n.get("Name")
                for n in _walk(step.get("Items"))
                if n.get("Status") == "RUNNING" and "Items" not in n
            ),
            None,
        )
        failed, _ = _issues(step)
        fired = [
            n["Message"]
            for n in _walk(step)
            if n.get("Name") == "Send WebSocket Event"
            and n.get("Status") == "FINISHED"
            and n.get("Message")
        ]
        groups = _exposure_groups(step)
        steps.append(
            {
                "index": i,
                "name": step.get("Name"),
                "target_name": name,
                "plan_id": match.group(1) if match else None,
                "pointing_index": int(match.group(2))
                if match and match.group(2)
                else None,
                "frame_type": (take or {}).get("ImageType"),
                "status": step.get("Status"),
                "phase": running,
                "issues": failed,
                # `exposure` is the FIRST group and must keep meaning exactly
                # what it always has (tests/test_live.py, README and the tool
                # docstring all read it as "the step's exposure"); the full
                # list lives beside it in `exposure_groups`.
                "exposure": _exposure(step, groups[0] if groups else None),
                "exposure_groups": [_exposure(step, group) for group in groups],
                "horizon": _horizon(json_steps[i]) if paired else None,
                "fired_events": fired,
            }
        )
    failed_all, preflight_all = _issues(state)
    issues.extend(failed_all)
    return {
        "running": any(n.get("Status") == "RUNNING" for n in _walk(state)),
        "status": area.get("Status"),
        "step_count": len(steps),
        "completed_steps": sum(s["status"] == "FINISHED" for s in steps),
        "parse_error": parse_error,
        "issues": issues,
        "preflight_issues": preflight_all,
        "steps": steps,
    }
