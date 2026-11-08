import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from nina_planner.models.frame import FrameStats


class FrameStatsNormalizationTest(unittest.TestCase):
    def test_camel_case_fields_map_and_coerce(self):
        # The endpoint's exact camelCase spellings (see _FIELD_MAP) must land
        # on the snake_case fields, with ASCOM's string numerics coerced.
        frame = FrameStats.model_validate(
            {
                "Filename": "M31_0001.fits",
                "ImageType": "LIGHT",
                "HFR": "0.42",
                "HFRStDev": "0.03",
                "Stars": "12",
                "Gain": "100",
                "RmsText": 'Tot: 0.42 (0.31")',
                "Date": "2026-09-22T21:00:00Z",
            }
        )
        self.assertEqual(frame.filename, "M31_0001.fits")
        self.assertEqual(frame.image_type, "LIGHT")
        self.assertEqual(frame.hfr, 0.42)
        self.assertEqual(frame.hfr_st_dev, 0.03)
        self.assertEqual(frame.stars, 12)
        self.assertEqual(frame.gain, 100)
        self.assertEqual(frame.rms_text, 'Tot: 0.42 (0.31")')
        self.assertEqual(frame.date, datetime(2026, 9, 22, 21, 0, tzinfo=UTC))

    def test_unmeasured_nan_normalizes_to_none(self):
        # Unlike ImageMetaData.csv's 0-is-unmeasured, this endpoint sends
        # NaN; the model must turn it into None so 0 stays a real reading.
        frame = FrameStats.model_validate({"HFR": float("nan"), "Stars": float("nan")})
        self.assertIsNone(frame.hfr)
        self.assertIsNone(frame.stars)

    def test_zero_star_count_is_preserved(self):
        frame = FrameStats.model_validate({"Stars": 0, "HFR": 0.0})
        self.assertEqual(frame.stars, 0)
        self.assertEqual(frame.hfr, 0.0)

    def test_non_mapping_payload_is_rejected_not_crashed(self):
        # The before-validator hands a non-dict straight back so pydantic —
        # not the normalizer — reports the type error, instead of the
        # validator raising AttributeError while iterating the payload.
        with self.assertRaises(ValidationError):
            FrameStats.model_validate("not a frame")


if __name__ == "__main__":
    unittest.main()
