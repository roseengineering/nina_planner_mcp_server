import unittest

from nina_planner.models.weather import Weather


class WeatherDeriveFieldsTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestWeather",
            "description": "test weather",
        }
        data.update(overrides)
        return data

    def test_weather_full_integration(self):
        """All 11 weather fields mapped from camelCase input."""
        data = self._base(
            temperature=15.5,
            dew_point=10.2,
            humidity=65.0,
            pressure=1013.25,
            wind_speed=2.5,
            wind_gust=4.0,
            wind_direction=180.0,
            sky_temperature=5.0,
            sky_quality=21.5,
            rain_rate=0.0,
            cloud_cover=10.0,
        )
        w = Weather(**data)
        self.assertEqual(w.temperature_c, 15.5)
        self.assertEqual(w.dew_point_c, 10.2)
        self.assertEqual(w.humidity_pct, 65.0)
        self.assertEqual(w.pressure_hpa, 1013.25)
        self.assertEqual(w.wind_speed_mps, 2.5)
        self.assertEqual(w.wind_gust_mps, 4.0)
        self.assertEqual(w.wind_direction_deg, 180.0)
        self.assertEqual(w.sky_temperature_c, 5.0)
        self.assertEqual(w.sky_quality_mpsas, 21.5)
        self.assertEqual(w.rain_rate_mm_per_hr, 0.0)
        self.assertEqual(w.cloud_cover_pct, 10.0)

    def test_weather_empty_dict_yields_none_fields(self):
        """Input with no weather keys → all model fields stay None."""
        w = Weather(**self._base())
        self.assertIsNone(w.temperature_c)
        self.assertIsNone(w.dew_point_c)
        self.assertIsNone(w.humidity_pct)
        self.assertIsNone(w.pressure_hpa)
        self.assertIsNone(w.wind_speed_mps)
        self.assertIsNone(w.wind_gust_mps)
        self.assertIsNone(w.wind_direction_deg)
        self.assertIsNone(w.sky_temperature_c)
        self.assertIsNone(w.sky_quality_mpsas)
        self.assertIsNone(w.rain_rate_mm_per_hr)
        self.assertIsNone(w.cloud_cover_pct)

    def test_weather_with_string_values(self):
        """String-encoded numbers are parsed by ascom_float."""
        data = self._base(
            temperature="15.5",
            humidity="65",
            pressure="1013.25",
        )
        w = Weather(**data)
        self.assertEqual(w.temperature_c, 15.5)
        self.assertEqual(w.humidity_pct, 65.0)
        self.assertEqual(w.pressure_hpa, 1013.25)

    def test_weather_with_real_minus_one_preserved(self):
        """A real -1°C temperature (or other negative values) is preserved
        as a real value, not nulled.
        """
        w = Weather(**self._base(temperature=-1.0, dew_point=-1.0))
        self.assertEqual(w.temperature_c, -1.0)
        self.assertEqual(w.dew_point_c, -1.0)

    def test_weather_with_nan_string_yields_none(self):
        """NaN/empty strings are filtered by ascom_float."""
        w = Weather(**self._base(temperature="NaN", humidity="", pressure="n/a"))
        self.assertIsNone(w.temperature_c)
        self.assertIsNone(w.humidity_pct)
        self.assertIsNone(w.pressure_hpa)

    def test_weather_partial_input(self):
        """Some weather fields present, others absent — present ones parsed,
        absent ones stay None."""
        w = Weather(**self._base(temperature=20.0, humidity=50.0))
        self.assertEqual(w.temperature_c, 20.0)
        self.assertEqual(w.humidity_pct, 50.0)
        self.assertIsNone(w.dew_point_c)
        self.assertIsNone(w.pressure_hpa)
        self.assertIsNone(w.wind_speed_mps)


if __name__ == "__main__":
    unittest.main()
