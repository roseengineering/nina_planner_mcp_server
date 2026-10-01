import unittest

from nina_planner.models.guider import GuiderDevice, GuiderMetric


class GuiderDeviceDeriveFieldsTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestGuider",
            "description": "test guider",
        }
        data.update(overrides)
        return data

    def test_guider_state_from_app_state(self):
        guider = GuiderDevice(**self._base(app_state="Guiding"))
        self.assertEqual(guider.state, "Guiding")

    def test_guider_state_falls_back_to_state_key(self):
        guider = GuiderDevice(**self._base(state="Idle"))
        self.assertEqual(guider.state, "Idle")

    def test_guider_state_prefers_app_state_over_state(self):
        guider = GuiderDevice(**self._base(app_state="Guiding", state="Idle"))
        self.assertEqual(guider.state, "Guiding")

    def test_guider_pixel_scale_mapped(self):
        guider = GuiderDevice(**self._base(pixel_scale=1.5))
        self.assertEqual(guider.pixel_scale, 1.5)

    def test_guider_pixel_scale_none_preserves_default(self):
        guider = GuiderDevice(**self._base(pixel_scale=None))
        self.assertIsNone(guider.pixel_scale)

    def test_guider_full_integration(self):
        guider = GuiderDevice(
            **self._base(
                app_state="Guiding",
                pixel_scale=1.5,
                rms_error={
                    "ra": {"pixel": 0.5, "arcseconds": 1.2},
                    "peak_dec": {"pixel": 1.0, "arcseconds": 2.5},
                },
            )
        )
        self.assertEqual(guider.state, "Guiding")
        self.assertEqual(guider.pixel_scale, 1.5)
        self.assertEqual(guider.ra_rms_error.arcseconds, 1.2)
        self.assertEqual(guider.peak_dec_excursion.pixel, 1.0)


class GuiderDeviceNestedRmsErrorTest(unittest.TestCase):
    """ninaAPI nests guiding error under `RMSError`."""

    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestGuider",
            "description": "test guider",
        }
        data.update(overrides)
        return data

    def test_nested_rms_error_populates_all_metrics(self):
        guider = GuiderDevice(
            **self._base(
                rms_error={
                    "ra": {"pixel": 0.35, "arcseconds": 0.43},
                    "dec": {"pixel": 0.28, "arcseconds": 0.34},
                    "total": {"pixel": 0.45, "arcseconds": 0.55},
                    "peak_ra": {"pixel": 1.2, "arcseconds": 1.48},
                    "peak_dec": {"pixel": 0.9, "arcseconds": 1.11},
                }
            )
        )
        self.assertEqual(guider.ra_rms_error.arcseconds, 0.43)
        self.assertEqual(guider.ra_rms_error.pixel, 0.35)
        self.assertEqual(guider.dec_rms_error.arcseconds, 0.34)
        self.assertEqual(guider.dec_rms_error.pixel, 0.28)
        self.assertEqual(guider.total_rms_error.arcseconds, 0.55)
        self.assertEqual(guider.total_rms_error.pixel, 0.45)
        self.assertEqual(guider.peak_ra_excursion.arcseconds, 1.48)
        self.assertEqual(guider.peak_ra_excursion.pixel, 1.2)
        self.assertEqual(guider.peak_dec_excursion.arcseconds, 1.11)
        self.assertEqual(guider.peak_dec_excursion.pixel, 0.9)

    def test_nested_metric_missing_pixel_leaves_pixel_none(self):
        guider = GuiderDevice(**self._base(rms_error={"ra": {"arcseconds": 0.43}}))
        self.assertEqual(guider.ra_rms_error.arcseconds, 0.43)
        self.assertIsNone(guider.ra_rms_error.pixel)

    def test_non_dict_nested_block_is_ignored(self):
        guider = GuiderDevice(**self._base(rms_error="n/a"))
        self.assertIsNone(guider.ra_rms_error.arcseconds)
        self.assertIsNone(guider.total_rms_error.pixel)

    def test_non_dict_metric_entry_is_ignored(self):
        guider = GuiderDevice(**self._base(rms_error={"ra": "n/a"}))
        self.assertIsNone(guider.ra_rms_error.arcseconds)


class GuiderMetricTest(unittest.TestCase):
    def test_default_metric_has_none_fields(self):
        m = GuiderMetric()
        self.assertIsNone(m.arcseconds)
        self.assertIsNone(m.pixel)

    def test_metric_with_values(self):
        m = GuiderMetric(arcseconds=1.0, pixel=0.5)
        self.assertEqual(m.arcseconds, 1.0)
        self.assertEqual(m.pixel, 0.5)


if __name__ == "__main__":
    unittest.main()
