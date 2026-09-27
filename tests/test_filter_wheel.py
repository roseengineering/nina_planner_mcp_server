import unittest

from nina_planner.models.filter_wheel import FilterWheelDevice


class FilterWheelDeviceDeriveFieldsTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestFilterWheel",
            "description": "test filter wheel",
        }
        data.update(overrides)
        return data

    def test_filter_wheel_position_from_selected_filter_dict(self):
        """`selected_filter={"id": 5}` → position=5."""
        fw = FilterWheelDevice(**self._base(selected_filter={"id": 5}))
        self.assertEqual(fw.position, 5)

    def test_filter_wheel_position_from_selected_filter_dict_zero(self):
        """Position 0 is a valid filter position (slot 0)."""
        fw = FilterWheelDevice(**self._base(selected_filter={"id": 0}))
        self.assertEqual(fw.position, 0)

    def test_filter_wheel_selected_filter_not_dict_skips(self):
        """`selected_filter="L"` (string) → position stays None.
        Only dict-valued selected_filter is unpacked.
        """
        fw = FilterWheelDevice(**self._base(selected_filter="L"))
        self.assertIsNone(fw.position)

    def test_filter_wheel_selected_filter_int_skips(self):
        """`selected_filter=2` (int) → position stays None.
        Only dict-valued selected_filter is unpacked.
        """
        fw = FilterWheelDevice(**self._base(selected_filter=2))
        self.assertIsNone(fw.position)

    def test_filter_wheel_no_selected_filter_key(self):
        """Missing selected_filter → position stays None."""
        fw = FilterWheelDevice(**self._base())
        self.assertIsNone(fw.position)

    def test_filter_wheel_selected_filter_dict_without_id(self):
        """selected_filter dict without 'id' key → position=None (id missing)."""
        fw = FilterWheelDevice(**self._base(selected_filter={"name": "L"}))
        self.assertIsNone(fw.position)

    def test_filter_wheel_selected_filter_dict_id_none(self):
        """selected_filter dict with id=None → position=None."""
        fw = FilterWheelDevice(**self._base(selected_filter={"id": None}))
        self.assertIsNone(fw.position)


if __name__ == "__main__":
    unittest.main()
