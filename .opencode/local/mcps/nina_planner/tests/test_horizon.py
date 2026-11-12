"""Custom-horizon parsing, interpolation, and fetching."""

import unittest
from unittest.mock import AsyncMock, patch

from nina_planner.horizon import Horizon, fetch_horizon, parse_horizon


class HorizonModelTest(unittest.TestCase):
    def test_empty_arrays_are_a_flat_horizon(self):
        h = parse_horizon({"Altitudes": [], "Azimuths": []})
        self.assertFalse(h.is_custom)
        for az in (0.0, 90.0, 180.0, 359.0):
            self.assertEqual(h.altitude_at(az), 0.0)

    def test_single_point_is_not_custom(self):
        # N.I.N.A. requires at least two pairs for a horizon file to take
        # effect; one point must degrade to flat 0, not interpolate.
        h = Horizon(points=[(90.0, 30.0)])
        self.assertFalse(h.is_custom)
        self.assertEqual(h.altitude_at(90.0), 0.0)

    def test_payload_without_horizon_shape_is_flat(self):
        # A mocked api_get can return the profile payload for this path;
        # it must parse to flat, never raise.
        self.assertFalse(parse_horizon({"Name": "Test"}).is_custom)
        self.assertFalse(parse_horizon(None).is_custom)
        self.assertFalse(parse_horizon([1, 2]).is_custom)
        self.assertFalse(parse_horizon({"Azimuths": "x", "Altitudes": []}).is_custom)

    def test_exact_knot_returns_its_value(self):
        h = Horizon(points=[(0.0, 10.0), (90.0, 40.0), (180.0, 20.0)])
        self.assertEqual(h.altitude_at(0.0), 10.0)
        self.assertEqual(h.altitude_at(90.0), 40.0)
        self.assertEqual(h.altitude_at(180.0), 20.0)

    def test_interpolates_between_knots(self):
        h = Horizon(points=[(0.0, 10.0), (90.0, 40.0)])
        self.assertAlmostEqual(h.altitude_at(45.0), 25.0)
        self.assertAlmostEqual(h.altitude_at(30.0), 20.0)

    def test_wraps_across_the_north_seam(self):
        # Points at 350 and 10: az 0 sits 10/20 of the way across the seam,
        # az 355 a quarter, az 5 three-quarters.
        h = Horizon(points=[(10.0, 30.0), (350.0, 10.0)])
        self.assertAlmostEqual(h.altitude_at(0.0), 20.0)
        self.assertAlmostEqual(h.altitude_at(355.0), 15.0)
        self.assertAlmostEqual(h.altitude_at(5.0), 25.0)

    def test_azimuth_is_normalized(self):
        h = Horizon(points=[(0.0, 10.0), (90.0, 40.0)])
        self.assertAlmostEqual(h.altitude_at(450.0), h.altitude_at(90.0))
        self.assertAlmostEqual(h.altitude_at(-90.0), h.altitude_at(270.0))

    def test_unsorted_and_duplicate_points_are_normalized(self):
        # N.I.N.A. files conventionally close the circle with 360 mirroring
        # 0; wrapping must dedupe rather than create a zero-width segment.
        h = Horizon(points=[(180.0, 20.0), (360.0, 10.0), (0.0, 99.0)])
        self.assertEqual(h.points, [(0.0, 99.0), (180.0, 20.0)])
        self.assertAlmostEqual(h.altitude_at(90.0), 59.5)

    def test_garbage_pairs_are_dropped_not_fatal(self):
        h = Horizon(points=[(0.0, 10.0), ("x", 20.0), (90.0, None), (90.0, 40.0)])
        self.assertEqual(h.points, [(0.0, 10.0), (90.0, 40.0)])
        self.assertTrue(h.is_custom)

    def test_result_is_clamped(self):
        h = Horizon(points=[(0.0, 95.0), (180.0, -95.0)])
        self.assertEqual(h.altitude_at(0.0), 90.0)
        self.assertEqual(h.altitude_at(180.0), -90.0)

    def test_is_custom_reflects_parsed_points(self):
        h = parse_horizon({"Azimuths": [0, 180], "Altitudes": [10, 20]})
        self.assertTrue(h.is_custom)
        self.assertEqual(len(h.points), 2)


class FetchHorizonTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_and_parses_the_endpoint(self):
        payload = {"Azimuths": [0, 90, 180], "Altitudes": [10, 40, 20]}
        with patch(
            "nina_planner.client.api_get", AsyncMock(return_value=payload)
        ) as api:
            h = await fetch_horizon("C:\\hrz\\site.hrz")
        api.assert_awaited_once_with("/profile/horizon")
        self.assertTrue(h.is_custom)
        self.assertEqual(h.file_path, "C:\\hrz\\site.hrz")
        self.assertAlmostEqual(h.altitude_at(45.0), 25.0)

    async def test_endpoint_failure_degrades_to_flat(self):
        with patch(
            "nina_planner.client.api_get",
            AsyncMock(side_effect=RuntimeError("Unknown endpoint")),
        ):
            h = await fetch_horizon("C:\\hrz\\site.hrz")
        self.assertFalse(h.is_custom)
        self.assertEqual(h.file_path, "C:\\hrz\\site.hrz")
        self.assertEqual(h.altitude_at(123.0), 0.0)

    async def test_empty_response_is_flat(self):
        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value={"Azimuths": [], "Altitudes": []}),
        ):
            h = await fetch_horizon()
        self.assertFalse(h.is_custom)
        self.assertEqual(h.file_path, "")


class ProfileHorizonIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """get_site_profile carries the horizon; a horizon failure never fails it."""

    @staticmethod
    def _raw(horizon_file: str = "") -> dict:
        return {
            "Name": "Test",
            "Id": "abc",
            "AstrometrySettings": {
                "Latitude": 45.0,
                "Longitude": -75.0,
                "Elevation": 100.0,
                "HorizonFilePath": horizon_file,
            },
            "TelescopeSettings": {},
            "CameraSettings": {},
            "FilterWheelSettings": {"FilterWheelFilters": []},
            "ImageFileSettings": {"FilePath": "C:\\NINA", "FilePattern": None},
            "PlateSolveSettings": {},
            "FramingAssistantSettings": {},
            "GuiderSettings": {},
            "RotatorSettings": {},
            "DomeSettings": {},
            "WeatherDataSettings": {},
            "SafetyMonitorSettings": {},
            "FocuserSettings": {},
            "SwitchSettings": {},
        }

    async def test_profile_carries_the_horizon(self):
        from nina_planner.tools.equipment import get_site_profile

        horizon = {"Azimuths": [0, 180, 350], "Altitudes": [14, 30, 20]}

        async def fake(path, **kwargs):
            if path == "/profile/horizon":
                return horizon
            return self._raw("C:\\hrz\\site.hrz")

        with patch("nina_planner.client.api_get", side_effect=fake):
            profile = await get_site_profile()
        self.assertTrue(profile.horizon.is_custom)
        self.assertEqual(profile.horizon.file_path, "C:\\hrz\\site.hrz")
        self.assertAlmostEqual(profile.horizon.altitude_at(180.0), 30.0)

    async def test_horizon_endpoint_failure_keeps_the_profile(self):
        from nina_planner.tools.equipment import get_site_profile

        async def fake(path, **kwargs):
            if path == "/profile/horizon":
                raise RuntimeError("Unknown endpoint /profile/horizon")
            return self._raw()

        with patch("nina_planner.client.api_get", side_effect=fake):
            profile = await get_site_profile()
        self.assertEqual(profile.profile_name, "Test")
        self.assertFalse(profile.horizon.is_custom)
        self.assertEqual(profile.horizon.altitude_at(90.0), 0.0)


if __name__ == "__main__":
    unittest.main()
