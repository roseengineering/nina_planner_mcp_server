from collections.abc import Awaitable, Callable
from typing import Any

import anyio

from .. import client
from ..app import mcp
from ..progress import append_progress_entry
from ..system_time import (
    kill_nina_command,
    nina_exe_path,
    schtasks_create_command,
    schtasks_delete_command,
    schtasks_run_command,
    windows_interop_available,
    write_nina_launcher,
)


async def _launch_nina_interactive(nina_exe: str) -> None:
    """Launch NINA in the active interactive desktop session via Task Scheduler."""
    _, win_path = write_nina_launcher(nina_exe)
    create_cmd = schtasks_create_command(win_path)
    run_cmd = schtasks_run_command()
    del_cmd = schtasks_delete_command()
    client.log_payload("_launch_nina_interactive: create", " ".join(create_cmd))
    create_result = await client.run_blocking(create_cmd, timeout=15.0)
    _raise_for_command_failure(
        create_result, create_cmd, "Task Scheduler task creation"
    )
    client.log_payload("_launch_nina_interactive: run", " ".join(run_cmd))
    try:
        run_result = await client.run_blocking(run_cmd, timeout=15.0)
        _raise_for_command_failure(run_result, run_cmd, "Task Scheduler task execution")
    finally:
        # Remove the temporary task even when starting it fails.
        try:
            await client.run_blocking(del_cmd, timeout=10.0)
        except Exception as e:
            client.log_payload("_launch_nina_interactive: delete task warning", repr(e))


def _raise_for_command_failure(result: Any, argv: list[str], action: str) -> None:
    """Raise with command output when a completed subprocess exits nonzero."""
    returncode = result.returncode
    if returncode == 0:
        return

    output = "\n".join(
        part.strip()
        for part in (result.stdout or "", result.stderr or "")
        if part.strip()
    )
    detail = f": {output}" if output else ""
    raise RuntimeError(
        f"{action} command {' '.join(argv)!r} failed with exit code "
        f"{returncode}{detail}"
    )


async def _poll_until_async(
    check: Callable[[], Awaitable[bool]], timeout: float, interval: float
) -> bool:
    """Poll ``check`` (async callable returning bool) until it returns True or timeout."""
    deadline = anyio.current_time() + timeout
    while anyio.current_time() < deadline:
        if await check():
            return True
        await anyio.sleep(interval)
    return False


@mcp.tool()
async def stop_nina() -> dict[str, Any]:
    """Stops NINA if it is running locally. No-ops if NINA is down; fails if NINA is remote and this host cannot control it."""
    responsive, probe_error = await client.api_probe()
    if not responsive:
        summary = (
            "stop_nina: NINA is not running (REST API unresponsive) — no action taken."
        )
        if probe_error:
            summary += f" Probe error: {probe_error}"
        client.log_payload("stop_nina:", summary)
        return {"stopped": False, "summary": summary}

    if not windows_interop_available():
        summary = (
            "stop_nina failed: NINA is running on a remote host and Windows "
            "interop is unavailable here, so it cannot be terminated from this "
            "agent. Stop NINA on its host instead."
        )
        client.log_payload("stop_nina error:", summary)
        raise RuntimeError(summary)

    log_extra: list[str] = []
    try:
        proc = await client.run_blocking(kill_nina_command(), timeout=15.0)
    except Exception as e:
        summary = f"stop_nina failed: taskkill error: {e!r}"
        log_extra.append(summary)
        client.log_payload("stop_nina error:", summary)
        raise RuntimeError(summary) from e

    taskkill_detail = f"taskkill exited {proc.returncode}"
    stderr = (proc.stderr or "").strip()
    if stderr:
        taskkill_detail += f": {stderr}"

    # taskkill's exit code is not the end state: a denied kill, or a process
    # that exited on its own between the probe and the kill, both exit
    # nonzero. Whether NINA's REST API still answers is authoritative.
    async def _nina_down() -> bool:
        return not await client.nina_api_responsive()

    down = await _poll_until_async(_nina_down, timeout=10.0, interval=0.5)
    if not down:
        summary = (
            "stop_nina failed: NINA's REST API still answers after taskkill "
            f"({taskkill_detail}); NINA may not have terminated"
        )
        log_extra.append(summary)
        client.log_payload("stop_nina error:", summary)
        raise RuntimeError(summary)

    summary = "stop_nina: NINA.exe terminated successfully."
    if proc.returncode != 0:
        # e.g. "not found" because NINA exited on its own — end state holds.
        summary += f" (note: {taskkill_detail})"
    log_extra.append("killed running NINA")

    client.log_payload("stop_nina:", summary)
    try:
        await append_progress_entry("stop_nina: " + "; ".join(log_extra))
    except Exception as e:
        client.log_payload("stop_nina: progress entry failed", repr(e))

    return {"stopped": True, "summary": summary}


@mcp.tool()
async def start_nina() -> dict[str, Any]:
    """Launches NINA in the interactive desktop session. Fails if NINA is already running or this host cannot control the remote NINA process."""
    # Liveness comes from the REST API so this works against a remote NINA too.
    if await client.nina_api_responsive():
        if windows_interop_available():
            raise RuntimeError("NINA is already running. Call stop_nina() first.")
        raise RuntimeError(
            "NINA is already running. This host has no Windows interop, so it "
            "cannot stop or restart a remote NINA — manage NINA on its own host."
        )

    # API unresponsive => NINA is down. Launching it needs local Windows interop.
    if not windows_interop_available():
        raise RuntimeError(
            "start_nina failed: NINA's REST API is unresponsive and this host "
            "has no Windows interop, so NINA cannot be launched from here. NINA "
            "appears to run on a remote host; start it on that host instead."
        )

    nina_exe = nina_exe_path()
    log_extra = ["launching NINA"]
    await _launch_nina_interactive(nina_exe)
    log_extra.append(f"launched NINA from {nina_exe}")
    summary = (
        "start_nina: launched NINA into interactive desktop session. "
        "Poll get_site_equipment_status() to confirm readiness."
    )
    client.log_payload("start_nina:", summary)
    try:
        await append_progress_entry("start_nina: " + "; ".join(log_extra))
    except Exception as e:
        client.log_payload("start_nina: progress entry failed", repr(e))
    return {"summary": summary}


@mcp.tool()
async def get_nina_status() -> dict[str, Any]:
    """Checks whether NINA's REST API is responsive and whether this host can start or stop it. Never raises when NINA is down."""
    running, probe_error = await client.api_probe()
    if not running and probe_error:
        client.log_payload("get_nina_status: api probe failed", probe_error)

    lifecycle_control_available = windows_interop_available()

    if running:
        summary = "NINA running — REST API responsive"
        if not lifecycle_control_available:
            summary += " (remote; lifecycle control unavailable)"
    else:
        summary = "NINA not running — REST API unresponsive"
        if probe_error:
            summary += f" (probe error: {probe_error})"
        if not lifecycle_control_available:
            summary += " (remote; cannot be restarted from here)"

    return {
        "running": running,
        "lifecycle_control_available": lifecycle_control_available,
        "probe_error": probe_error,
        "summary": summary,
    }
