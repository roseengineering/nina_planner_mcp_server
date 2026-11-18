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
    def test_accepted_values(self):
        for value in (0.0, 90.0, 180.0, 270.0, 359.9999, "90.0"):
            with self.subTest(position_angle_deg=value):
                p = _pointing(position_angle_deg=value)
                expected = 90.0 if value == "90.0" else value
                self.assertEqual(p.position_angle_deg, expected)

    def test_none_is_allowed(self):
        self.assertIsNone(_pointing(position_angle_deg=None).position_angle_deg)

    def test_rejected_values(self):
        # Locks the [0.0, 360.0) bounds and that the error names the field.
        for value in (-0.001, 360.0, 360.001):
            with self.subTest(position_angle_deg=value):
                with self.assertRaises(ValidationError) as ctx:
                    _pointing(position_angle_deg=value)
                self.assertIn("position_angle_deg", str(ctx.exception))

    def test_rejected_value_blames_only_the_pa_field(self):
        with self.assertRaises(ValidationError) as ctx:
            _pointing(position_angle_deg=360.0)
        msg = str(ctx.exception)
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
