import unittest

from pydantic import ValidationError

from nina_planner.models.mount import MountDevice
from nina_planner.models.pointing import PointingInfo


class MountDeviceHoursParsingTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestMount",
            "description": "test mount",
        }
        data.update(overrides)
        return data

    def test_coordinates_ra_in_hours_populates_pointing_ra_hours(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 10.0}))
        self.assertEqual(mount.pointing.ra_hours, 5.5)

    def test_top_level_right_ascension_fallback_when_coordinates_missing(self):
        mount = MountDevice(**self._base(right_ascension=12.0))
        self.assertEqual(mount.pointing.ra_hours, 12.0)

    def test_top_level_right_ascension_fallback_when_coordinates_ra_is_none(self):
        mount = MountDevice(
            **self._base(coordinates={"ra": None, "dec": None}, right_ascension=18.25)
        )
        self.assertEqual(mount.pointing.ra_hours, 18.25)

    def test_ra_hours_boundary_zero_accepted(self):
        mount = MountDevice(**self._base(coordinates={"ra": 0.0, "dec": 0.0}))
        self.assertEqual(mount.pointing.ra_hours, 0.0)

    def test_ra_hours_just_under_24_accepted(self):
        mount = MountDevice(**self._base(coordinates={"ra": 23.9999, "dec": 0.0}))
        self.assertEqual(mount.pointing.ra_hours, 23.9999)

    def test_ra_hours_string_value_coerced(self):
        mount = MountDevice(**self._base(coordinates={"ra": "5.5", "dec": "10.0"}))
        self.assertEqual(mount.pointing.ra_hours, 5.5)

    def test_ra_minus_one_sentinel_is_none(self):
        mount = MountDevice(**self._base(coordinates={"ra": -1, "dec": 10.0}))
        self.assertIsNone(mount.pointing.ra_hours)

    def test_ra_nan_string_sentinel_is_none(self):
        mount = MountDevice(**self._base(coordinates={"ra": "NaN", "dec": 10.0}))
        self.assertIsNone(mount.pointing.ra_hours)

    def test_missing_ra_leaves_ra_hours_none(self):
        mount = MountDevice(**self._base())
        self.assertIsNone(mount.pointing.ra_hours)

    def test_dec_parses_alongside_ra_hours(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 22.5}))
        self.assertEqual(mount.pointing.dec_deg, 22.5)

    def test_disconnected_mount_skips_validator(self):
        mount = MountDevice(**self._base(connected=False, right_ascension=12.0))
        self.assertIsNone(mount.pointing.ra_hours)


class MountDeviceRaHoursValidationTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestMount",
            "description": "test mount",
        }
        data.update(overrides)
        return data

    def test_ra_hours_180_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(coordinates={"ra": 180.0, "dec": 10.0}))

    def test_ra_hours_just_over_24_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(coordinates={"ra": 24.001, "dec": 10.0}))

    def test_ra_hours_exactly_24_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(coordinates={"ra": 24.0, "dec": 10.0}))

    def test_ra_hours_negative_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(coordinates={"ra": -0.001, "dec": 10.0}))

    def test_top_level_right_ascension_180_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(right_ascension=180.0, declination=10.0))

    def test_validation_error_message_mentions_ra_hours(self):
        with self.assertRaises(ValidationError) as ctx:
            MountDevice(**self._base(coordinates={"ra": 180.0, "dec": 10.0}))
        self.assertIn("ra_hours", str(ctx.exception))

    def test_pointing_info_direct_construction_180_raises(self):
        with self.assertRaises(ValidationError):
            PointingInfo(ra_hours=180.0)

    def test_pointing_info_none_ra_hours_is_allowed(self):
        info = PointingInfo()
        self.assertIsNone(info.ra_hours)


class MountDeviceDecParsingTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestMount",
            "description": "test mount",
        }
        data.update(overrides)
        return data

    def test_coordinates_dec_populates_pointing_dec_deg(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 22.5}))
        self.assertEqual(mount.pointing.dec_deg, 22.5)

    def test_top_level_declination_fallback_when_coordinates_missing(self):
        mount = MountDevice(**self._base(declination=45.0))
        self.assertEqual(mount.pointing.dec_deg, 45.0)

    def test_top_level_declination_fallback_when_coordinates_dec_is_none(self):
        mount = MountDevice(
            **self._base(coordinates={"ra": None, "dec": None}, declination=-30.0)
        )
        self.assertEqual(mount.pointing.dec_deg, -30.0)

    def test_dec_boundary_positive_90_accepted(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 90.0}))
        self.assertEqual(mount.pointing.dec_deg, 90.0)

    def test_dec_boundary_negative_90_accepted(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": -90.0}))
        self.assertEqual(mount.pointing.dec_deg, -90.0)

    def test_dec_zero_accepted(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 0.0}))
        self.assertEqual(mount.pointing.dec_deg, 0.0)

    def test_dec_string_value_coerced(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": "22.5"}))
        self.assertEqual(mount.pointing.dec_deg, 22.5)

    def test_dec_minus_one_sentinel_is_none(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": -1}))
        self.assertIsNone(mount.pointing.dec_deg)

    def test_dec_nan_string_sentinel_is_none(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": "NaN"}))
        self.assertIsNone(mount.pointing.dec_deg)

    def test_missing_dec_leaves_dec_deg_none(self):
        mount = MountDevice(**self._base())
        self.assertIsNone(mount.pointing.dec_deg)

    def test_disconnected_mount_skips_validator_for_dec(self):
        mount = MountDevice(**self._base(connected=False, declination=45.0))
        self.assertIsNone(mount.pointing.dec_deg)


class MountDeviceDecValidationTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestMount",
            "description": "test mount",
        }
        data.update(overrides)
        return data

    def test_dec_just_over_90_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 90.001}))

    def test_dec_exactly_90_accepted(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 90.0}))
        self.assertEqual(mount.pointing.dec_deg, 90.0)

    def test_dec_exactly_minus_90_accepted(self):
        mount = MountDevice(**self._base(coordinates={"ra": 5.5, "dec": -90.0}))
        self.assertEqual(mount.pointing.dec_deg, -90.0)

    def test_dec_just_under_minus_90_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(coordinates={"ra": 5.5, "dec": -90.001}))

    def test_dec_180_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 180.0}))

    def test_top_level_declination_180_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(declination=180.0))

    def test_validation_error_message_mentions_dec_deg(self):
        with self.assertRaises(ValidationError) as ctx:
            MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 180.0}))
        self.assertIn("dec_deg", str(ctx.exception))

    def test_pointing_info_direct_construction_dec_180_raises(self):
        with self.assertRaises(ValidationError):
            PointingInfo(dec_deg=180.0)

    def test_pointing_info_none_dec_deg_is_allowed(self):
        info = PointingInfo()
        self.assertIsNone(info.dec_deg)

    def test_ra_valid_with_dec_invalid_still_raises(self):
        with self.assertRaises(ValidationError):
            MountDevice(**self._base(coordinates={"ra": 5.5, "dec": 91.0}))


if __name__ == "__main__":
    unittest.main()
