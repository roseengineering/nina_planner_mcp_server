"""Test-suite configuration and shared helpers.

Pytest auto-discovers this conftest.py and adds its directory to ``sys.path``,
so test modules can simply ``from conftest import requires_windows_interop``.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from nina_planner.system_time import windows_interop_available


def make_metadata_row(
    image_type="LIGHT",
    filter_name="LP",
    duration="60.0",
    target: str | None = "M31 _plan-abc123_",
    exposure_start: str | None = None,
):
    """One ImageMetaData.csv row as ``progress``/``audit`` consume it."""
    if exposure_start is None:
        exposure_start = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {
        "image_type": image_type,
        "filter_name": filter_name,
        "duration": duration,
        # The window logic reads ExposureStartUTC only; the naive local
        # ExposureStart column is deliberately absent so a regression that
        # reads it sees "no timestamp" rather than silently passing.
        "exposure_start_utc": exposure_start,
    }
    if target is not None:
        row["file_path"] = f"C:/NINA/LIGHT/{target}__60.00s_0000.fits"
    return row


requires_windows_interop = unittest.skipUnless(
    windows_interop_available(),
    "requires Windows interop (tasklist.exe/taskkill.exe/schtasks.exe "
    "on PATH); REST-only live tests do not need this.",
)
