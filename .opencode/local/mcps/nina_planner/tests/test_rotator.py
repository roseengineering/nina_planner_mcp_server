"""RotatorDevice derived fields and sync-state interpretation."""

import unittest

from nina_planner.models.rotator import RotatorDevice


class RotatorDeviceDeriveFieldsTest(unittest.TestCase):
    """Position mapping and is_synced derivation from a raw payload."""

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
        """The position field maps onto position_deg."""
        rotator = RotatorDevice(**self._base(position=123.4))
        self.assertEqual(rotator.position_deg, 123.4)

    def test_rotator_position_none_preserves_default(self):
        """A null position leaves position_deg None."""
        rotator = RotatorDevice(**self._base(position=None))
        self.assertIsNone(rotator.position_deg)

    def test_rotator_mechanical_position_mapped(self):
        """The mechanical_position field maps onto mechanical_position_deg."""
        rotator = RotatorDevice(**self._base(mechanical_position=200.5))
        self.assertEqual(rotator.mechanical_position_deg, 200.5)

    def test_rotator_mechanical_position_none_preserves_default(self):
        """A null mechanical_position leaves mechanical_position_deg None."""
        rotator = RotatorDevice(**self._base(mechanical_position=None))
        self.assertIsNone(rotator.mechanical_position_deg)

    def test_rotator_is_synced_true_when_state_synced(self):
        """sync_state="Synced" reads as synced."""
        rotator = RotatorDevice(**self._base(sync_state="Synced"))
        self.assertTrue(rotator.is_synced)

    def test_rotator_is_synced_true_when_state_synchronized(self):
        """sync_state="Synchronized" also reads as synced."""
        rotator = RotatorDevice(**self._base(sync_state="Synchronized"))
        self.assertTrue(rotator.is_synced)

    def test_rotator_is_synced_true_when_state_is_true(self):
        """sync_state=True reads as synced."""
        rotator = RotatorDevice(**self._base(sync_state=True))
        self.assertTrue(rotator.is_synced)

    def test_rotator_is_synced_false_when_state_unsynced(self):
        """sync_state="Unsynced" reads as not synced."""
        rotator = RotatorDevice(**self._base(sync_state="Unsynced"))
        self.assertFalse(rotator.is_synced)

    def test_rotator_is_synced_passthrough_when_present(self):
        """A supplied is_synced is kept; the validator never overrides it."""
        rotator = RotatorDevice(**self._base(sync_state="Unsynced", is_synced=True))
        self.assertTrue(rotator.is_synced)

    def test_rotator_is_synced_none_when_no_state_reported(self):
        """No sync_state and no is_synced leaves is_synced None, not False."""
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
        """RotatorInfo's Synced=True maps to is_synced."""
        rotator = RotatorDevice(**self._base(synced=True))
        self.assertTrue(rotator.is_synced)

    def test_rotator_synced_false_maps_to_is_synced(self):
        """RotatorInfo's Synced=False maps to is_synced=False."""
        rotator = RotatorDevice(**self._base(synced=False))
        self.assertFalse(rotator.is_synced)

    def test_rotator_null_synced_falls_back_to_sync_state(self):
        """A null Synced falls back to sync_state for the verdict."""
        rotator = RotatorDevice(**self._base(synced=None, sync_state="Synced"))
        self.assertTrue(rotator.is_synced)

    def test_rotator_synced_takes_precedence_over_sync_state(self):
        """Synced=False wins over a contradictory sync_state."""
        rotator = RotatorDevice(**self._base(synced=False, sync_state="Synced"))
        self.assertFalse(rotator.is_synced)

    def test_rotator_explicit_is_synced_still_wins(self):
        """An explicit is_synced still beats a contradictory Synced."""
        rotator = RotatorDevice(**self._base(synced=False, is_synced=True))
        self.assertTrue(rotator.is_synced)


if __name__ == "__main__":
    unittest.main()
