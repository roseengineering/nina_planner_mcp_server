import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from nina_planner.models.observatory import ObservatoryEquipment
from nina_planner.models.plan import ObservationPlan
from nina_planner.models.profile import ObservatoryProfile
from nina_planner.server import (
    _check_pointing_index,
    _convert_keys,
    _convert_to_met,
    _log_payload,
    _sanitize_filename,
    _validate_filters,
    _validate_position_angle,
)


class ConvertToMetTest(unittest.IsolatedAsyncioTestCase):
    def _now(self) -> datetime:
        return datetime(2026, 9, 22, 3, 0, 0, tzinfo=UTC)

    def _patch_api_get(self, now: datetime, data):
        async def fake(path):
            if path == "/time":
                return now.isoformat()
            return data

        return patch("nina_planner.server._api_get", side_effect=fake)

    async def test_utc_z_seconds(self):
        now = self._now()
        data = [{"Time": "2026-09-22T02:16:25Z"}]
        with self._patch_api_get(now, data):
            res = await _convert_to_met(data, since=3600, name="Time")
        self.assertEqual(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    async def test_offset_shifted_to_utc_z(self):
        now = self._now()
        data = [{"Time": "2026-09-21T21:16:25-05:00"}]
        with self._patch_api_get(now, data):
            res = await _convert_to_met(data, since=3600, name="Time")
        self.assertEqual(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    async def test_microseconds_stripped(self):
        now = self._now()
        data = [{"Time": "2026-09-22T02:16:25.123456Z"}]
        with self._patch_api_get(now, data):
            res = await _convert_to_met(data, since=3600, name="Time")
        self.assertEqual(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    async def test_naive_uses_now_tzinfo(self):
        now = self._now()
        data = [{"Time": "2026-09-22 02:16:25"}]
        with self._patch_api_get(now, data):
            res = await _convert_to_met(data, since=3600, name="Time")
        self.assertEqual(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    async def test_naive_uses_now_tzinfo_with_z_events(self):
        data = [{"Time": "2026-09-22T03:00:00Z"}]

        async def fake(path):
            if path == "/time":
                return "2026-09-22 03:00:00"
            return data

        with patch("nina_planner.server._api_get", side_effect=fake):
            res = await _convert_to_met(data, since=86400, name="Time")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["timestamp"], "2026-09-22T03:00:00Z")

    async def test_aware_uses_now_tzinfo_with_naive_events(self):
        now = self._now()
        data = [{"Time": "2026-09-22 03:00:00"}]

        async def fake(path):
            if path == "/time":
                return now.isoformat()
            return data

        with patch("nina_planner.server._api_get", side_effect=fake):
            res = await _convert_to_met(data, since=3600, name="Time")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["timestamp"], "2026-09-22T03:00:00Z")

    async def test_old_entries_filtered(self):
        now = self._now()
        data = [{"Time": "2026-09-20T00:00:00Z"}]
        with self._patch_api_get(now, data):
            res = await _convert_to_met(data, since=3600, name="Time")
        self.assertEqual(res, [])


class HelperFunctionsTest(unittest.TestCase):
    """Pure-logic helpers in server.py."""

    def test_sanitize_filename_strips_invalid_chars(self):
        # Path separators, control chars, and Windows-reserved chars all
        # replaced with a hyphen.
        self.assertEqual(
            _sanitize_filename('a/b\\c:d*e?f"g<h>i|j'), "a-b-c-d-e-f-g-h-i-j"
        )
        self.assertEqual(_sanitize_filename("plain_name"), "plain_name")

    def test_convert_keys_dict(self):
        result = _convert_keys({"CamelCase": 1, "snake_case": 2})
        self.assertEqual(result, {"camel_case": 1, "snake_case": 2})

    def test_convert_keys_nested(self):
        result = _convert_keys({"OuterKey": {"InnerKey": [{"DeepKey": 1}]}})
        self.assertEqual(result, {"outer_key": {"inner_key": [{"deep_key": 1}]}})

    def test_convert_keys_list(self):
        result = _convert_keys([{"ListKey": 1}, {"OtherKey": 2}])
        self.assertEqual(result, [{"list_key": 1}, {"other_key": 2}])

    def test_convert_keys_passthrough_non_dict_non_list(self):
        self.assertEqual(_convert_keys(42), 42)
        self.assertEqual(_convert_keys("string"), "string")
        self.assertEqual(_convert_keys(None), None)

    def test_log_payload_writes_to_file(self):
        with tempfile_TemporaryDirectory_patch() as tmp:
            log_path = Path(tmp) / "test.log"
            with patch("nina_planner.server.NINA_PLANNER_LOG", str(log_path)):
                _log_payload("Test", "payload")
            content = log_path.read_text()
            self.assertIn("Test", content)
            self.assertIn("payload", content)

    def test_log_payload_falls_back_to_stderr_when_no_env(self):
        # When NINA_PLANNER_LOG is unset, log_payload writes to stderr.
        # Hard to assert stderr cleanly — just verify no exception.
        with patch("nina_planner.server.NINA_PLANNER_LOG", None):
            _log_payload("Test", "payload")  # should not raise

    def _make_plan(self, **overrides):
        from nina_planner.models.plan import ObservationPlan

        data = {
            "plan_id": "plan-test",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
            ],
            "flat": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 5}
            ],
            "dark": [{"exposure_time_seconds": 60.0, "total_count": 5}],
            "bias": [{"total_count": 5}],
        }
        data.update(overrides)
        return ObservationPlan(**data)

    def _make_profile(self, filters=None, has_rotator=True):
        if filters is None:
            filters = []
        from nina_planner.models.profile import (
            EquipmentConfig,
            OpticalTrainInfo,
            SiteLocationInfo,
        )

        return ObservatoryProfile(
            profile_name="Test",
            profile_id="abc",
            description="test",
            site=SiteLocationInfo(),
            optics=OpticalTrainInfo(),
            filters=filters,
            plate_solver=None,
            blind_plate_solver=None,
            image_save_path="/tmp/x",
            file_pattern=None,
            equipment=EquipmentConfig(
                has_mount=False,
                has_camera=False,
                has_focuser=False,
                has_filter_wheel=False,
                has_guider=False,
                has_rotator=has_rotator,
                has_dome=False,
                has_weather=False,
                has_safety_monitor=False,
                has_switch=False,
            ),
        )

    def test_validate_filters_all_known(self):
        from nina_planner.models.profile import FilterInfo

        filters = [FilterInfo(name="L", position=1, focus_offset=0)]
        profile = self._make_profile(filters=filters)
        plan = self._make_plan()
        # No exception → all filters known. _validate_filters is async.
        import asyncio

        asyncio.run(_validate_filters(plan, profile))

    def test_validate_filters_unknown_filter_raises(self):
        from nina_planner.models.profile import FilterInfo

        filters = [FilterInfo(name="L", position=1, focus_offset=0)]
        profile = self._make_profile(filters=filters)
        plan = self._make_plan(
            light=[
                {
                    "filter_name": "UNKNOWN",
                    "exposure_time_seconds": 60.0,
                    "total_count": 1,
                }
            ]
        )
        import asyncio

        with self.assertRaises(ValueError) as ctx:
            asyncio.run(_validate_filters(plan, profile))
        self.assertIn("UNKNOWN", str(ctx.exception))

    def test_validate_filters_includes_reference_filter(self):
        from nina_planner.models.profile import FilterInfo

        # Plan with reference_filter_name not in any light/flat list —
        # validator should still check it.
        filters = [
            FilterInfo(name="L", position=1, focus_offset=0),
            FilterInfo(name="R", position=2, focus_offset=0),
        ]
        profile = self._make_profile(filters=filters)
        plan = self._make_plan(
            autofocus={"reference_filter_name": "R"},
            light=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat=[{"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}],
        )
        import asyncio

        asyncio.run(_validate_filters(plan, profile))  # no exception, R is in profile

    def test_validate_position_angle_all_set(self):
        profile = self._make_profile(has_rotator=True)
        plan = self._make_plan(
            pointings=[
                {"ra_hours": 5.0, "dec_deg": 10.0, "position_angle_deg": 0.0},
                {"ra_hours": 6.0, "dec_deg": 11.0, "position_angle_deg": 90.0},
            ]
        )
        _validate_position_angle(plan, profile)

    def test_validate_position_angle_mixed_raises(self):
        profile = self._make_profile(has_rotator=True)
        plan = self._make_plan(
            pointings=[
                {"ra_hours": 5.0, "dec_deg": 10.0, "position_angle_deg": 0.0},
                {"ra_hours": 6.0, "dec_deg": 11.0},
            ]
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_position_angle(plan, profile)
        self.assertIn("mixes position_angle_deg", str(ctx.exception))

    def test_validate_position_angle_no_rotator_raises(self):
        profile = self._make_profile(has_rotator=False)
        plan = self._make_plan(
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0, "position_angle_deg": 90.0}]
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_position_angle(plan, profile)
        self.assertIn("no rotator", str(ctx.exception))

    def test_check_pointing_index_in_range(self):
        plan = self._make_plan(
            pointings=[
                {"ra_hours": 5.0, "dec_deg": 10.0},
                {"ra_hours": 6.0, "dec_deg": 11.0},
            ]
        )
        _check_pointing_index(plan, 1)
        _check_pointing_index(plan, 2)

    def test_check_pointing_index_below_raises(self):
        plan = self._make_plan()
        with self.assertRaises(ValueError):
            _check_pointing_index(plan, 0)

    def test_check_pointing_index_above_raises(self):
        plan = self._make_plan()
        with self.assertRaises(ValueError):
            _check_pointing_index(plan, 99)


def tempfile_TemporaryDirectory_patch():
    """Lazy import to avoid pulling tempfile at module top level."""
    import tempfile

    return tempfile.TemporaryDirectory()


class McpToolDirectTest(unittest.IsolatedAsyncioTestCase):
    """Direct-call tests for @mcp.tool() functions in server.py.

    The @mcp.tool() decorator just registers the function with the MCP
    server; the function remains a normal Python callable. Patching the
    helper dependencies (HTTP/subprocess) exercises the tool bodies
    without going through MCP dispatch simulation.
    """

    def _minimal_profile(self):
        """Return an ObservatoryProfile with all equipment flags False so
        _get_site_equipment_status skips per-device API calls."""
        from nina_planner.models.profile import (
            EquipmentConfig,
            OpticalTrainInfo,
            SiteLocationInfo,
        )

        return ObservatoryProfile(
            profile_name="Test",
            profile_id="abc",
            description="test",
            site=SiteLocationInfo(),
            optics=OpticalTrainInfo(),
            filters=[],
            plate_solver=None,
            blind_plate_solver=None,
            image_save_path="C:\\NINA",
            file_pattern=None,
            equipment=EquipmentConfig(
                has_mount=False,
                has_camera=False,
                has_focuser=False,
                has_filter_wheel=False,
                has_guider=False,
                has_rotator=False,
                has_dome=False,
                has_weather=False,
                has_safety_monitor=False,
                has_switch=False,
            ),
        )

    def _full_profile(self, filters=None):
        from nina_planner.models.profile import (
            EquipmentConfig,
            FilterInfo,
            OpticalTrainInfo,
            SiteLocationInfo,
        )

        if filters is None:
            filters = [FilterInfo(name="L", position=1, focus_offset=0)]
        return ObservatoryProfile(
            profile_name="Test",
            profile_id="abc",
            description="test",
            site=SiteLocationInfo(),
            optics=OpticalTrainInfo(
                telescope_name="TestScope",
                focal_length_mm=600.0,
                focal_ratio=5.0,
                pixel_size_microns=3.76,
                default_gain=100,
                camera_xsize_px=3000,
                camera_ysize_px=2000,
            ),
            filters=filters,
            plate_solver=None,
            blind_plate_solver=None,
            image_save_path="C:\\NINA",
            file_pattern=None,
            equipment=EquipmentConfig(
                has_mount=True,
                has_camera=True,
                has_focuser=True,
                has_filter_wheel=True,
                has_guider=True,
                has_rotator=True,
                has_dome=False,
                has_weather=False,
                has_safety_monitor=False,
                has_switch=False,
            ),
        )

    # ---- get_site_profile ----

    async def test_get_site_profile_returns_observatory_profile(self):
        from nina_planner.server import get_site_profile

        raw = {
            "Name": "Test",
            "Id": "abc",
            "Description": "test profile",
            "AstrometrySettings": {
                "Latitude": 45.0,
                "Longitude": -90.0,
                "Elevation": 200.0,
            },
            "TelescopeSettings": {
                "Name": "Scope",
                "FocalLength": 600,
                "FocalRatio": 5.0,
            },
            "CameraSettings": {"Id": "Camera1", "PixelSize": 3.76, "Gain": 100},
            "FilterWheelSettings": {"Id": "FW1", "FilterWheelFilters": []},
            "ImageFileSettings": {"FilePath": "C:\\NINA", "FilePattern": None},
            "PlateSolveSettings": {"PlateSolverType": None, "BlindSolverType": None},
            "FramingAssistantSettings": {"CameraWidth": 3000, "CameraHeight": 2000},
            # GuiderSettings has GuiderName but it matches the skip set
            # (Direct_Guider, No_Guider), so has_guider becomes False.
            "GuiderSettings": {"GuiderName": "Direct_Guider"},
            "RotatorSettings": {"Id": "Rot1"},
            "DomeSettings": {},
            "WeatherDataSettings": {},
            "SafetyMonitorSettings": {},
            "FocuserSettings": {"Id": "Foc1"},
            "SwitchSettings": {},
        }
        with patch("nina_planner.server._api_get", AsyncMock(return_value=raw)):
            result = await get_site_profile()
        self.assertEqual(result.profile_name, "Test")
        self.assertEqual(result.optics.focal_length_mm, 600)
        self.assertTrue(result.equipment.has_mount)
        self.assertTrue(result.equipment.has_camera)
        # Direct_Guider is in the skip set, so has_guider should be False.
        self.assertFalse(result.equipment.has_guider)

    async def test_get_site_profile_with_filters(self):
        from nina_planner.server import get_site_profile

        filters = [
            {"Name": "L", "Position": 1, "FocusOffset": 0},
            {"Name": "R", "Position": 2, "FocusOffset": 1},
        ]
        raw = {
            "Name": "Test",
            "Id": "abc",
            "Description": "",
            "AstrometrySettings": {},
            "TelescopeSettings": {},
            "CameraSettings": {},
            "FilterWheelSettings": {"FilterWheelFilters": filters},
            "ImageFileSettings": {"FilePath": "", "FilePattern": None},
            "PlateSolveSettings": {"PlateSolverType": None, "BlindSolverType": None},
            "FramingAssistantSettings": {},
            "GuiderSettings": {},
            "RotatorSettings": {},
            "DomeSettings": {},
            "WeatherDataSettings": {},
            "SafetyMonitorSettings": {},
            "FocuserSettings": {},
            "SwitchSettings": {},
        }
        with patch("nina_planner.server._api_get", AsyncMock(return_value=raw)):
            result = await get_site_profile()
        self.assertEqual(len(result.filters), 2)
        self.assertEqual(result.filters[0].name, "L")
        self.assertEqual(result.filters[0].position, 1)
        self.assertEqual(result.filters[1].name, "R")

    # ---- _get_site_equipment_status ----

    async def test_get_site_equipment_status_returns_empty_when_all_false(self):
        from nina_planner.server import _get_site_equipment_status

        profile = self._minimal_profile()
        with patch("nina_planner.server._api_get", AsyncMock(return_value={})):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsInstance(equipment, ObservatoryEquipment)
        self.assertIsNone(equipment.mount)
        self.assertIsNone(equipment.camera)

    async def test_get_site_equipment_status_connects_missing_devices(self):
        from nina_planner.server import _get_site_equipment_status

        profile = self._minimal_profile()
        # Set has_mount=True but Connected=False → should call connect endpoint.
        from nina_planner.models.profile import EquipmentConfig

        profile_connected = profile.model_copy(
            update={
                "equipment": EquipmentConfig(
                    has_mount=True,
                    has_camera=False,
                    has_focuser=False,
                    has_filter_wheel=False,
                    has_guider=False,
                    has_rotator=False,
                    has_dome=False,
                    has_weather=False,
                    has_safety_monitor=False,
                    has_switch=False,
                )
            }
        )
        raw = {"Mount": {"Connected": False, "Name": "TestMount"}}

        async def fake(path, **kwargs):
            if "connect" in path:
                return {}
            return raw

        with patch("nina_planner.server._api_get", side_effect=fake):
            with self.assertRaises(RuntimeError) as ctx:
                await _get_site_equipment_status(profile_connected)
        self.assertIn("Mount", str(ctx.exception))

    # ---- get_events / get_logs ----

    async def test_get_events_returns_filtered(self):
        from nina_planner.server import get_events

        # Use a /time value that's already UTC-anchored so the test is
        # timezone-independent. /time is naive; if we make it Z-suffixed it's
        # treated as already-UTC and not tagged with host tz.
        events = [{"Time": "2026-09-22T02:55:00Z", "Type": "Info"}]
        time_str = "2026-09-22T03:00:00Z"  # UTC-anchored

        async def fake(path):
            if path == "/time":
                return time_str
            return events

        with patch("nina_planner.server._api_get", side_effect=fake):
            result = await get_events(since=3600)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["timestamp"], "2026-09-22T02:55:00Z")

    async def test_get_logs_strips_internal_fields(self):
        from nina_planner.server import get_logs

        logs = [
            {
                "Timestamp": "2026-09-22T02:55:00Z",
                "Message": "hello",
                "line": 42,
                "member": "M",
                "source": "test",
            }
        ]
        time_str = "2026-09-22T03:00:00Z"

        async def fake(path):
            if path == "/time":
                return time_str
            return logs

        with patch("nina_planner.server._api_get", side_effect=fake):
            result = await get_logs(since=3600)
        self.assertEqual(len(result), 1)
        self.assertIn("timestamp", result[0])
        self.assertNotIn("line", result[0])
        self.assertNotIn("member", result[0])
        self.assertNotIn("source", result[0])

    # ---- write_plan_file ----

    async def test_write_plan_file_creates_file(self):
        from nina_planner.server import write_plan_file

        plan = ObservationPlan(
            plan_id="plan-test123",
            target="M31",
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0}],
            light=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat=[{"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}],
            dark=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias=[{"total_count": 1}],
        )

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "subdir").mkdir()
            (project_dir / "subdir" / "progress.md").write_text("")
            with (
                patch("nina_planner.server.PROJECT_DIR", project_dir / "subdir"),
                patch(
                    "nina_planner.server.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
            ):
                result = await write_plan_file(plan)
        self.assertIn("Plan successfully written to", result)
        self.assertTrue(result.endswith(".json"))

    # ---- get_plan_progress ----

    async def test_get_plan_progress_returns_summary(self):
        import tempfile as _tempfile

        from nina_planner.server import get_plan_progress

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias": [{"total_count": 1}],
        }

        with _tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.server._read_metadata",
                AsyncMock(return_value=[]),
            ):
                result = await get_plan_progress(str(plan_path), pointing_index=1)
        self.assertEqual(result["plan_id"], "plan-test123-1")
        self.assertEqual(result["pointing_index"], 1)
        self.assertEqual(result["target"], "M31")
        self.assertIn("light", result["frame_types"])

    # ---- load_sequence_from_plan ----

    async def test_load_sequence_from_plan_remaining_mode(self):
        from nina_planner.server import load_sequence_from_plan

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            # plan_progress returns a dict of frame-type → list of plain dicts.
            fake_progress = {
                "light": [
                    {
                        "filter_name": "L",
                        "exposure_time_seconds": 60.0,
                        "total_count": 1,
                        "acquired_count": 1,
                        "remaining_count": 0,
                    }
                ],
                "flat": [
                    {
                        "filter_name": "L",
                        "exposure_time_seconds": 5.0,
                        "total_count": 1,
                        "acquired_count": 1,
                        "remaining_count": 0,
                    }
                ],
                "dark": [
                    {
                        "filter_name": None,
                        "exposure_time_seconds": 60.0,
                        "total_count": 1,
                        "acquired_count": 1,
                        "remaining_count": 0,
                    }
                ],
                "bias": [
                    {
                        "filter_name": None,
                        "exposure_time_seconds": None,
                        "total_count": 1,
                        "acquired_count": 1,
                        "remaining_count": 0,
                    }
                ],
            }

            with (
                patch(
                    "nina_planner.server.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
                patch(
                    "nina_planner.server._get_site_equipment_status",
                    AsyncMock(return_value=ObservatoryEquipment()),
                ),
                patch(
                    "nina_planner.server._read_metadata",
                    AsyncMock(return_value=[]),
                ),
                patch(
                    "nina_planner.server.plan_progress",
                    return_value=fake_progress,
                ),
            ):
                result = await load_sequence_from_plan(
                    str(plan_path), frame_type="light", pointing_index=1
                )
        self.assertIn("is complete", result)
        self.assertIn("nothing to load", result)

    async def test_load_sequence_from_plan_invalid_mode(self):
        from nina_planner.server import load_sequence_from_plan

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.server.get_site_profile",
                AsyncMock(return_value=self._full_profile()),
            ):
                with self.assertRaises(ValueError) as ctx:
                    await load_sequence_from_plan(
                        str(plan_path),
                        frame_type="light",
                        pointing_index=1,
                        mode="invalid",
                    )
        self.assertIn("Unsupported mode", str(ctx.exception))

    # ---- stow_telescope / enter_safety_standby ----

    async def test_stow_telescope_loads_and_starts(self):
        from nina_planner.server import stow_telescope

        with (
            patch(
                "nina_planner.server.get_site_equipment_status",
                AsyncMock(return_value=ObservatoryEquipment()),
            ),
            patch("nina_planner.server._api_post", AsyncMock(return_value={})),
            patch("nina_planner.server._api_get", AsyncMock(return_value={})),
        ):
            result = await stow_telescope()
        self.assertEqual(result, "Teardown sequence started.")

    async def test_enter_safety_standby_loads_and_starts(self):
        from nina_planner.server import enter_safety_standby

        with (
            patch(
                "nina_planner.server.get_site_equipment_status",
                AsyncMock(return_value=ObservatoryEquipment()),
            ),
            patch("nina_planner.server._api_post", AsyncMock(return_value={})),
            patch("nina_planner.server._api_get", AsyncMock(return_value={})),
        ):
            result = await enter_safety_standby()
        self.assertEqual(result, "Safety standby sequence started.")

    # ---- start_sequence / stop_sequence / get_sequence_state ----

    async def test_start_sequence(self):
        from nina_planner.server import start_sequence

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value={}),
        ):
            result = await start_sequence()
        self.assertEqual(result, "Sequence started.")

    async def test_stop_sequence(self):
        from nina_planner.server import stop_sequence

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value={}),
        ):
            result = await stop_sequence()
        self.assertEqual(result, "Sequence stopped.")

    async def test_get_sequence_state_returns_json(self):
        from nina_planner.server import get_sequence_state

        expected = {"Sequence": {"Name": "Root"}}
        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value=expected),
        ):
            result = await get_sequence_state()
        self.assertEqual(result, expected)

    # ---- get_imaging_metadata ----

    async def test_get_imaging_metadata_returns_widened(self):
        import tempfile as _tempfile

        from nina_planner.server import get_imaging_metadata

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias": [{"total_count": 1}],
        }

        with _tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            # Image directory doesn't exist → read_imaging_csv returns [].
            with (
                patch(
                    "nina_planner.server._resolve_imaging_root",
                    AsyncMock(return_value=Path(tmp)),
                ),
                patch("nina_planner.server.read_imaging_csv", return_value=[]),
                patch("nina_planner.server.read_weather_csv", return_value=[]),
            ):
                result = await get_imaging_metadata(
                    str(plan_path), pointing_index=1, image_type="light"
                )
        self.assertEqual(result["plan_id"], "plan-test123-1")
        self.assertEqual(result["target"], "M31")

    # ---- get_nina_time ----

    async def test_get_nina_time_returns_dict(self):
        from nina_planner.server import get_nina_time

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value="2026-09-22T03:00:00"),
        ):
            result = await get_nina_time()
        self.assertIn("nina_time", result)
        self.assertIn("host_time", result)
        self.assertIn("delta_seconds", result)
        self.assertIn("simulated", result)

    async def test_get_nina_time_with_offset(self):
        from nina_planner.server import get_nina_time

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value="2026-09-22T03:00:00-05:00"),
        ):
            result = await get_nina_time()
        # With offset, the timestamp is tz-aware → delta is computed
        # in UTC.
        self.assertIn("delta_seconds", result)


if __name__ == "__main__":
    unittest.main()
