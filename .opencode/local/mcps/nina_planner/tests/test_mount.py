"""MountDevice and PointingInfo parsing and range validation of RA and dec."""

import unittest

from pydantic import ValidationError

from nina_planner.models.mount import MountDevice
from nina_planner.models.pointing import PointingInfo


def _base(**overrides) -> dict:
    data = {
        "connected": True,
        "name": "TestMount",
        "description": "test mount",
    }
    data.update(overrides)
    return data


class MountDeviceHoursParsingTest(unittest.TestCase):
    """RA hours parsing: coordinates.ra, right_ascension fallback, sentinels."""

    def test_coordinates_ra_in_hours_populates_pointing_ra_hours(self):
        """coordinates.ra lands in pointing.ra_hours unchanged."""
        mount = MountDevice(**_base(coordinates={"ra": 5.5, "dec": 10.0}))
        self.assertEqual(mount.pointing.ra_hours, 5.5)

    def test_top_level_right_ascension_fallback_when_coordinates_missing(self):
        """Top-level right_ascension fills ra_hours when coordinates is absent."""
        mount = MountDevice(**_base(right_ascension=12.0))
        self.assertEqual(mount.pointing.ra_hours, 12.0)

    def test_top_level_right_ascension_fallback_when_coordinates_ra_is_none(self):
        """Top-level right_ascension also wins when coordinates.ra is null."""
        mount = MountDevice(
            **_base(coordinates={"ra": None, "dec": None}, right_ascension=18.25)
        )
        self.assertEqual(mount.pointing.ra_hours, 18.25)

    def test_ra_hours_boundary_zero_accepted(self):
        """RA of exactly 0 is in range."""
        mount = MountDevice(**_base(coordinates={"ra": 0.0, "dec": 0.0}))
        self.assertEqual(mount.pointing.ra_hours, 0.0)

    def test_ra_hours_just_under_24_accepted(self):
        """RA just below 24 stays in range."""
        mount = MountDevice(**_base(coordinates={"ra": 23.9999, "dec": 0.0}))
        self.assertEqual(mount.pointing.ra_hours, 23.9999)

    def test_ra_hours_string_value_coerced(self):
        """String coordinates are coerced to float hours."""
        mount = MountDevice(**_base(coordinates={"ra": "5.5", "dec": "10.0"}))
        self.assertEqual(mount.pointing.ra_hours, 5.5)

    def test_ra_minus_one_caught_by_pydantic_range_check(self):
        """A surviving -1 is still caught by the RA range check."""
        # -1 is preserved by ascom_float now, but RA must be in [0, 24);
        # Pydantic validation rejects it.
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": -1, "dec": 10.0}))

    def test_ra_nan_string_sentinel_is_none(self):
        """A "NaN" string sentinel becomes None, not a number."""
        mount = MountDevice(**_base(coordinates={"ra": "NaN", "dec": 10.0}))
        self.assertIsNone(mount.pointing.ra_hours)

    def test_missing_ra_leaves_ra_hours_none(self):
        """A mount without coordinates keeps ra_hours as None."""
        mount = MountDevice(**_base())
        self.assertIsNone(mount.pointing.ra_hours)

    def test_disconnected_mount_skips_validator(self):
        """A disconnected mount's coordinates are not parsed at all."""
        mount = MountDevice(**_base(connected=False, right_ascension=12.0))
        self.assertIsNone(mount.pointing.ra_hours)


class MountDeviceRaHoursValidationTest(unittest.TestCase):
    """RA outside [0, 24) fails validation from any source; None is allowed."""

    def test_ra_hours_180_raises_validation_error(self):
        """Degrees masquerading as hours (180) are rejected."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": 180.0, "dec": 10.0}))

    def test_ra_hours_just_over_24_raises_validation_error(self):
        """RA just above 24 is out of range."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": 24.001, "dec": 10.0}))

    def test_ra_hours_exactly_24_raises_validation_error(self):
        """RA of exactly 24 is out of range: the range is [0, 24)."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": 24.0, "dec": 10.0}))

    def test_ra_hours_negative_raises_validation_error(self):
        """A negative RA below zero is rejected."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": -0.001, "dec": 10.0}))

    def test_top_level_right_ascension_180_raises_validation_error(self):
        """The top-level right_ascension fallback is range-checked too."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(right_ascension=180.0, declination=10.0))

    def test_validation_error_message_mentions_ra_hours(self):
        """The error names ra_hours so the failing field is identifiable."""
        with self.assertRaises(ValidationError) as ctx:
            MountDevice(**_base(coordinates={"ra": 180.0, "dec": 10.0}))
        self.assertIn("ra_hours", str(ctx.exception))

    def test_pointing_info_direct_construction_180_raises(self):
        """PointingInfo enforces the same RA range on its own."""
        with self.assertRaises(ValidationError):
            PointingInfo(ra_hours=180.0)

    def test_pointing_info_none_ra_hours_is_allowed(self):
        """A missing RA (None) remains legal on PointingInfo."""
        info = PointingInfo()
        self.assertIsNone(info.ra_hours)


class MountDeviceDecParsingTest(unittest.TestCase):
    """Declination parsing: coordinates.dec, declination fallback, sentinels."""

    def test_coordinates_dec_populates_pointing_dec_deg(self):
        """coordinates.dec lands in pointing.dec_deg unchanged."""
        mount = MountDevice(**_base(coordinates={"ra": 5.5, "dec": 22.5}))
        self.assertEqual(mount.pointing.dec_deg, 22.5)

    def test_top_level_declination_fallback_when_coordinates_missing(self):
        """Top-level declination fills dec_deg when coordinates is absent."""
        mount = MountDevice(**_base(declination=45.0))
        self.assertEqual(mount.pointing.dec_deg, 45.0)

    def test_top_level_declination_fallback_when_coordinates_dec_is_none(self):
        """Top-level declination also wins when coordinates.dec is null."""
        mount = MountDevice(
            **_base(coordinates={"ra": None, "dec": None}, declination=-30.0)
        )
        self.assertEqual(mount.pointing.dec_deg, -30.0)

    def test_dec_string_value_coerced(self):
        """A string declination is coerced to a float degrees value."""
        mount = MountDevice(**_base(coordinates={"ra": 5.5, "dec": "22.5"}))
        self.assertEqual(mount.pointing.dec_deg, 22.5)

    def test_dec_minus_one_passes_through(self):
        """Declination -1 is valid and is deliberately not rejected."""
        # -1 is a valid declination; this pins that it is NOT rejected.
        mount = MountDevice(**_base(coordinates={"ra": 5.5, "dec": -1}))
        self.assertEqual(mount.pointing.dec_deg, -1.0)

    def test_dec_nan_string_sentinel_is_none(self):
        """A "NaN" string sentinel becomes None, not a number."""
        mount = MountDevice(**_base(coordinates={"ra": 5.5, "dec": "NaN"}))
        self.assertIsNone(mount.pointing.dec_deg)

    def test_missing_dec_leaves_dec_deg_none(self):
        """A mount without coordinates keeps dec_deg as None."""
        mount = MountDevice(**_base())
        self.assertIsNone(mount.pointing.dec_deg)

    def test_disconnected_mount_skips_validator_for_dec(self):
        """A disconnected mount's declination is not parsed at all."""
        mount = MountDevice(**_base(connected=False, declination=45.0))
        self.assertIsNone(mount.pointing.dec_deg)


class MountDeviceDecValidationTest(unittest.TestCase):
    """Dec within [-90, 90] is enforced on both device and pointing models."""

    def test_dec_just_over_90_raises_validation_error(self):
        """Declination just above 90 is out of range."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": 5.5, "dec": 90.001}))

    def test_dec_exactly_90_accepted(self):
        """Exactly +90 is within range."""
        mount = MountDevice(**_base(coordinates={"ra": 5.5, "dec": 90.0}))
        self.assertEqual(mount.pointing.dec_deg, 90.0)

    def test_dec_exactly_minus_90_accepted(self):
        """Exactly -90 is within range."""
        mount = MountDevice(**_base(coordinates={"ra": 5.5, "dec": -90.0}))
        self.assertEqual(mount.pointing.dec_deg, -90.0)

    def test_dec_just_under_minus_90_raises_validation_error(self):
        """Declination just below -90 is out of range."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": 5.5, "dec": -90.001}))

    def test_dec_180_raises_validation_error(self):
        """Degrees north of the celestial pole (180) are rejected."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": 5.5, "dec": 180.0}))

    def test_top_level_declination_180_raises_validation_error(self):
        """The top-level declination fallback is range-checked too."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(declination=180.0))

    def test_validation_error_message_mentions_dec_deg(self):
        """The error names dec_deg so the failing field is identifiable."""
        with self.assertRaises(ValidationError) as ctx:
            MountDevice(**_base(coordinates={"ra": 5.5, "dec": 180.0}))
        self.assertIn("dec_deg", str(ctx.exception))

    def test_pointing_info_direct_construction_dec_180_raises(self):
        """PointingInfo enforces the same declination range on its own."""
        with self.assertRaises(ValidationError):
            PointingInfo(dec_deg=180.0)

    def test_pointing_info_none_dec_deg_is_allowed(self):
        """A missing declination (None) remains legal on PointingInfo."""
        info = PointingInfo()
        self.assertIsNone(info.dec_deg)

    def test_ra_valid_with_dec_invalid_still_raises(self):
        """A valid RA does not rescue an invalid declination."""
        with self.assertRaises(ValidationError):
            MountDevice(**_base(coordinates={"ra": 5.5, "dec": 91.0}))


class MountDeviceStatusTest(unittest.TestCase):
    """Status priority: slewing beats tracking beats idle."""

    def test_slewing_status_yields_slewing(self):
        """Slewing wins over tracking_enabled=False in the status priority."""
        # slewing=True wins over tracking_enabled=False.
        mount = MountDevice(**_base(slewing=True, tracking_enabled=False))
        self.assertEqual(mount.mount_status, "SLEWING")

    def test_slewing_status_yields_slewing_even_when_tracking(self):
        """Slewing also wins over tracking_enabled=True (priority order)."""
        # slewing=True wins over tracking_enabled=True (priority order).
        mount = MountDevice(**_base(slewing=True, tracking_enabled=True))
        self.assertEqual(mount.mount_status, "SLEWING")

    def test_tracking_enabled_yields_tracking(self):
        """Tracking alone reports TRACKING."""
        mount = MountDevice(**_base(slewing=False, tracking_enabled=True))
        self.assertEqual(mount.mount_status, "TRACKING")

    def test_idle_when_neither_slewing_nor_tracking(self):
        """Neither slewing nor tracking reports IDLE."""
        mount = MountDevice(**_base(slewing=False, tracking_enabled=False))
        self.assertEqual(mount.mount_status, "IDLE")


class MountDeviceMeridianFlipTest(unittest.TestCase):
    """The time_to_meridian_flip normalization: the 24.0 sentinel becomes None."""

    def test_hours_to_flip_exactly_24_normalized_to_none(self):
        """The ASCOM 24.0 sentinel ("no flip pending") normalizes to None."""
        mount = MountDevice(**_base(time_to_meridian_flip=24.0))
        self.assertIsNone(mount.meridian.hours_to_flip)

    def test_hours_to_flip_real_value_preserved(self):
        """A genuine fractional value (e.g. 4.5 hours) is NOT normalized."""
        mount = MountDevice(**_base(time_to_meridian_flip=4.5))
        self.assertEqual(mount.meridian.hours_to_flip, 4.5)

    def test_hours_to_flip_zero_preserved(self):
        """Zero is a real value (flip is imminent) — not normalized."""
        mount = MountDevice(**_base(time_to_meridian_flip=0.0))
        self.assertEqual(mount.meridian.hours_to_flip, 0.0)

    def test_hours_to_flip_missing_yields_none(self):
        """Missing time_to_meridian_flip → ascom_float returns None → None."""
        mount = MountDevice(**_base())
        self.assertIsNone(mount.meridian.hours_to_flip)


if __name__ == "__main__":
    unittest.main()
