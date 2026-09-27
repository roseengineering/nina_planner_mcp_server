import unittest
from datetime import datetime

from nina_planner.nina_utils import ascom_datetime, ascom_float, ascom_int


class AscomFloatTest(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(ascom_float(None))

    def test_float_passes_through(self):
        self.assertEqual(ascom_float(1.5), 1.5)

    def test_int_promoted(self):
        self.assertEqual(ascom_float(3), 3.0)

    def test_int_passes_through(self):
        self.assertEqual(ascom_float(-1), -1.0)

    def test_float_nan_is_sentinel(self):
        self.assertIsNone(ascom_float(float("nan")))

    def test_float_minus_one_passes_through(self):
        self.assertEqual(ascom_float(-1.0), -1.0)

    def test_bool_true(self):
        self.assertEqual(ascom_float(True), 1.0)

    def test_bool_false(self):
        self.assertEqual(ascom_float(False), 0.0)

    def test_string_valid(self):
        self.assertEqual(ascom_float("2.5"), 2.5)

    def test_string_nan_is_sentinel(self):
        self.assertIsNone(ascom_float("nan"))

    def test_string_NaN_is_sentinel(self):
        self.assertIsNone(ascom_float("NaN"))

    def test_string_nan_with_whitespace_is_sentinel(self):
        self.assertIsNone(ascom_float(" NaN "))

    def test_string_na_is_sentinel(self):
        self.assertIsNone(ascom_float("na"))

    def test_string_NA_is_sentinel(self):
        self.assertIsNone(ascom_float("NA"))

    def test_string_na_with_whitespace_is_sentinel(self):
        self.assertIsNone(ascom_float(" NA "))

    def test_string_slash_a_is_sentinel(self):
        self.assertIsNone(ascom_float("N/A"))

    def test_string_slash_a_lowercase_is_sentinel(self):
        self.assertIsNone(ascom_float("n/a"))

    def test_string_slash_a_with_whitespace_is_sentinel(self):
        self.assertIsNone(ascom_float(" N/A "))

    def test_string_empty_is_sentinel(self):
        self.assertIsNone(ascom_float(""))

    def test_string_whitespace_only_is_sentinel(self):
        self.assertIsNone(ascom_float("   "))

    def test_string_minus_one_passes_through(self):
        self.assertEqual(ascom_float("-1"), -1.0)

    def test_string_minus_one_with_whitespace_passes_through(self):
        self.assertEqual(ascom_float(" -1 "), -1.0)

    def test_string_real_negative_passes_through(self):
        self.assertEqual(ascom_float("-1.5"), -1.5)


class AscomIntTest(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(ascom_int(None))

    def test_int_passes_through(self):
        self.assertEqual(ascom_int(42), 42)

    def test_float_truncates(self):
        self.assertEqual(ascom_int(3.7), 3)

    def test_int_passes_through_for_minus_one(self):
        self.assertEqual(ascom_int(-1), -1)

    def test_float_minus_one_passes_through(self):
        self.assertEqual(ascom_int(-1.0), -1)

    def test_float_nan_is_sentinel(self):
        self.assertIsNone(ascom_int(float("nan")))

    def test_bool_true(self):
        self.assertEqual(ascom_int(True), 1)

    def test_bool_false(self):
        self.assertEqual(ascom_int(False), 0)

    def test_string_valid(self):
        self.assertEqual(ascom_int("42"), 42)

    def test_string_decimal(self):
        self.assertEqual(ascom_int("42.7"), 42)

    def test_string_nan_is_sentinel(self):
        self.assertIsNone(ascom_int("nan"))

    def test_string_NaN_is_sentinel(self):
        self.assertIsNone(ascom_int("NaN"))

    def test_string_nan_with_whitespace_is_sentinel(self):
        self.assertIsNone(ascom_int(" NaN "))

    def test_string_na_is_sentinel(self):
        self.assertIsNone(ascom_int("na"))

    def test_string_NA_is_sentinel(self):
        self.assertIsNone(ascom_int("NA"))

    def test_string_slash_a_is_sentinel(self):
        self.assertIsNone(ascom_int("N/A"))

    def test_string_slash_a_with_whitespace_is_sentinel(self):
        self.assertIsNone(ascom_int(" N/A "))

    def test_string_empty_is_sentinel(self):
        self.assertIsNone(ascom_int(""))

    def test_string_whitespace_only_is_sentinel(self):
        self.assertIsNone(ascom_int("   "))

    def test_string_minus_one_passes_through(self):
        self.assertEqual(ascom_int("-1"), -1)

    def test_string_minus_one_with_whitespace_passes_through(self):
        self.assertEqual(ascom_int(" -1 "), -1)

    def test_string_real_negative_passes_through(self):
        self.assertEqual(ascom_int("-10"), -10)


class AscomDatetimeTest(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(ascom_datetime(None))

    def test_string_nan_is_sentinel(self):
        self.assertIsNone(ascom_datetime("nan"))

    def test_string_empty_is_sentinel(self):
        self.assertIsNone(ascom_datetime(""))

    def test_string_datetime_min_is_sentinel(self):
        self.assertIsNone(ascom_datetime("0001-01-01T00:00:00"))

    def test_string_datetime_min_with_ticks_is_sentinel(self):
        self.assertIsNone(ascom_datetime("0001-01-01T00:00:00.0000000"))

    def test_string_datetime_min_with_z_is_sentinel(self):
        self.assertIsNone(ascom_datetime("0001-01-01T00:00:00Z"))

    def test_string_datetime_min_with_offset_is_sentinel(self):
        self.assertIsNone(ascom_datetime("0001-01-01T00:00:00+00:00"))

    def test_string_real_iso_parses(self):
        self.assertEqual(
            ascom_datetime("2026-09-26T12:00:00"),
            datetime(2026, 9, 26, 12, 0, 0),
        )

    def test_string_min_with_nonzero_microseconds_not_sentinel(self):
        self.assertEqual(
            ascom_datetime("0001-01-01T00:00:00.123"),
            datetime(1, 1, 1, 0, 0, 0, 123000),
        )


class AscomFloatFallbackTest(unittest.TestCase):
    """Defensive fallback paths in ascom_float/ascom_int for non-standard
    types (Decimal, custom classes, etc.) that aren't str/float/int/bool.
    Coercion goes through str() then float() / int(float()).
    """

    def test_ascom_float_fallback_for_decimal(self):
        from decimal import Decimal

        self.assertEqual(ascom_float(Decimal("3.14")), 3.14)
        self.assertEqual(ascom_float(Decimal("-1.5")), -1.5)

    def test_ascom_int_fallback_for_decimal(self):
        from decimal import Decimal

        self.assertEqual(ascom_int(Decimal("42")), 42)
        self.assertEqual(ascom_int(Decimal("3.7")), 3)


class AscomDatetimeFallbackTest(unittest.TestCase):
    """Defensive fallback paths in ascom_datetime for unparseable strings
    and non-string/non-None inputs."""

    def test_invalid_string_returns_none(self):
        self.assertIsNone(ascom_datetime("not a date"))
        self.assertIsNone(ascom_datetime("2026-13-45"))  # impossible month/day
        self.assertIsNone(ascom_datetime("garbage"))

    def test_non_string_non_none_returns_none(self):
        self.assertIsNone(ascom_datetime(12345))  # int
        self.assertIsNone(ascom_datetime(1.5))  # float
        self.assertIsNone(ascom_datetime(["2026-09-22"]))  # list
        self.assertIsNone(ascom_datetime({"date": "2026-09-22"}))  # dict


if __name__ == "__main__":
    unittest.main()
