import unittest
from datetime import UTC, datetime

from nina_planner.nina_utils import ascom_datetime, ascom_float, ascom_int

# (input, expected) readings shared by ascom_float and ascom_int: sentinels
# map to None on both, numerics coerce to the same value (ascom_int
# truncates towards zero).
_CASES = (
    (None, None),
    (1.5, 1.5),
    (3, 3.0),
    (-1, -1.0),
    (float("nan"), None),
    (-1.0, -1.0),
    (True, 1.0),
    (False, 0.0),
    ("2.5", 2.5),
    ("nan", None),
    ("NaN", None),
    (" NaN ", None),
    ("na", None),
    ("NA", None),
    (" NA ", None),
    ("N/A", None),
    ("n/a", None),
    (" N/A ", None),
    ("", None),
    ("   ", None),
    ("-1", -1.0),
    (" -1 ", -1.0),
    ("-1.5", -1.5),
    # exact ints pass through on both converters.
    (42, 42.0),
    ("42", 42.0),
    ("-10", -10.0),
)

# Truncating shapes only ascom_int sees (ascom_float returns the full value).
_INT_ONLY_CASES = ((3.7, 3), ("42.7", 42))


def _assert_table(
    case: unittest.TestCase,
    convert,
    *,
    expected_as_float: bool,
    extra: tuple = (),
) -> None:
    for value, expected in (*_CASES, *extra):
        with case.subTest(value=value):
            got = convert(value)
            if expected is None:
                case.assertIsNone(got)
            elif expected_as_float:
                case.assertEqual(got, expected)
            else:
                case.assertEqual(got, int(expected))


class AscomFloatTest(unittest.TestCase):
    def test_table(self):
        _assert_table(self, ascom_float, expected_as_float=True)


class AscomIntTest(unittest.TestCase):
    def test_table(self):
        _assert_table(self, ascom_int, expected_as_float=False, extra=_INT_ONLY_CASES)


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


class ToLocalDisplayTest(unittest.TestCase):
    def test_naive_datetime_is_taken_as_already_local(self):
        from nina_planner.nina_utils import to_local_display

        # No conversion: a naive wall clock is rendered verbatim (with its
        # seconds), matching "naive means local" everywhere else.
        self.assertEqual(
            to_local_display(datetime(2026, 9, 22, 21, 0, 0)), "2026-09-22T21:00:00"
        )

    def test_aware_datetime_is_rendered_in_the_host_zone(self):
        from nina_planner.nina_utils import to_local_display

        rendered = to_local_display(datetime(2026, 9, 22, 21, 0, 0, tzinfo=UTC))
        self.assertTrue(rendered.startswith("2026-09-2"))
        self.assertIn(":", rendered[-6:])


if __name__ == "__main__":
    unittest.main()
