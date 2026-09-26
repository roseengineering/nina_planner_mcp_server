from __future__ import annotations

import os
import subprocess
from datetime import datetime

NINA_EXE_DEFAULT = (
    r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe"
)

DRIFT_TOLERANCE_SECONDS = 2.0


def nina_exe_path() -> str:
    """Path to NINA.exe on the Windows host, or its ``NINA_EXE_PATH`` override.

    The bash version of the simulation launches NINA via ``cmd.exe /c start``
    using a Windows path, so the value here is a Windows path even when the
    MCP server is running under WSL.
    """
    return os.environ.get("NINA_EXE_PATH") or NINA_EXE_DEFAULT


def nina_running() -> bool:
    """Return True if a ``NINA.exe`` process is visible to ``tasklist.exe``.

    Mirrors ``tasklist.exe /FI "IMAGENAME eq NINA.exe" 2>&1 | grep -q "NINA.exe"``
    from the bash. The tasklist banner uses ``NINA.exe`` (mixed case) regardless
    of the actual ImageName column casing, so we look for that substring.
    """
    result = subprocess.run(
        ["tasklist.exe", "/FI", "IMAGENAME eq NINA.exe"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "NINA.exe" in (result.stdout or "")


def kill_nina_command() -> list[str]:
    """Argv for ``subprocess.run`` to force-kill NINA.exe."""
    return ["taskkill.exe", "/F", "/IM", "NINA.exe"]


def launch_nina_command(nina_exe: str) -> list[str]:
    """Argv for ``subprocess.Popen`` to relaunch NINA detached.

    Equivalent to the bash's
    ``cmd.exe /c start "" "<nina_exe>"`` — the empty string is the
    ``start`` command's window-title placeholder.
    """
    return ["cmd.exe", "/c", "start", "", nina_exe]


def validate_iso_local(when: str) -> datetime:
    """Parse ``when`` as a naive ISO 8601 local datetime.

    Accepts both ``T`` and space separators (e.g. ``"2026-10-15T23:15:00"``
    or ``"2026-10-15 23:15:00"``). Rejects:

    - Empty / whitespace-only strings.
    - Single quotes (would break the PowerShell single-quoted literal that
      wraps ``Set-Date``).
    - Strings carrying explicit timezone info (``Z`` suffix or ``+HH:MM`` /
      ``-HH:MM`` offsets), because ``Set-Date`` operates on local time and a
      timezone-tagged value would silently land wrong. Callers with a UTC
      value should convert to their local zone first.

    Returns the parsed ``datetime``. Raises ``ValueError`` with a useful
    message on any rejection.
    """
    if not when or not when.strip():
        raise ValueError(
            "`when` must be a non-empty ISO 8601 local datetime "
            "(e.g. '2026-10-15T23:15:00' or '2026-10-15 23:15:00')."
        )
    if "'" in when:
        raise ValueError(
            "`when` cannot contain a single quote (PowerShell single-quoted "
            "literal limitation)."
        )
    try:
        parsed = datetime.fromisoformat(when)
    except ValueError as e:
        raise ValueError(
            "`when` must be ISO 8601 local datetime "
            "(e.g. '2026-10-15T23:15:00' or '2026-10-15 23:15:00'); "
            f"got {when!r}: {e}"
        ) from e
    if parsed.tzinfo is not None:
        raise ValueError(
            f"`when` must be naive local time without a timezone suffix; "
            f"got {when!r} (Set-Date operates on local time — convert a UTC "
            f"value to your local zone first)"
        )
    return parsed


def shift_clock_powershell(when: str) -> str:
    """Inner ``Start-Process powershell -Verb RunAs 'Set-Date "<when>"'`` string.

    ``when`` must be a naive ISO 8601 local datetime (validated by
    :func:`validate_iso_local`); PowerShell's ``Set-Date`` accepts ISO 8601
    directly. Embedded single quotes cannot appear because the surrounding
    PowerShell argument literal is single-quoted.
    """
    validate_iso_local(when)
    return (
        f"Start-Process powershell -ArgumentList 'Set-Date \"{when}\"' "
        f"-Verb RunAs"
    )


def restore_clock_powershell() -> str:
    """Inner ``Start-Process powershell -Verb RunAs 'w32tm /resync'`` string."""
    return (
        "Start-Process powershell -ArgumentList 'w32tm /resync' -Verb RunAs"
    )


def time_simulator_enabled() -> bool:
    """Whether the ``simulate_observation_time`` tool is enabled.

    Opt-in via the ``NINA_TIME_SIMULATOR_ENABLED=1`` env var in the MCP server
    environment. Re-read on every call so tests can flip it without
    re-importing the server module.
    """
    return os.environ.get("NINA_TIME_SIMULATOR_ENABLED") == "1"
