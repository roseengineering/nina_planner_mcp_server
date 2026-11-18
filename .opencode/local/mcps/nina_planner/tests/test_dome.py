"""Dome payload field derivation and compute_dome_status precedence."""

import unittest

from nina_planner.models.dome import DomeDevice, compute_dome_status


class DomeDeviceDeriveFieldsTest(unittest.TestCase):
    """Derived dome fields and status strings from raw driver payloads."""

    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestDome",
            "description": "test dome",
            "at_park": False,
            "at_home": False,
            "azimuth": 90.0,
            "shutter_status": "Open",
            "slewing": False,
            "is_following": True,
            "is_synchronized": True,
        }
        data.update(overrides)
        return data

    def test_dome_not_connected_skips_derivation(self):
        """A disconnected dome with a configured driver keeps its defaults."""
        data = {"connected": False, "name": "OfflineDome"}
        dome = DomeDevice(**data)
        self.assertFalse(dome.connected)
        self.assertEqual(dome.name, "OfflineDome")
        self.assertIsNone(dome.is_parked)
        self.assertIsNone(dome.is_homed)
        self.assertIsNone(dome.azimuth_deg)
        self.assertIsNone(dome.shutter_status)
        self.assertIsNone(dome.dome_status)

    def test_dome_connected_derives_is_parked_from_at_park(self):
        """at_park=True on a connected dome derives is_parked."""
        data = self._base(at_park=True)
        dome = DomeDevice(**data)
        self.assertTrue(dome.is_parked)

    def test_dome_connected_derives_is_homed_from_at_home(self):
        """at_home=True on a connected dome derives is_homed."""
        data = self._base(at_home=True)
        dome = DomeDevice(**data)
        self.assertTrue(dome.is_homed)

    def test_dome_azimuth_mapped_to_azimuth_deg(self):
        """Driver azimuth lands on azimuth_deg."""
        data = self._base(azimuth=123.4)
        dome = DomeDevice(**data)
        self.assertEqual(dome.azimuth_deg, 123.4)

    def test_dome_azimuth_none_preserves_default(self):
        """A null azimuth leaves azimuth_deg None instead of 0."""
        data = self._base(azimuth=None)
        dome = DomeDevice(**data)
        self.assertIsNone(dome.azimuth_deg)

    def test_dome_shutter_status_missing_is_none(self):
        """An absent shutter_status stays None, not an invented sentinel."""
        data = self._base()
        del data["shutter_status"]
        dome = DomeDevice(**data)
        self.assertIsNone(dome.shutter_status)

    def test_dome_shutter_status_preserved_when_provided(self):
        """A provided shutter_status is copied through unchanged."""
        data = self._base(shutter_status="Closed")
        dome = DomeDevice(**data)
        self.assertEqual(dome.shutter_status, "Closed")

    def test_dome_compute_dome_status_slewing_wins(self):
        """Slewing wins over following and synchronized in the status."""
        # slewing=True wins over everything else
        status = compute_dome_status(
            slewing=True, is_following=True, is_synchronized=True
        )
        self.assertEqual(status, "SLEWING")
        status = compute_dome_status(
            slewing=True, is_following=False, is_synchronized=False
        )
        self.assertEqual(status, "SLEWING")

    def test_dome_compute_dome_status_idle_when_not_following(self):
        """Not following and not synchronized reads as IDLE."""
        status = compute_dome_status(
            slewing=False, is_following=False, is_synchronized=False
        )
        self.assertEqual(status, "IDLE")

    def test_dome_compute_dome_status_syncing_when_following_not_synced(self):
        """Following but unsynchronized reads as SYNCING."""
        status = compute_dome_status(
            slewing=False, is_following=True, is_synchronized=False
        )
        self.assertEqual(status, "SYNCING")

    def test_dome_compute_dome_status_ready_when_all_set(self):
        """Following and synchronized while idle reads as READY."""
        status = compute_dome_status(
            slewing=False, is_following=True, is_synchronized=True
        )
        self.assertEqual(status, "READY")

    def test_dome_full_integration_connected_path(self):
        """A fully populated connected payload yields a complete DomeDevice."""
        data = self._base(
            at_park=True,
            at_home=False,
            azimuth=270.0,
            shutter_status="Open",
            slewing=False,
            is_following=True,
            is_synchronized=True,
        )
        dome = DomeDevice(**data)
        self.assertTrue(dome.connected)
        self.assertTrue(dome.is_parked)
        self.assertFalse(dome.is_homed)
        self.assertEqual(dome.azimuth_deg, 270.0)
        self.assertEqual(dome.shutter_status, "Open")
        self.assertEqual(dome.dome_status, "READY")


if __name__ == "__main__":
    unittest.main()
