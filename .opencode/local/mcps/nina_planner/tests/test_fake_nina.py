"""End-to-end load/start/stop through the real HTTP client against a fake NINA."""

import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import test_server
from fake_nina import FakeNina

from nina_planner.models.camera import CameraDevice
from nina_planner.models.mount import MountDevice
from nina_planner.models.observatory import ObservatoryEquipment
from nina_planner.server import (
    get_sequence_state,
    load_sequence,
    start_sequence,
    stop_sequence,
)


def _equipment(can_park: bool = True, can_find_home: bool = False):
    return ObservatoryEquipment(
        mount=MountDevice(
            connected=True, name="m", can_park=can_park, can_find_home=can_find_home
        ),
        camera=CameraDevice(connected=True, name="cam", can_set_temperature=True),
    )


def _end_area(payload):
    return payload["Items"]["$values"][2]


class FakeNinaTest(unittest.IsolatedAsyncioTestCase):
    def _patched(self, equipment=None):
        # Equipment/profile parsing is a separate layer; only the sequence
        # endpoints are faked over HTTP.
        profile = test_server.McpToolDirectTest("_full_profile")._full_profile()
        stack = ExitStack()
        stack.enter_context(
            patch(
                "nina_planner.tools.equipment._get_site_equipment_status",
                AsyncMock(return_value=equipment or _equipment()),
            )
        )
        stack.enter_context(
            patch(
                "nina_planner.tools.equipment.get_site_profile",
                AsyncMock(return_value=profile),
            )
        )
        return stack

    async def test_stow_loads_over_http_without_starting(self):
        fake = FakeNina()
        with fake.installed(), self._patched():
            await load_sequence([])

        self.assertEqual(fake.posts, ["/sequence/load"])
        self.assertNotIn("/sequence/start", [p for _, p in fake.requests])
        self.assertFalse(fake.running)
        self.assertIsNotNone(fake.loaded)
        self.assertEqual(
            [i["Name"] for i in fake.loaded["Items"]["$values"]],
            ["Start Sequence", "Target Sequence", "End Sequence"],
        )

    async def test_start_runs_the_loaded_sequence(self):
        fake = FakeNina()
        with fake.installed(), self._patched():
            await load_sequence([])
            await start_sequence()
            state = await get_sequence_state(detail="raw")

        self.assertTrue(fake.running)
        self.assertEqual(state["Status"], "RUNNING")
        self.assertIn(
            ("GET", "/sequence/start"),
            fake.requests,
        )

    async def test_start_without_a_loaded_sequence_surfaces_ninas_error(self):
        fake = FakeNina()
        with (
            fake.installed(),
            self.assertRaisesRegex(RuntimeError, "No sequence loaded"),
        ):
            await start_sequence()
        self.assertFalse(fake.running)

    async def test_load_over_a_running_sequence_is_rejected_and_not_replaced(self):
        fake = FakeNina()
        with fake.installed(), self._patched():
            await load_sequence([])
            await start_sequence()
            first = fake.loaded
            with self.assertRaisesRegex(RuntimeError, "Sequence is running"):
                await load_sequence([])

        self.assertIs(fake.loaded, first)
        self.assertTrue(fake.running)

    async def test_stop_then_reload_succeeds(self):
        fake = FakeNina()
        with fake.installed(), self._patched():
            await load_sequence([])
            await start_sequence()
            await stop_sequence()
            self.assertFalse(fake.running)
            await load_sequence([])

        self.assertEqual(fake.posts, ["/sequence/load", "/sequence/load"])

    async def test_unstowable_mount_never_reaches_nina(self):
        fake = FakeNina()
        with (
            fake.installed(),
            self._patched(_equipment(can_park=False, can_find_home=False)),
            self.assertRaisesRegex(ValueError, "nothing to stow"),
        ):
            await load_sequence([])
        self.assertEqual(fake.requests, [])

    async def test_stow_end_area_homes_when_mount_cannot_park(self):
        fake = FakeNina()
        with fake.installed(), self._patched(_equipment(False, True)):
            await load_sequence([])

        end = repr(_end_area(fake.loaded))
        self.assertIn("FindHome", end)
        self.assertNotIn("ParkScope", end)


if __name__ == "__main__":
    unittest.main()
