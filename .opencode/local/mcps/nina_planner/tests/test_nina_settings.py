"""Tests for the get_nina_settings MCP tool."""

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from nina_planner.server import get_nina_settings

_NAMES = (
    "NINA_ARCHIVE_DEST",
    "NINA_UPLOAD_CMD",
    "NINA_VERIFY_CMD",
    "NINA_ENDPOINT",
    "NINA_DRIVE_MOUNT",
)
_ARCHIVE_TRIO = _NAMES[:3]


def _call(env: dict[str, str]) -> dict:
    cleaned = {k: v for k, v in os.environ.items() if k not in _NAMES}
    with patch.dict(os.environ, {**cleaned, **env}, clear=True):
        return asyncio.run(get_nina_settings())


class NinaSettingsTest(unittest.TestCase):
    def test_all_unset_reports_everything_missing(self):
        result = _call({})
        self.assertEqual(result["settings"], {})
        self.assertEqual(result["missing"], list(_NAMES))
        self.assertFalse(result["ready"])

    def test_archive_trio_set_is_ready_even_without_endpoint_and_mount(self):
        # NINA_ENDPOINT/NINA_DRIVE_MOUNT have defaults, so their absence is
        # diagnostic context only — it must never block the archive flow.
        result = _call(
            {
                "NINA_ARCHIVE_DEST": "~/nina-archive",
                "NINA_UPLOAD_CMD": "gcloud storage rsync a b",
                "NINA_VERIFY_CMD": "gcloud storage rsync --dry-run a b",
            }
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["missing"], ["NINA_ENDPOINT", "NINA_DRIVE_MOUNT"])
        self.assertEqual(
            result["settings"]["NINA_ARCHIVE_DEST"],
            str(Path("~/nina-archive").expanduser()),
        )
        self.assertEqual(
            result["settings"]["NINA_UPLOAD_CMD"], "gcloud storage rsync a b"
        )

    def test_all_five_set_reports_nothing_missing(self):
        result = _call(
            {
                "NINA_ARCHIVE_DEST": "/tmp/archive",
                "NINA_UPLOAD_CMD": "upload",
                "NINA_VERIFY_CMD": "verify",
                "NINA_ENDPOINT": "10.0.0.35:1888",
                "NINA_DRIVE_MOUNT": "/Volumes/nina",
            }
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["settings"]["NINA_ENDPOINT"], "10.0.0.35:1888")
        self.assertEqual(result["settings"]["NINA_DRIVE_MOUNT"], "/Volumes/nina")

    def test_blank_verify_command_counts_as_missing(self):
        result = _call(
            {
                "NINA_ARCHIVE_DEST": "/tmp/x",
                "NINA_UPLOAD_CMD": "upload",
                "NINA_VERIFY_CMD": "   ",
            }
        )
        self.assertEqual(
            result["missing"],
            ["NINA_VERIFY_CMD", "NINA_ENDPOINT", "NINA_DRIVE_MOUNT"],
        )
        self.assertFalse(result["ready"])

    def test_blank_drive_mount_counts_as_missing_but_stays_ready(self):
        result = _call(
            {
                "NINA_ARCHIVE_DEST": "/tmp/x",
                "NINA_UPLOAD_CMD": "upload",
                "NINA_VERIFY_CMD": "verify",
                "NINA_ENDPOINT": "10.0.0.35:1888",
                "NINA_DRIVE_MOUNT": "   ",
            }
        )
        self.assertEqual(result["missing"], ["NINA_DRIVE_MOUNT"])
        self.assertTrue(result["ready"])
        self.assertNotIn("NINA_DRIVE_MOUNT", result["settings"])


if __name__ == "__main__":
    unittest.main()
