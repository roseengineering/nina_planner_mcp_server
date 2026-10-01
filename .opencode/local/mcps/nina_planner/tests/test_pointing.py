import unittest
from typing import Any

from pydantic import ValidationError

from nina_planner.models.pointing import Pointing


def _pointing(**overrides: Any) -> Pointing:
    data: dict[str, Any] = {
        "ra_hours": 5.0,
        "dec_deg": 10.0,
    }
    data.update(overrides)
    return Pointing.model_validate(data)


class PointingPositionAngleDegValidationTest(unittest.TestCase):
    def test_position_angle_deg_zero_accepted(self):
        p = _pointing(position_angle_deg=0.0)
        self.assertEqual(p.position_angle_deg, 0.0)

    def test_position_angle_deg_just_under_360_accepted(self):
        p = _pointing(position_angle_deg=359.9999)
        self.assertEqual(p.position_angle_deg, 359.9999)

    def test_position_angle_deg_midrange_accepted(self):
        for value in (90.0, 180.0, 270.0):
            with self.subTest(position_angle_deg=value):
                p = _pointing(position_angle_deg=value)
                self.assertEqual(p.position_angle_deg, value)

    def test_position_angle_deg_exactly_360_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            _pointing(position_angle_deg=360.0)

    def test_position_angle_deg_just_over_360_raises(self):
        with self.assertRaises(ValidationError):
            _pointing(position_angle_deg=360.001)

    def test_position_angle_deg_negative_raises_validation_error(self):
        # Locks the ge=0.0 lower bound: any value below 0 raises.
        with self.assertRaises(ValidationError):
            _pointing(position_angle_deg=-0.001)

    def test_position_angle_deg_none_is_allowed(self):
        p = _pointing(position_angle_deg=None)
        self.assertIsNone(p.position_angle_deg)

    def test_position_angle_deg_string_coerced(self):
        p = _pointing(position_angle_deg="90.0")
        self.assertEqual(p.position_angle_deg, 90.0)

    def test_position_angle_deg_error_message_mentions_field(self):
        with self.assertRaises(ValidationError) as ctx:
            _pointing(position_angle_deg=360.0)
        self.assertIn("position_angle_deg", str(ctx.exception))

    def test_position_angle_does_not_affect_label_ra_dec_validation(self):
        # Invalid PA + valid ra/dec still raises for PA only; other fields
        # are accepted independently.
        with self.assertRaises(ValidationError) as ctx:
            _pointing(position_angle_deg=360.0)
        msg = str(ctx.exception)
        self.assertIn("position_angle_deg", msg)
        self.assertNotIn("ra_hours", msg)
        self.assertNotIn("dec_deg", msg)


class PointingMosaicMetadataTest(unittest.TestCase):
    def test_row_and_column_default_none(self):
        p = _pointing()
        self.assertIsNone(p.row)
        self.assertIsNone(p.column)

    def test_row_and_column_accepted(self):
        p = _pointing(row=2, column=3)
        self.assertEqual(p.row, 2)
        self.assertEqual(p.column, 3)

    def test_row_and_column_coerced_from_string(self):
        p = _pointing(row="1", column="4")
        self.assertEqual(p.row, 1)
        self.assertEqual(p.column, 4)

    def test_negative_row_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            _pointing(row=-1)

    def test_negative_column_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            _pointing(column=-1)


if __name__ == "__main__":
    unittest.main()
