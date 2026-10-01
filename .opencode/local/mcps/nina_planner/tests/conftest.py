"""Test-suite configuration and shared helpers.

Pytest auto-discovers this conftest.py and adds its directory to ``sys.path``,
so test modules can simply ``from conftest import requires_windows_interop``.
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest

_WINDOWS_INTEROP_EXES = (
    "cmd.exe",
    "tasklist.exe",
    "taskkill.exe",
    "schtasks.exe",
    "powershell.exe",
)


def _is_wsl() -> bool:
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

    On Windows this is trivially true. Under WSL it requires interop to be
    enabled *and* the Windows binaries (``cmd.exe``, ``tasklist.exe``,
    ``taskkill.exe``, ``schtasks.exe``, ``powershell.exe``) to be reachable on
    ``PATH``. Tests that launch or kill NINA depend on this; tests that only
    talk to the NINA REST API do not.
    """
    if sys.platform == "win32":
        return True
    if not _is_wsl():
        return False
    return all(shutil.which(exe) for exe in _WINDOWS_INTEROP_EXES)


requires_wsl = unittest.skipUnless(
    _is_wsl(),
    "requires WSL host-clock behavior; set WSL_INTEROP or run on WSL.",
)

requires_windows_interop = unittest.skipUnless(
    windows_interop_available(),
    "requires Windows interop (cmd.exe/tasklist.exe/schtasks.exe/powershell.exe "
    "on PATH); REST-only live tests do not need this.",
)
