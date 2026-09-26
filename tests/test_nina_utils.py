import math
import unittest

from nina_planner.nina_utils import ascom_datetime, ascom_float, ascom_int


class AscomFloatTest(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(ascom_float(None))

    def test_float_passes_through(self):
        self.assertEqual(ascom_float(1.5), 1.5)

    def test_int_promoted(self):
        self.assertEqual(ascom_float(3), 3.0)

    def test_int_minus_one_is_sentinel(self):
        self.assertIsNone(ascom_float(-1))

    def test_float_nan_is_sentinel(self):
        self.assertIsNone(ascom_float(float("nan")))

    def test_float_minus_one_is_sentinel(self):
        self.assertIsNone(ascom_float(-1.0))

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


class AscomIntTest(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(ascom_int(None))

    def test_int_passes_through(self):
        self.assertEqual(ascom_int(42), 42)

    def test_float_truncates(self):
        self.assertEqual(ascom_int(3.7), 3)

    def test_int_minus_one_is_sentinel(self):
        self.assertIsNone(ascom_int(-1))

    def test_float_minus_one_is_sentinel(self):
        self.assertIsNone(ascom_int(-1.0))

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


class AscomDatetimeTest(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(ascom_datetime(None))

    def test_string_nan_is_sentinel(self):
        self.assertIsNone(ascom_datetime("nan"))

    def test_string_empty_is_sentinel(self):
        self.assertIsNone(ascom_datetime(""))


if __name__ == "__main__":
    unittest.main()
