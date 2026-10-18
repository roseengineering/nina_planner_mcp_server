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
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{timestamp} {label} {payload}\n")


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
    """Return the envelope's ``Response``, raising with N.I.N.A.'s own reason.

    Both failure shapes are covered: a non-2xx status (whose body would
    otherwise be dropped by ``raise_for_status``) and a 200 whose
    ``Success`` flag is false.
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
    if not isinstance(data, dict) or not data.get("Success", False):
        if isinstance(data, dict) and data.get("Error"):
            raise RuntimeError(str(data["Error"]))
        raise RuntimeError("API request failed")
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
        # Logged like GET's: the caller never sees a POST response (e.g.
        # /sequence/load), so the debug log is its only home.
        log_payload("_api_post:", json.dumps(data, indent=2))
        return cast(dict[str, Any], data.get("Response", {}))


def _sequence_running(state: Any) -> bool:
    """True when any node in a ``/sequence/json`` payload reports ``RUNNING``."""
    if isinstance(state, dict):
        if state.get("Status") == "RUNNING":
            return True
        return any(_sequence_running(value) for value in state.values())
    if isinstance(state, list):
        return any(_sequence_running(item) for item in state)
    return False


async def _sequence_is_running() -> bool:
    return _sequence_running(await api_get("/sequence/json"))


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
