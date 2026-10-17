from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

NINA_EXE_DEFAULT = (
    r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe"
)

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
    drive = windows_temp_path.drive
    if not drive or not windows_temp_path.is_absolute():
        raise RuntimeError(
            f"Windows TEMP is not an absolute drive path: {windows_temp!r}"
        )

    mount = os.environ.get("NINA_DRIVE_MOUNT") or f"/mnt/{drive[0].lower()}"
    local_temp = Path(mount).joinpath(*windows_temp_path.parts[1:])
    launcher_name = "launch_nina.cmd"
    local_p = local_temp / launcher_name
    windows_p = str(windows_temp_path / launcher_name)
    return local_p, windows_p


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
