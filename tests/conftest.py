"""Test-suite configuration and shared helpers.

Pytest auto-discovers this conftest.py and adds its directory to ``sys.path``,
so test modules can simply ``from conftest import requires_wsl``.
"""

from __future__ import annotations

import os
import sys
import unittest


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


requires_wsl = unittest.skipUnless(
    _is_wsl(),
    "requires WSL host-clock behavior; set WSL_INTEROP or run on WSL.",
)
