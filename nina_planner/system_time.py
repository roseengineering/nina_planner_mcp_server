from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NINA_EXE_DEFAULT = (
    r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe"
)

DRIFT_TOLERANCE_SECONDS = 2.0
NINA_TASK_NAME = "NINAPlanner-Launch"


def nina_exe_path() -> str:
    """Path to NINA.exe on the Windows host, or its ``NINA_EXE_PATH`` override.

    The Windows path to NINA.exe even when the MCP server is running under WSL.
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


def local_to_windows(local_path: Path | str, drive_mount: str | None = None) -> str:
    """Convert a local path (e.g. /mnt/c/...) to a Windows path (C:\\...)."""
    if sys.platform == "win32":
        return str(local_path)
    p = Path(local_path).resolve()
    parts = p.parts
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1:
        drive_letter = parts[2].upper()
        return f"{drive_letter}:\\" + "\\".join(parts[3:])
    mount = drive_mount or os.environ.get("NINA_DRIVE_MOUNT")
    if mount and str(p).startswith(mount):
        rel = str(p)[len(mount) :].lstrip("/")
        return "C:\\" + rel.replace("/", "\\")
    return str(p)


def get_launcher_paths() -> tuple[Path, str]:
    """Return (local_path, windows_path) for the NINA launch batch file."""
    custom_dir = os.environ.get("NINA_LAUNCHER_DIR")
    if custom_dir:
        p = Path(custom_dir)
        local_p = p / "launch_nina.cmd"
        return local_p, local_to_windows(local_p)

    user = os.environ.get("USER", "george")
    default_wsl_temp = Path(f"/mnt/c/Users/{user}/AppData/Local/Temp")
    if default_wsl_temp.is_dir():
        local_p = default_wsl_temp / "launch_nina.cmd"
        return local_p, f"C:\\Users\\{user}\\AppData\\Local\\Temp\\launch_nina.cmd"

    local_p = Path.cwd() / "launch_nina.cmd"
    return local_p, local_to_windows(local_p)


def write_nina_launcher(nina_exe: str | None = None) -> tuple[Path, str]:
    """Write a helper .cmd script to launch NINA with GUI, avoiding quoting issues.

    Returns (local_path, windows_path).
    """
    exe = nina_exe or nina_exe_path()
    local_path, win_path = get_launcher_paths()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(f'@echo off\r\nstart "" "{exe}"\r\n', encoding="utf-8")
    return local_path, win_path


def schtasks_create_command(
    launcher_win_path: str, task_name: str = NINA_TASK_NAME
) -> list[str]:
    """Command to create an interactive scheduled task for the current active user session."""
    return [
        "schtasks.exe",
        "/Create",
        "/TN",
        task_name,
        "/TR",
        launcher_win_path,
        "/SC",
        "ONCE",
        "/ST",
        "00:00",
        "/IT",
        "/F",
    ]


def schtasks_run_command(task_name: str = NINA_TASK_NAME) -> list[str]:
    """Command to run the scheduled task."""
    return ["schtasks.exe", "/Run", "/TN", task_name]


def schtasks_delete_command(task_name: str = NINA_TASK_NAME) -> list[str]:
    """Command to delete the scheduled task."""
    return ["schtasks.exe", "/Delete", "/TN", task_name, "/F"]


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
    """PowerShell ``Set-Date -Date '<when>'`` command body.

    ``when`` must be a naive ISO 8601 local datetime (validated by
    :func:`validate_iso_local`); PowerShell's ``Set-Date`` accepts ISO 8601
    directly. Embedded single quotes cannot appear because the surrounding
    PowerShell argument literal is single-quoted.

    Direct (non-elevated) ``Set-Date`` requires the calling Windows token to
    carry ``SeSystemtimePrivilege`` — granted via the "Change the system
    time" user right, or by being an Administrator with that token.
    """
    validate_iso_local(when)
    return f"Set-Date -Date '{when}'"


def restore_set_date_powershell(when: str) -> str:
    """PowerShell ``Set-Date -Date '<when>'`` command body, used to restore
    the host clock to a captured reference (real time) after NINA has
    captured the simulated clock.

    Same quoting rules as :func:`shift_clock_powershell`.
    """
    validate_iso_local(when)
    return f"Set-Date -Date '{when}'"


def read_windows_clock_powershell() -> str:
    """PowerShell command body that emits the Windows host clock as ISO 8601.

    ``Get-Date -Format o`` outputs ``YYYY-MM-DDTHH:MM:SS.fffffff<offset>`` (e.g.
    ``2026-09-26T22:01:12.0000000-05:00``), which :func:`datetime.fromisoformat`
    parses directly.
    """
    return "Get-Date -Format o"
