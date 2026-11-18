import json
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import anyio
import httpx

from . import config

_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _log_file() -> Path | None:
    """Resolve ``config.NINA_PLANNER_LOG`` to a file path usable on this host.

    The sample config points at a Windows path (``C:/Windows/Temp/...``)
    because the observatory host runs Windows. Running the MCP server on
    macOS or WSL would otherwise create a literal ``C:`` directory tree, so
    non-Windows hosts log to the system temp dir under the same file name.
    """
    if not config.NINA_PLANNER_LOG:
        return None
    if os.name != "nt" and _WINDOWS_ABSOLUTE_PATH.match(config.NINA_PLANNER_LOG):
        return Path(tempfile.gettempdir()) / Path(config.NINA_PLANNER_LOG).name
    return Path(config.NINA_PLANNER_LOG)


def log_payload(label: str, payload: str) -> None:
    path = _log_file()
    if path is None:
        return
    timestamp = datetime.now(UTC).isoformat()
    # Best-effort by contract: a debug line must never fail a tool call, so
    # an unwritable log path drops the line and moves on.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{timestamp} {label} {payload}\n")
    except OSError:
        return


def _error_detail(resp: httpx.Response) -> str:
    """N.I.N.A.'s own reason for a failed response, else its raw body.

    ``httpx``'s ``raise_for_status`` reports only the status line — the body,
    where the API explains a refusal, is never surfaced. Truncated so a large
    HTML error page does not swamp the agent-facing message.
    """
    body = resp.text.strip()
    if not body:
        return "an empty response body"
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("Error"):
        return str(payload["Error"])
    return body[:500]


def unwrap_response(method: str, path: str, resp: httpx.Response) -> dict[str, Any]:
    """Return the response envelope itself, raising with N.I.N.A.'s own reason.

    The whole decoded body — ``Success``/``Error``/``Response`` — comes back;
    callers wanting the payload take ``data["Response"]`` themselves. Every
    failure shape carries context: non-2xx status (whose body
    ``raise_for_status`` would drop), non-JSON/non-object body, and a 200
    with ``Success=false`` — reported with N.I.N.A.'s own ``Error`` text when
    it sent one, else with method, URL, status and the body it did send.
    """
    url = f"{config.NINA_API_URL}{path}"
    if not resp.is_success:
        raise RuntimeError(
            f"{method} {url} failed with HTTP {resp.status_code}: {_error_detail(resp)}"
        )
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(
            f"{method} {url} returned a non-JSON body: {_error_detail(resp)}"
        ) from None
    if not isinstance(data, dict):
        raise RuntimeError(
            f"{method} {url} returned HTTP {resp.status_code} with a "
            f"non-object JSON body: {_error_detail(resp)}"
        )
    if not data.get("Success", False):
        if data.get("Error"):
            raise RuntimeError(str(data["Error"]))
        # HTTP 200, Success=false, no Error field: name every detail we have.
        raise RuntimeError(
            f"{method} {url} returned HTTP {resp.status_code} with "
            f"Success=false and no Error field: {_error_detail(resp)}"
        )
    return data


async def api_get(path: str) -> Any:
    async with httpx.AsyncClient(base_url=config.NINA_API_URL, timeout=10.0) as client:
        resp = await client.get(path)
        data = unwrap_response("GET", path, resp)
        log_payload("_api_get:", json.dumps(data, indent=2))
        return data.get("Response", {})


async def api_post(path: str, body: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=config.NINA_API_URL, timeout=10.0) as client:
        resp = await client.post(path, json=body)
        data = unwrap_response("POST", path, resp)
        log_payload("_api_post:", json.dumps(data, indent=2))
        return cast(dict[str, Any], data.get("Response", {}))


def _sequence_running(state: Any) -> bool:
    """True when any node in a ``/sequence/json`` payload reports ``RUNNING``."""
    return "RUNNING" in _node_statuses(state)


async def _sequence_is_running() -> bool:
    return _sequence_running(await api_get(config.SEQUENCE_JSON_PATH))


async def stop_running_sequence_and_wait(timeout: float = 15.0) -> bool:
    """Stop the running sequence (if any) and wait until NINA reports it stopped.

    ``GET /sequence/stop`` acknowledges before the sequencer has finished
    unwinding, and ``POST /sequence/load`` does not retry — a load made too
    soon is simply rejected by NINA. This blocks until every node reports a
    non-running status. Returns True when a stop was needed.
    """
    if not await _sequence_is_running():
        return False
    await api_get("/sequence/stop")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not await _sequence_is_running():
            return True
        await anyio.sleep(0.2)
    raise RuntimeError(f"NINA sequence did not stop within {timeout:.0f}s")


# Budget for wait_until_sequence_started. A module constant read at call time
# (not a default argument) so tests can tighten it without re-binding the
# function's signature.
SEQUENCE_START_TIMEOUT = 10.0


def _node_statuses(state: Any) -> list[str]:
    """Every node ``Status`` string anywhere in a ``/sequence/json`` payload.

    The payload is nested arbitrarily (areas → containers → items), so this
    walks it and collects each ``Status`` value. ``_sequence_running`` is a
    ``"RUNNING" in ...`` filter over this list.
    """
    if isinstance(state, dict):
        statuses = [state["Status"]] if isinstance(state.get("Status"), str) else []
        for key, value in state.items():
            if key != "Status":
                statuses.extend(_node_statuses(value))
        return statuses
    if isinstance(state, list):
        return [status for item in state for status in _node_statuses(item)]
    return []


async def wait_until_sequence_started(timeout: float | None = None) -> list[str]:
    """Block until the sequencer leaves ``CREATED`` — the proof a start took.

    ``GET /sequence/start`` answers as soon as N.I.N.A. *accepts* the start;
    the run can still abort in its first milliseconds (a start-time
    validation throwing) with every node left ``CREATED`` and nothing else
    logged, so the caller would be told "started" about a sequence that
    never ran.

    The criterion is "ANY node left ``CREATED``," deliberately **not**
    "RUNNING": a short park sequence can legitimately finish between the
    start and the first read (N.I.N.A. leaves ``FINISHED``/``SKIPPED``
    nodes behind), so requiring ``RUNNING`` would raise on a sequence that
    had already succeeded.

    Returns the statuses seen. Raises ``RuntimeError`` with a short status
    summary when the budget runs out.
    """
    budget = SEQUENCE_START_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + budget
    while True:
        statuses = _node_statuses(await api_get(config.SEQUENCE_JSON_PATH))
        if any(status != "CREATED" for status in statuses):
            return statuses
        if time.monotonic() >= deadline:
            counts = {s: statuses.count(s) for s in sorted(set(statuses))}
            raise RuntimeError(
                f"NINA sequence did not start within {budget:.0f}s — every "
                f"node is still CREATED ({len(statuses)} node(s) read; "
                f"statuses: {counts or 'none reported'}). The sequence may "
                "have failed its start-time validation: check get_logs() and "
                "get_sequence_state() before retrying."
            )
        await anyio.sleep(0.2)


async def load_sequence_payload(seq: Any) -> None:
    """Load ``seq``, passing NINA's own refusal straight through.

    The sequencer rejects a load made over a running sequence, and its error is
    the one the caller sees — no local pre-check, no retry. A running sequence
    is never stopped on the caller's behalf: call ``stop_sequence`` first.
    """
    await api_post("/sequence/load", seq)


async def api_probe() -> tuple[bool, str | None]:
    """Liveness from ``GET /time``, plus NINA's own reason when it fails.

    Authoritative and host-agnostic: it works whether NINA runs on this host
    or on a remote one reachable through ``NINA_ENDPOINT``. Returning the
    failure text lets callers say *why* the API looks down instead of only
    that it did.
    """
    try:
        await api_get("/time")
        return True, None
    except Exception as e:
        return False, str(e)


async def nina_api_responsive() -> bool:
    """True when NINA's REST API answers ``GET /time`` (see ``api_probe``)."""
    return (await api_probe())[0]


async def run_blocking(argv: list[str], *, timeout: float = 30.0) -> Any:
    """Run a subprocess to completion in a thread; return CompletedProcess."""

    def _run() -> Any:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    return await anyio.to_thread.run_sync(_run)
