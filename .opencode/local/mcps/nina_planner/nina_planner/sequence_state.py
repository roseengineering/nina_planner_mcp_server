"""Compact projection of N.I.N.A.'s ``/sequence/state`` (plus horizon data from ``/sequence/json``)."""

import re
from typing import Any

_ATTRIBUTION = re.compile(r"_(plan-[a-f0-9]{12})(?:-(\d+))?_")
_TARGET_AREA = "Target Sequence_Container"


def _walk(node: Any):
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


def _exposure(step: dict[str, Any]) -> dict[str, Any] | None:
    smart = _find(step, "Smart Exposure_Container")
    take = _find(step, "Take Exposure")
    if smart is None and take is None:
        return None
    # Node-level Iterations means "completed" here but "total" in /sequence/json;
    # the loop condition is the only unambiguous source.
    loop = next(
        (
            c
            for c in (smart or {}).get("Conditions") or []
            if "Iterations" in c or "CompletedIterations" in c
        ),
        {},
    )
    flt = (_find(smart, "Switch Filter") or {}).get("Filter") if smart else None
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
    """Build the ``summary`` view. ``json_state`` (``/sequence/json``) is optional."""
    area = _target_area(state) or {}
    json_steps = (_target_area(json_state) or {}).get("Items") or []
    steps: list[dict[str, Any]] = []
    for i, step in enumerate(area.get("Items") or []):
        name = ((step.get("Target") or {}).get("TargetName")) or None
        match = _ATTRIBUTION.search(name or "")
        take = _find(step, "Take Exposure")
        running = next(
            (
                n.get("Name")
                for n in _walk(step.get("Items"))
                if n.get("Status") == "RUNNING"
                and "Items" not in n
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
        steps.append(
            {
                "index": i,
                "name": step.get("Name"),
                "target_name": name,
                "plan_id": match.group(1) if match else None,
                "pointing_index": int(match.group(2)) if match and match.group(2) else None,
                "frame_type": (take or {}).get("ImageType"),
                "status": step.get("Status"),
                "phase": running,
                "issues": failed,
                "exposure": _exposure(step),
                "horizon": _horizon(json_steps[i]) if i < len(json_steps) else None,
                "fired_events": fired,
            }
        )
    failed_all, preflight_all = _issues(state)
    return {
        "running": any(n.get("Status") == "RUNNING" for n in _walk(state)),
        "status": area.get("Status"),
        "step_count": len(steps),
        "completed_steps": sum(s["status"] == "FINISHED" for s in steps),
        "issues": failed_all,
        "preflight_issues": preflight_all,
        "steps": steps,
    }
