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

    def test_guider_ra_rms_error_metric_construction(self):
        guider = GuiderDevice(
            **self._base(
                ra_rms_error_arcseconds=1.2,
                ra_rms_error_pixel=0.5,
            )
        )
        self.assertEqual(guider.ra_rms_error.arcseconds, 1.2)
        self.assertEqual(guider.ra_rms_error.pixel, 0.5)

    def test_guider_dec_rms_error_metric_construction(self):
        guider = GuiderDevice(
            **self._base(
                dec_rms_error_arcseconds=0.8,
                dec_rms_error_pixel=0.3,
            )
        )
        self.assertEqual(guider.dec_rms_error.arcseconds, 0.8)
        self.assertEqual(guider.dec_rms_error.pixel, 0.3)

    def test_guider_total_rms_error_metric_construction(self):
        guider = GuiderDevice(
            **self._base(
                total_rms_error_arcseconds=1.5,
                total_rms_error_pixel=0.6,
            )
        )
        self.assertEqual(guider.total_rms_error.arcseconds, 1.5)
        self.assertEqual(guider.total_rms_error.pixel, 0.6)

    def test_guider_peak_ra_excursion_metric_construction(self):
        guider = GuiderDevice(
            **self._base(
                peak_ra_excursion_arcseconds=3.0,
                peak_ra_excursion_pixel=1.2,
            )
        )
        self.assertEqual(guider.peak_ra_excursion.arcseconds, 3.0)
        self.assertEqual(guider.peak_ra_excursion.pixel, 1.2)

    def test_guider_peak_dec_excursion_metric_construction(self):
        guider = GuiderDevice(
            **self._base(
                peak_dec_excursion_arcseconds=2.5,
                peak_dec_excursion_pixel=1.0,
            )
        )
        self.assertEqual(guider.peak_dec_excursion.arcseconds, 2.5)
        self.assertEqual(guider.peak_dec_excursion.pixel, 1.0)

    def test_guider_metric_with_only_arcseconds(self):
        """Partial data — only arcseconds, no pixel → pixel=None."""
        guider = GuiderDevice(**self._base(ra_rms_error_arcseconds=1.0))
        self.assertEqual(guider.ra_rms_error.arcseconds, 1.0)
        self.assertIsNone(guider.ra_rms_error.pixel)

    def test_guider_full_integration(self):
        guider = GuiderDevice(
            **self._base(
                app_state="Guiding",
                pixel_scale=1.5,
                ra_rms_error_arcseconds=1.2,
                ra_rms_error_pixel=0.5,
                dec_rms_error_arcseconds=0.8,
                dec_rms_error_pixel=0.3,
                total_rms_error_arcseconds=1.5,
                total_rms_error_pixel=0.6,
                peak_ra_excursion_arcseconds=3.0,
                peak_ra_excursion_pixel=1.2,
                peak_dec_excursion_arcseconds=2.5,
                peak_dec_excursion_pixel=1.0,
            )
        )
        self.assertEqual(guider.state, "Guiding")
        self.assertEqual(guider.pixel_scale, 1.5)
        self.assertEqual(guider.ra_rms_error.arcseconds, 1.2)
        self.assertEqual(guider.peak_dec_excursion.pixel, 1.0)


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
