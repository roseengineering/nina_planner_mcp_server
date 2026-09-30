import unittest

from nina_planner.models.rotator import RotatorDevice


class RotatorDeviceDeriveFieldsTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestRotator",
            "description": "test rotator",
            "position": 45.0,
            "mechanical_position": 45.0,
        }
        data.update(overrides)
        return data

    def test_rotator_position_mapped_to_position_deg(self):
        rotator = RotatorDevice(**self._base(position=123.4))
        self.assertEqual(rotator.position_deg, 123.4)

    def test_rotator_position_none_preserves_default(self):
        rotator = RotatorDevice(**self._base(position=None))
        self.assertIsNone(rotator.position_deg)

    def test_rotator_mechanical_position_mapped(self):
        rotator = RotatorDevice(**self._base(mechanical_position=200.5))
        self.assertEqual(rotator.mechanical_position_deg, 200.5)

    def test_rotator_mechanical_position_none_preserves_default(self):
        rotator = RotatorDevice(**self._base(mechanical_position=None))
        self.assertIsNone(rotator.mechanical_position_deg)

    def test_rotator_is_synced_true_when_state_synced(self):
        rotator = RotatorDevice(**self._base(sync_state="Synced"))
        self.assertTrue(rotator.is_synced)

    def test_rotator_is_synced_true_when_state_synchronized(self):
        rotator = RotatorDevice(**self._base(sync_state="Synchronized"))
        self.assertTrue(rotator.is_synced)

    def test_rotator_is_synced_true_when_state_is_true(self):
        rotator = RotatorDevice(**self._base(sync_state=True))
        self.assertTrue(rotator.is_synced)

    def test_rotator_is_synced_false_when_state_unsynced(self):
        rotator = RotatorDevice(**self._base(sync_state="Unsynced"))
        self.assertFalse(rotator.is_synced)

    def test_rotator_is_synced_passthrough_when_present(self):
        """If is_synced is already in the input, the validator does NOT
        override it (line 28: `if "is_synced" not in data`)."""
        rotator = RotatorDevice(**self._base(sync_state="Unsynced", is_synced=True))
        self.assertTrue(rotator.is_synced)

    def test_rotator_is_synced_none_when_no_state_reported(self):
        """No sync_state and no is_synced → stays None (unknown), rather than
        being asserted as False."""
        rotator = RotatorDevice(**self._base())
        self.assertIsNone(rotator.is_synced)


class RotatorDeviceNinaSyncedKeyTest(unittest.TestCase):
    """ninaAPI's `RotatorInfo` reports a boolean `Synced` field."""

    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestRotator",
            "description": "test rotator",
            "position": 45.0,
            "mechanical_position": 45.0,
        }
        data.update(overrides)
        return data

    def test_rotator_synced_true_maps_to_is_synced(self):
        rotator = RotatorDevice(**self._base(synced=True))
        self.assertTrue(rotator.is_synced)

    def test_rotator_synced_false_maps_to_is_synced(self):
        rotator = RotatorDevice(**self._base(synced=False))
        self.assertFalse(rotator.is_synced)

    def test_rotator_null_synced_falls_back_to_sync_state(self):
        rotator = RotatorDevice(**self._base(synced=None, sync_state="Synced"))
        self.assertTrue(rotator.is_synced)

    def test_rotator_synced_takes_precedence_over_sync_state(self):
        rotator = RotatorDevice(**self._base(synced=False, sync_state="Synced"))
        self.assertFalse(rotator.is_synced)

    def test_rotator_explicit_is_synced_still_wins(self):
        rotator = RotatorDevice(**self._base(synced=False, is_synced=True))
        self.assertTrue(rotator.is_synced)


if __name__ == "__main__":
    unittest.main()
