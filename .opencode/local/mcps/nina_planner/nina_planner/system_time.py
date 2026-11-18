"""Windows-side helpers for starting, stopping and locating N.I.N.A.

The MCP server may run on macOS, WSL or Windows while N.I.N.A. itself runs
on a Windows host, so these functions build Windows argv (``taskkill``,
``schtasks``) and paths without importing Windows-only modules. Commands
returned here are executed by the caller, never run at import time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

from .imaging import windows_to_local

NINA_EXE_DEFAULT = (
    r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe"
)

NINA_TASK_NAME = "NINAPlanner-Launch"


def nina_exe_path() -> str:
    """Path to NINA.exe on the Windows host, or its ``NINA_EXE_PATH`` override.

    The Windows path to NINA.exe even when the MCP server is running under WSL.
    """
    return os.environ.get("NINA_EXE_PATH") or NINA_EXE_DEFAULT


def kill_nina_command() -> list[str]:
    """Argv for ``subprocess.run`` to force-kill NINA.exe."""
    return ["taskkill.exe", "/F", "/IM", "NINA.exe"]


_WINDOWS_INTEROP_EXES = (
    "tasklist.exe",
    "taskkill.exe",
    "schtasks.exe",
)


def _is_wsl() -> bool:
    """True when running under the Windows Subsystem for Linux."""
    if sys.platform != "linux":
        return False
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        release = open("/proc/sys/kernel/osrelease", encoding="utf-8").read().lower()
    except OSError:
        return False
    return "microsoft" in release or "wsl" in release


def windows_interop_available() -> bool:
    """True when this host can actually drive Windows executables.

    NINA lifecycle control (``start_nina`` / ``stop_nina``) needs ``tasklist.exe``,
    ``taskkill.exe``, and ``schtasks.exe``. On Windows this is trivially true.
    Under WSL it requires interop to be enabled *and* the binaries to be reachable
    on ``PATH``. Everywhere else (e.g. a Mac driving a remote NINA REST endpoint)
    it is false: NINA may be reachable over the network, but this host cannot
    launch or terminate it.
    """
    if sys.platform == "win32":
        return True
    if not _is_wsl():
        return False
    return all(shutil.which(exe) for exe in _WINDOWS_INTEROP_EXES)


def get_launcher_paths() -> tuple[Path, str]:
    """Return local and Windows paths for a launcher visible to Task Scheduler.

    Under WSL, Python's temporary directory is usually ``/tmp``, which Windows
    Task Scheduler cannot use as an action path. Resolve Windows' own ``%TEMP%``
    directory and map it into the WSL-mounted drive instead.
    """
    if sys.platform == "win32":
        local_p = Path(tempfile.gettempdir()) / "launch_nina.cmd"
        return local_p, str(local_p)

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    windows_temp = (result.stdout or "").strip()
    if result.returncode != 0 or not windows_temp:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "Could not resolve the Windows temporary directory for NINA launch"
            + (f": {detail}" if detail else ".")
        )

    windows_temp_path = PureWindowsPath(windows_temp)
    if not windows_temp_path.drive or not windows_temp_path.is_absolute():
        raise RuntimeError(
            f"Windows TEMP is not an absolute drive path: {windows_temp!r}"
        )

    launcher_name = "launch_nina.cmd"
    local_p = windows_to_local(windows_temp) / launcher_name
    windows_p = str(windows_temp_path / launcher_name)
    return local_p, windows_p


def _batch_quoted(value: str, var: str) -> str:
    """Make ``value`` safe inside ``start "" "…"`` in the launcher .cmd file.

    The launcher runs under Task Scheduler: a ``"`` would close the quoting
    (and ``&``/``|``/a newline then run arbitrary commands), and ``%VAR%``
    expands even inside quotes. Quotes and control characters are rejected
    outright (fail closed); ``%`` is escaped by doubling — the batch-file
    spelling for a literal percent. ``&``, ``|``, ``<``, ``>`` and ``^``
    need no escaping once the quotes are guaranteed intact.
    """
    if '"' in value or any(ord(c) < 32 for c in value):
        raise ValueError(
            f"{var} contains a quote or control character and cannot be "
            f"safely written into launch_nina.cmd: {value!r}"
        )
    return value.replace("%", "%%")


def write_nina_launcher(nina_exe: str | None = None) -> tuple[Path, str]:
    """Write a helper .cmd script to launch NINA with GUI, avoiding quoting issues.

    ``nina_exe`` (or ``NINA_EXE_PATH``) is sanitized with
    :func:`_batch_quoted` before it is embedded. Returns
    (local_path, windows_path).
    """
    exe = _batch_quoted(nina_exe or nina_exe_path(), "NINA_EXE_PATH")
    local_path, win_path = get_launcher_paths()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(f'@echo off\r\nstart "" "{exe}"\r\n', encoding="utf-8")
    return local_path, win_path


def schtasks_create_command(
    launcher_win_path: str, task_name: str = NINA_TASK_NAME
) -> list[str]:
    """Create an interactive scheduled task for the current active user session."""
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
