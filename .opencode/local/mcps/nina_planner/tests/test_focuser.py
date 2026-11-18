"""FocuserDevice temperature and step-size field derivation."""

import unittest

from nina_planner.models.focuser import FocuserDevice


class FocuserDeviceDeriveFieldsTest(unittest.TestCase):
    """Temperature and step-size normalization for FocuserDevice."""

    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestFocuser",
            "description": "test focuser",
        }
        data.update(overrides)
        return data

    def test_temperature_mapped_to_temperature_c(self):
        """The temperature field maps onto temperature_c."""
        focuser = FocuserDevice(**self._base(temperature=7.5))
        self.assertEqual(focuser.temperature_c, 7.5)

    def test_temperature_string_coerced(self):
        """A string temperature is coerced to a float."""
        focuser = FocuserDevice(**self._base(temperature="7.5"))
        self.assertEqual(focuser.temperature_c, 7.5)

    def test_temperature_missing_stays_none(self):
        """A missing temperature stays None."""
        focuser = FocuserDevice(**self._base())
        self.assertIsNone(focuser.temperature_c)

    def test_temperature_nan_sentinel_stays_none(self):
        """A non-numeric temperature sentinel becomes None."""
        focuser = FocuserDevice(**self._base(temperature="n/a"))
        self.assertIsNone(focuser.temperature_c)

    def test_step_size_mapped_to_step_size_microns(self):
        """The step_size field maps onto step_size_microns."""
        focuser = FocuserDevice(**self._base(step_size=2))
        self.assertEqual(focuser.step_size_microns, 2)

    def test_step_size_float_truncated_to_int(self):
        """A float step size truncates to an int micron value."""
        focuser = FocuserDevice(**self._base(step_size=2.0))
        self.assertEqual(focuser.step_size_microns, 2)

    def test_step_size_missing_stays_none(self):
        """A missing step_size stays None."""
        focuser = FocuserDevice(**self._base())
        self.assertIsNone(focuser.step_size_microns)

    def test_same_named_fields_pass_through(self):
        """Same-named fields pass through the model untouched."""
        focuser = FocuserDevice(
            **self._base(
                position=12345,
                is_moving=False,
                is_settling=True,
                temp_comp=True,
            )
        )
        self.assertEqual(focuser.position, 12345)
        self.assertFalse(focuser.is_moving)
        self.assertTrue(focuser.is_settling)
        self.assertTrue(focuser.temp_comp)

    def test_disconnected_does_not_invoke_validator_normalization(self):
        """A disconnected focuser skips normalization entirely."""
        focuser = FocuserDevice(
            **self._base(connected=False, temperature=7.5, step_size=2)
        )
        self.assertIsNone(focuser.temperature_c)
        self.assertIsNone(focuser.step_size_microns)


if __name__ == "__main__":
    unittest.main()
