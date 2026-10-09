import asyncio
import base64
import binascii
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from nina_planner.models.camera import CameraDevice
from nina_planner.models.mount import MountDevice
from nina_planner.models.observatory import ObservatoryEquipment
from nina_planner.models.plan import ObservationPlan
from nina_planner.models.profile import (
    EquipmentConfig,
    FilterInfo,
    ObservatoryProfile,
    OpticalTrainInfo,
    SiteLocationInfo,
)
from nina_planner.models.request import SequenceRequest
from nina_planner.server import (
    _check_pointing_index,
    _convert_keys,
    _convert_to_met,
    _log_payload,
    _sanitize_filename,
    _validate_filters,
    _validate_position_angle,
)

_STUB_LAUNCHER_PATHS = (
    Path("/tmp/launch_nina.cmd"),
    r"C:\Users\user\AppData\Local\Temp\launch_nina.cmd",
)


def _find_types(obj: Any, needle: str) -> list[dict]:
    """Every dict in a sequence payload whose $type mentions ``needle``."""
    found: list[dict] = []
    if isinstance(obj, dict):
        if needle in str(obj.get("$type", "")):
            found.append(obj)
        for value in obj.values():
            found.extend(_find_types(value, needle))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_find_types(value, needle))
    return found


def _target_items(payload: dict) -> list[dict]:
    """The direct children of a root sequence's Target area."""
    root = payload["Items"]["$values"]
    target = next(i for i in root if "TargetAreaContainer" in str(i.get("$type", "")))
    return target["Items"]["$values"]


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
        self.assertEqual(
            _sanitize_filename('a/b\\c:d*e?f"g<h>i|j'),
            "a-b-c-d-e-f-g-h-i-j",
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
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "test.log"
            with patch("nina_planner.server.NINA_PLANNER_LOG", str(log_path)):
                _log_payload("Test", "payload")
            content = log_path.read_text()
            self.assertIn("Test", content)
            self.assertIn("payload", content)

    def test_log_payload_falls_back_to_stderr_when_no_env(self):
        # When NINA_PLANNER_LOG is unset, log_payload writes to stderr.
        with patch("nina_planner.server.NINA_PLANNER_LOG", None):
            _log_payload("Test", "payload")  # should not raise

    def test_log_payload_windows_path_relocated_on_posix(self):
        """The sample config's C:/... log path must not build a literal C: tree
        on macOS/WSL — it lands in the system temp dir under the same name."""
        import os

        if os.name == "nt":
            self.skipTest("Windows uses the configured path as-is")
        from nina_planner.server import _log_file

        with patch(
            "nina_planner.server.NINA_PLANNER_LOG",
            "C:/Windows/Temp/nina-planner-debug.log",
        ):
            expected = Path(tempfile.gettempdir()) / "nina-planner-debug.log"
            self.assertEqual(_log_file(), expected)
            _log_payload("Test", "payload")
        self.assertIn("Test", expected.read_text())

    def test_api_probe_reports_liveness_and_reason(self):
        from nina_planner.server import _api_probe

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value="2026-10-07T09:00:00"),
        ):
            self.assertEqual(asyncio.run(_api_probe()), (True, None))

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(side_effect=RuntimeError("HTTP 500: sequencer wedged")),
        ):
            self.assertEqual(
                asyncio.run(_api_probe()), (False, "HTTP 500: sequencer wedged")
            )

    def test_api_post_logs_its_response(self):
        """A POST response is never handed back to the caller, so the debug
        log is its only home — /sequence/load's reply included."""
        import httpx

        from nina_planner.server import _api_post

        response = httpx.Response(
            200,
            json={"Success": True, "Response": {"loaded": True}},
            request=httpx.Request("POST", "http://127.0.0.1:1888/v2/api/sequence/load"),
        )

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, path, json=None):
                return response

        with (
            patch(
                "nina_planner.server.httpx.AsyncClient",
                lambda **kwargs: _FakeClient(),
            ),
            patch("nina_planner.server._log_payload") as log,
        ):
            result = asyncio.run(_api_post("/sequence/load", {"x": 1}))

        self.assertEqual(result, {"loaded": True})
        log.assert_called_once()
        label, payload = log.call_args.args
        self.assertEqual(label, "_api_post:")
        self.assertIn('"loaded": true', payload)

    @staticmethod
    def _api_response(status: int, *, json_body=None, text: str = ""):
        import httpx

        request = httpx.Request("POST", "http://127.0.0.1:1888/v2/api/sequence/load")
        if json_body is not None:
            return httpx.Response(status, json=json_body, request=request)
        return httpx.Response(status, text=text, request=request)

    def test_api_error_surfaces_nina_body_behind_http_status(self):
        """A non-2xx refusal must carry NINA's own reason — httpx's
        raise_for_status would report only the status line."""
        from nina_planner.server import _unwrap_response

        resp = self._api_response(
            400, json_body={"Success": False, "Error": "Sequence is already running"}
        )
        with self.assertRaises(RuntimeError) as ctx:
            _unwrap_response("POST", "/sequence/load", resp)
        message = str(ctx.exception)
        self.assertIn("Sequence is already running", message)
        self.assertIn("POST", message)
        self.assertIn("400", message)

    def test_api_error_surfaces_and_truncates_plain_text_body(self):
        from nina_planner.server import _unwrap_response

        resp = self._api_response(502, text="upstream " + "x" * 600)
        with self.assertRaises(RuntimeError) as ctx:
            _unwrap_response("GET", "/time", resp)
        message = str(ctx.exception)
        self.assertIn("upstream ", message)
        self.assertLess(len(message), 700)

    def test_api_error_on_empty_body_reports_status(self):
        from nina_planner.server import _unwrap_response

        resp = self._api_response(500)
        with self.assertRaisesRegex(
            RuntimeError, "failed with HTTP 500: an empty response body"
        ):
            _unwrap_response("GET", "/time", resp)

    def test_api_200_with_success_false_keeps_nina_error_verbatim(self):
        from nina_planner.server import _unwrap_response

        resp = self._api_response(
            200, json_body={"Success": False, "Error": "Filter 'L' not found"}
        )
        with self.assertRaisesRegex(RuntimeError, "^Filter 'L' not found$"):
            _unwrap_response("GET", "/profile", resp)

    def test_api_200_with_non_json_body_reports_body(self):
        from nina_planner.server import _unwrap_response

        resp = self._api_response(200, text="<html>gateway exploded</html>")
        with self.assertRaises(RuntimeError) as ctx:
            _unwrap_response("GET", "/time", resp)
        self.assertIn("non-JSON", str(ctx.exception))
        self.assertIn("gateway exploded", str(ctx.exception))

    def _make_plan(self, **overrides):
        data = {
            "plan_id": "plan-test",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 5}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 5}],
            "bias_frames": [{"total_count": 5}],
        }
        data.update(overrides)
        return ObservationPlan(**data)

    def _make_profile(self, filters=None, has_rotator=True):
        if filters is None:
            filters = []
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
        filters = [FilterInfo(name="L", position=1, focus_offset=0)]
        profile = self._make_profile(filters=filters)
        plan = self._make_plan()
        # No exception → all filters known. _validate_filters is async.
        asyncio.run(_validate_filters(plan, profile))

    def test_validate_filters_unknown_filter_raises(self):
        filters = [FilterInfo(name="L", position=1, focus_offset=0)]
        profile = self._make_profile(filters=filters)
        plan = self._make_plan(
            light_frames=[
                {
                    "filter_name": "UNKNOWN",
                    "exposure_time_seconds": 60.0,
                    "total_count": 1,
                }
            ]
        )
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(_validate_filters(plan, profile))
        self.assertIn("UNKNOWN", str(ctx.exception))

    def test_validate_filters_includes_reference_filter(self):
        filters = [
            FilterInfo(name="L", position=1, focus_offset=0),
            FilterInfo(name="R", position=2, focus_offset=0),
        ]
        profile = self._make_profile(filters=filters)
        plan = self._make_plan(
            autofocus={"reference_filter_name": "R"},
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat_frames=[
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
        )
        asyncio.run(_validate_filters(plan, profile))

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


class McpToolDirectTest(unittest.IsolatedAsyncioTestCase):
    """Direct-call tests for @mcp.tool() functions in server.py.

    The @mcp.tool() decorator just registers the function with the MCP
    server; the function remains a normal Python callable. Patching the
    helper dependencies (HTTP/subprocess) exercises the tool bodies
    without going through MCP dispatch simulation.
    """

    def _minimal_profile(self):
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
            file_pattern="$$DATEMINUS12$$\\$$IMAGETYPE$$\\$$TARGETNAME$$",
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

    @staticmethod
    def _equipment(
        can_park: bool = True, can_find_home: bool = False, has_cooler: bool = True
    ) -> ObservatoryEquipment:
        """Equipment for the plan-less actions: stowable mount, cooler camera."""
        return ObservatoryEquipment(
            mount=MountDevice(
                connected=True,
                name="m",
                can_park=can_park,
                can_find_home=can_find_home,
            ),
            camera=CameraDevice(
                connected=True, name="cam", can_set_temperature=has_cooler
            ),
        )

    async def test_resolve_imaging_root(self):
        from nina_planner.imaging import windows_to_local
        from nina_planner.server import _resolve_imaging_root

        profile = self._full_profile()
        with patch(
            "nina_planner.server.get_site_profile",
            AsyncMock(return_value=profile),
        ):
            result = await _resolve_imaging_root()
        expected = windows_to_local("C:\\NINA")
        self.assertEqual(result, expected)

    async def test_load_plan_with_relative_path(self):
        from nina_planner.server import _load_plan

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            plan_filename = "subdir_plan.json"
            (project_dir / plan_filename).write_text(json.dumps(plan_dict))
            with patch("nina_planner.server.PROJECT_DIR", project_dir):
                result = await _load_plan(plan_filename)
        self.assertEqual(result.plan_id, "plan-test123")
        self.assertEqual(result.target, "M31")

    async def test_read_metadata_with_image_type(self):
        from nina_planner.server import _read_metadata

        def fake_read(root, image_type):
            return [{"image_type": image_type.upper(), "duration": "60.0"}]

        with (
            patch(
                "nina_planner.server.get_site_profile",
                AsyncMock(return_value=self._full_profile()),
            ),
            patch("nina_planner.server.read_imaging_csv", side_effect=fake_read),
        ):
            rows = await _read_metadata("light")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_type"], "LIGHT")

    async def test_read_metadata_without_image_type(self):
        from nina_planner.server import _read_metadata

        types_seen = []

        def fake_read(root, image_type):
            types_seen.append(image_type)
            return [{"image_type": image_type}]

        with (
            patch(
                "nina_planner.server.get_site_profile",
                AsyncMock(return_value=self._full_profile()),
            ),
            patch("nina_planner.server.read_imaging_csv", side_effect=fake_read),
        ):
            rows = await _read_metadata()
        self.assertEqual(set(types_seen), {"light", "dark", "bias", "flat"})
        self.assertEqual(len(rows), 4)

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
            # GuiderSettings has GuiderName matching the skip set, so has_guider=False.
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

    async def test_list_site_profiles_marks_active(self):
        from nina_planner.server import list_site_profiles

        metas = [
            {
                "Id": "p1",
                "Name": "Backyard",
                "Description": "home",
                "LastUsed": "2026-09-30T02:00:00Z",
            },
            {
                "Id": "p2",
                "Name": "Remote",
                "Description": "",
                "LastUsed": "2026-09-01T02:00:00Z",
            },
        ]
        active = {"Id": "p2", "Name": "Remote"}

        async def fake(path):
            return metas if path == "/profile/show?active=false" else active

        with patch("nina_planner.server._api_get", side_effect=fake):
            result = await list_site_profiles()
        self.assertEqual([p.profile_id for p in result], ["p1", "p2"])
        self.assertFalse(result[0].active)
        self.assertTrue(result[1].active)
        self.assertEqual(result[0].description, "home")
        self.assertIsNone(result[1].description)

    async def test_switch_site_profile_calls_api_with_id(self):
        from nina_planner.server import switch_site_profile

        seen = {}

        async def fake(path):
            seen["path"] = path
            return "Successfully switched profile"

        with patch("nina_planner.server._api_get", side_effect=fake):
            result = await switch_site_profile("p2")
        self.assertEqual(seen["path"], "/profile/switch?profileid=p2")
        self.assertEqual(result, "Successfully switched profile")

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
        # Set has_mount=True but Connected=False → should call connect.
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

    async def test_get_site_equipment_status_connected_device(self):
        from nina_planner.server import _get_site_equipment_status

        profile = self._minimal_profile().model_copy(
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
        raw = {"Mount": {"Connected": True, "Name": "TestMount"}}

        with patch("nina_planner.server._api_get", AsyncMock(return_value=raw)):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsNotNone(equipment.mount)
        self.assertEqual(equipment.mount.name, "TestMount")
        self.assertTrue(equipment.mount.connected)

    async def test_get_events_returns_filtered(self):
        from nina_planner.server import get_events

        events = [{"Time": "2026-09-22T02:55:00Z", "Type": "Info"}]
        time_str = "2026-09-22T03:00:00Z"

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

    async def test_write_plan_file_creates_file(self):
        from nina_planner.server import write_plan_file

        plan = ObservationPlan(
            plan_id="plan-test123",
            target="M31",
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0}],
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat_frames=[
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias_frames=[{"total_count": 1}],
        )

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "sub"
            project_dir.mkdir()
            (project_dir / "progress.md").write_text("")
            with (
                patch("nina_planner.server.PROJECT_DIR", project_dir),
                patch(
                    "nina_planner.server.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
            ):
                result = await write_plan_file(plan)
        self.assertIn("Plan successfully written to", result)
        self.assertTrue(result.endswith(".json"))

    async def test_write_plan_file_auto_fills_plan_id(self):
        from nina_planner.server import write_plan_file

        plan = ObservationPlan(
            plan_id="",
            target="M31_Plan",
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0}],
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat_frames=[
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias_frames=[{"total_count": 1}],
        )

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "sub"
            project_dir.mkdir()
            (project_dir / "progress.md").write_text("")
            with (
                patch("nina_planner.server.PROJECT_DIR", project_dir),
                patch(
                    "nina_planner.server.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
            ):
                result = await write_plan_file(plan)
        self.assertIn("m31_plan", result.lower())
        self.assertTrue(result.endswith(".json"))

    async def test_get_plan_progress_returns_summary(self):
        from nina_planner.server import get_plan_progress

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.server._read_metadata",
                AsyncMock(return_value=[]),
            ):
                result = await get_plan_progress(str(plan_path))
        self.assertEqual(result["plan_id"], "plan-test123")
        self.assertEqual(result["target"], "M31")
        self.assertEqual(result["pointing_count"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["pointings"]), 1)
        self.assertEqual(result["pointings"][0]["plan_id"], "plan-test123-1")
        self.assertIn("light", result["pointings"][0]["frame_types"])
        # Calibration groups carry the window state so a zero remaining can be
        # told apart from a closed window; lights do not.
        dark = result["pointings"][0]["frame_types"]["dark"][0]
        self.assertTrue(dark["window_open"])
        self.assertIn("window_end", dark)
        self.assertNotIn(
            "window_open", result["pointings"][0]["frame_types"]["light"][0]
        )

    def _write_mosaic_source(self, directory: Path, *, pa: float = 0.0, rows: int = 2):
        directory.mkdir(parents=True, exist_ok=True)
        plan_path = directory / "base.json"
        plan_path.write_text(
            json.dumps(
                {
                    "target": "Veil Nebula",
                    "intent": "Widefield supernova remnant",
                    "plan_id": "plan-base123",
                    "pointings": [{"ra_hours": 20.85, "dec_deg": 31.22}],
                    "light_frames": [
                        {
                            "filter_name": "L",
                            "exposure_time_seconds": 60.0,
                            "total_count": 2,
                        }
                    ],
                    "flat_frames": [
                        {
                            "filter_name": "L",
                            "exposure_time_seconds": 5.0,
                            "total_count": 1,
                        }
                    ],
                    "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
                    "bias_frames": [{"total_count": 1}],
                }
            )
        )
        header = (
            "Pane, RA, DEC, Position Angle (East), Pane width (arcmins), "
            "Pane height (arcmins), Overlap, Row, Column"
        )
        lines = [header]
        for i in range(1, rows + 1):
            lines.append(
                f"Pane {i}, 0hr {55 + i:02d}' 00\", 45º 51' 18\", {pa:.2f}, "
                f"309.00, 205.80, 10%, 1, {i}"
            )
        csv_path = directory / "mosaic.csv"
        csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return plan_path, csv_path

    async def test_write_mosaic_plan_creates_multi_pointing_file(self):
        from nina_planner.server import write_mosaic_plan

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            out.mkdir()
            plan_path, csv_path = self._write_mosaic_source(src)
            with (
                patch("nina_planner.server.PROJECT_DIR", out),
                patch(
                    "nina_planner.server.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
            ):
                result = await write_mosaic_plan(str(plan_path), str(csv_path))

            self.assertIn("Mosaic plan successfully written to", result)
            self.assertIn("2 pointing(s)", result)
            written = list(out.glob("*.json"))
            self.assertEqual(len(written), 1)
            data = json.loads(written[0].read_text())
            self.assertEqual(len(data["pointings"]), 2)
            self.assertEqual(data["pointings"][0]["label"], "Pane 1")
            self.assertEqual(data["pointings"][0]["row"], 1)
            self.assertEqual(data["pointings"][1]["column"], 2)
            self.assertNotEqual(data["plan_id"], "plan-base123")
            self.assertTrue(data["plan_id"])

    async def test_write_mosaic_plan_zero_pa_without_rotator_ok(self):
        from nina_planner.server import write_mosaic_plan

        profile = self._full_profile()
        profile = profile.model_copy(
            update={
                "equipment": profile.equipment.model_copy(update={"has_rotator": False})
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            out.mkdir()
            plan_path, csv_path = self._write_mosaic_source(src, pa=0.0)
            with (
                patch("nina_planner.server.PROJECT_DIR", out),
                patch(
                    "nina_planner.server.get_site_profile",
                    AsyncMock(return_value=profile),
                ),
            ):
                result = await write_mosaic_plan(str(plan_path), str(csv_path))
            self.assertIn("2 pointing(s)", result)

    async def test_write_mosaic_plan_nonzero_pa_without_rotator_raises(self):
        from nina_planner.server import write_mosaic_plan

        profile = self._full_profile()
        profile = profile.model_copy(
            update={
                "equipment": profile.equipment.model_copy(update={"has_rotator": False})
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            out.mkdir()
            plan_path, csv_path = self._write_mosaic_source(src, pa=90.0)
            with (
                patch("nina_planner.server.PROJECT_DIR", out),
                patch(
                    "nina_planner.server.get_site_profile",
                    AsyncMock(return_value=profile),
                ),
                self.assertRaises(ValueError) as ctx,
            ):
                await write_mosaic_plan(str(plan_path), str(csv_path))
            self.assertIn("no rotator", str(ctx.exception))

    async def test_get_plan_progress_all_pointings(self):
        from nina_planner.server import get_plan_progress

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [
                {"ra_hours": 5.0, "dec_deg": 10.0, "label": "Pane 1"},
                {"ra_hours": 6.0, "dec_deg": 11.0, "label": "Pane 2"},
            ],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 2}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))
            with patch(
                "nina_planner.server._read_metadata",
                AsyncMock(return_value=[]),
            ):
                result = await get_plan_progress(str(plan_path))

        self.assertEqual(result["pointing_count"], 2)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["pointings"]), 2)
        self.assertEqual(result["pointings"][0]["pointing_index"], 1)
        self.assertEqual(result["pointings"][0]["label"], "Pane 1")
        self.assertEqual(result["pointings"][0]["plan_id"], "plan-test123-1")
        self.assertEqual(result["pointings"][1]["plan_id"], "plan-test123-2")
        self.assertFalse(result["pointings"][0]["complete"])

    async def test_load_sequence_remaining_mode(self):
        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

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
                result = await load_sequence(
                    SequenceRequest(
                        plan=str(plan_path), action="light", pointing_index=1
                    )
                )
        self.assertIn("is complete", result)
        self.assertIn("nothing to load", result)

    async def test_load_sequence_read_metadata_fails(self):
        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

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
                    AsyncMock(side_effect=RuntimeError("imaging dir missing")),
                ),
            ):
                with self.assertRaises(ValueError) as ctx:
                    await load_sequence(
                        SequenceRequest(
                            plan=str(plan_path),
                            action="light",
                            pointing_index=1,
                        )
                    )
        self.assertIn("Cannot compute remaining frames", str(ctx.exception))
        self.assertIn("Pass mode='full'", str(ctx.exception))

    async def test_load_sequence_invalid_mode(self):
        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

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
            ):
                with self.assertRaises(ValueError) as ctx:
                    await load_sequence(
                        SequenceRequest(
                            plan=str(plan_path),
                            action="light",
                            pointing_index=1,
                            mode="invalid",
                        )
                    )
        self.assertIn("Unsupported mode", str(ctx.exception))

    async def test_load_sequence_requires_plan(self):
        """`plan` is optional in the schema, but every action needs one."""
        from nina_planner.server import load_sequence

        with self.assertRaises(ValueError) as ctx:
            await load_sequence(SequenceRequest(action="light"))
        self.assertIn("action='light' requires a plan", str(ctx.exception))
        self.assertIn("request.plan", str(ctx.exception))

    async def test_load_sequence_dark_frame(self):
        from unittest.mock import MagicMock as _MM

        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

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
                    "nina_planner.server.build_sequence_darks",
                    new=_MM(return_value={"$type": "darks"}),
                ),
                patch(
                    "nina_planner.server._api_post",
                    AsyncMock(return_value={}),
                ),
                patch(
                    "nina_planner.server._api_get",
                    AsyncMock(return_value={}),
                ),
            ):
                result = await load_sequence(
                    SequenceRequest(
                        plan=str(plan_path), action="dark", pointing_index=1
                    )
                )
        self.assertIn("`dark` sequence loaded", result)

    async def test_load_sequence_dark_skipped_when_window_closed(self):
        """A plan whose lights are older than `calibration_window_days` cannot
        credit any dark shot now, so `mode="remaining"` must report the darks
        as done rather than re-shoot them forever."""
        from unittest.mock import MagicMock as _MM

        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }
        old_light = {
            "image_type": "LIGHT",
            "filter_name": "L",
            "duration": "60.0",
            "exposure_start": "2020-01-01 20:00:00",
            "file_path": "C:/NINA/LIGHT/M31 (plan-test123-1)__60.00s_0000.fits",
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            darks_builder = _MM(return_value={"$type": "darks"})
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
                    AsyncMock(return_value=[old_light]),
                ),
                patch(
                    "nina_planner.server.get_nina_time",
                    AsyncMock(return_value={"nina_time": "2026-10-05T12:00:00-05:00"}),
                ),
                patch(
                    "nina_planner.server.build_sequence_darks",
                    new=darks_builder,
                ),
                patch(
                    "nina_planner.server._api_get",
                    AsyncMock(return_value={}),
                ),
            ):
                result = await load_sequence(
                    SequenceRequest(
                        plan=str(plan_path), action="dark", pointing_index=1
                    )
                )

        self.assertIn("nothing to load", result)
        darks_builder.assert_not_called()

    async def test_load_sequence_bias_frame(self):
        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            captured_kwargs = {}

            def fake_darks(plan, equipment, bias=False):
                captured_kwargs["bias"] = bias
                return {"$type": "darks"}

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
                    "nina_planner.server.build_sequence_darks",
                    side_effect=fake_darks,
                ),
                patch(
                    "nina_planner.server._api_post",
                    AsyncMock(return_value={}),
                ),
                patch(
                    "nina_planner.server._api_get",
                    AsyncMock(return_value={}),
                ),
            ):
                result = await load_sequence(
                    SequenceRequest(
                        plan=str(plan_path), action="bias", pointing_index=1
                    )
                )
        self.assertTrue(captured_kwargs["bias"])
        self.assertIn("`bias` sequence loaded", result)

    async def test_load_sequence_dawn_flat(self):
        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            captured_kwargs = {}

            def fake_flats(plan, equipment, profile, dusk=False):
                captured_kwargs["dusk"] = dusk
                return {"$type": "flats"}

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
                    "nina_planner.server.build_sequence_flats",
                    side_effect=fake_flats,
                ),
                patch(
                    "nina_planner.server._api_post",
                    AsyncMock(return_value={}),
                ),
                patch(
                    "nina_planner.server._api_get",
                    AsyncMock(return_value={}),
                ),
            ):
                result = await load_sequence(
                    SequenceRequest(
                        plan=str(plan_path), action="dawn_flat", pointing_index=1
                    )
                )
        self.assertFalse(captured_kwargs["dusk"])
        self.assertIn("`dawn_flat` sequence loaded", result)

    async def test_load_sequence_dusk_flat(self):
        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            captured_kwargs = {}

            def fake_flats(plan, equipment, profile, dusk=False):
                captured_kwargs["dusk"] = dusk
                return {"$type": "flats"}

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
                    "nina_planner.server.build_sequence_flats",
                    side_effect=fake_flats,
                ),
                patch(
                    "nina_planner.server._api_post",
                    AsyncMock(return_value={}),
                ),
                patch(
                    "nina_planner.server._api_get",
                    AsyncMock(return_value={}),
                ),
            ):
                result = await load_sequence(
                    SequenceRequest(
                        plan=str(plan_path), action="dusk_flat", pointing_index=1
                    )
                )
        self.assertTrue(captured_kwargs["dusk"])
        self.assertIn("`dusk_flat` sequence loaded", result)

    async def test_load_light_sequence_requires_targetname_file_pattern(self):
        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))
            api_post = AsyncMock(return_value={})

            with (
                patch(
                    "nina_planner.server.get_site_profile",
                    AsyncMock(
                        return_value=self._full_profile().model_copy(
                            update={"file_pattern": None}
                        )
                    ),
                ),
                patch(
                    "nina_planner.server._get_site_equipment_status",
                    AsyncMock(return_value=ObservatoryEquipment()),
                ),
                patch("nina_planner.server._api_post", api_post),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    r"N\.I\.N\.A\. FilePattern must contain \$\$TARGETNAME\$\$",
                ):
                    await load_sequence(
                        SequenceRequest(
                            plan=str(plan_path), action="light", mode="full"
                        )
                    )

            api_post.assert_not_awaited()

    async def test_load_sequence_full_mode(self):
        from unittest.mock import MagicMock as _MM

        from nina_planner.server import load_sequence

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            # In full mode, _read_metadata is NOT called.
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
                    new=_MM(side_effect=AssertionError("should not be called")),
                ),
                patch(
                    "nina_planner.server._api_post",
                    AsyncMock(return_value={}),
                ) as api_post,
                patch(
                    "nina_planner.server._api_get",
                    AsyncMock(return_value={}),
                ) as api_get,
            ):
                result = await load_sequence(
                    SequenceRequest(
                        plan=str(plan_path),
                        action="light",
                        pointing_index=1,
                        mode="full",
                    )
                )
        self.assertIn("`light` sequence loaded", result)
        api_post.assert_awaited_once()
        self.assertEqual(api_post.await_args.args[0], "/sequence/load")
        # Loading is the only API call: no running-state pre-check, no start.
        api_get.assert_not_awaited()

    async def test_start_sequence(self):
        from nina_planner.server import start_sequence

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value={}),
        ) as api_get:
            result = await start_sequence()
        self.assertEqual(result, "Sequence started.")
        api_get.assert_awaited_once_with("/sequence/start?skipValidation=true")

    async def test_load_sequence_stow_loads_without_starting(self):
        from nina_planner.server import load_sequence

        # The schema's own defaults (pointing_index, mode, thresholds) are
        # echoed back by clients and must not count as unused fields.
        with (
            patch(
                "nina_planner.server.get_site_equipment_status",
                AsyncMock(return_value=self._equipment()),
            ),
            patch(
                "nina_planner.server._api_post", AsyncMock(return_value={})
            ) as api_post,
            patch(
                "nina_planner.server._api_get",
                AsyncMock(side_effect=AssertionError("no NINA reads expected")),
            ),
        ):
            result = await load_sequence(SequenceRequest(action="stow"))

        self.assertIn("`stow` sequence loaded", result)
        self.assertIn("start_sequence()", result)
        path, payload = api_post.await_args.args
        self.assertEqual(path, "/sequence/load")
        sent = json.dumps(payload)
        self.assertIn("ParkScope", sent)  # stow parks (or homes) ...
        self.assertIn("WarmCamera", sent)  # ... and then warms

    async def test_load_sequence_park_does_not_warm(self):
        from nina_planner.server import load_sequence

        with (
            patch(
                "nina_planner.server.get_site_equipment_status",
                AsyncMock(return_value=self._equipment()),
            ),
            patch(
                "nina_planner.server._api_post", AsyncMock(return_value={})
            ) as api_post,
        ):
            result = await load_sequence(SequenceRequest(action="park"))

        self.assertIn("`park` sequence loaded", result)
        sent = json.dumps(api_post.await_args.args[1])
        self.assertIn("ParkScope", sent)
        self.assertNotIn("WarmCamera", sent)

    async def test_load_sequence_park_falls_back_to_home(self):
        from nina_planner.server import load_sequence

        equipment = self._equipment(can_park=False, can_find_home=True)
        with (
            patch(
                "nina_planner.server.get_site_equipment_status",
                AsyncMock(return_value=equipment),
            ),
            patch(
                "nina_planner.server._api_post", AsyncMock(return_value={})
            ) as api_post,
        ):
            await load_sequence(SequenceRequest(action="park"))

        sent = json.dumps(api_post.await_args.args[1])
        self.assertIn("FindHome", sent)
        self.assertNotIn("ParkScope", sent)
        self.assertNotIn("WarmCamera", sent)

    async def test_load_sequence_warm_does_not_touch_the_mount(self):
        from nina_planner.server import load_sequence

        with (
            patch(
                "nina_planner.server.get_site_equipment_status",
                AsyncMock(return_value=self._equipment()),
            ),
            patch(
                "nina_planner.server._api_post", AsyncMock(return_value={})
            ) as api_post,
        ):
            result = await load_sequence(SequenceRequest(action="warm"))

        self.assertIn("`warm` sequence loaded", result)
        sent = json.dumps(api_post.await_args.args[1])
        self.assertIn("WarmCamera", sent)
        self.assertNotIn("ParkScope", sent)
        self.assertNotIn("FindHome", sent)

    async def test_planless_actions_reject_plan_only_fields(self):
        """A field the action cannot use is an error, not silently dropped."""
        from nina_planner.server import load_sequence

        for action in ("stow", "park", "warm"):
            with self.subTest(action=action):
                with self.assertRaises(ValueError) as ctx:
                    await load_sequence(
                        SequenceRequest(action=action, plan="plan.json", mode="full")
                    )
                message = str(ctx.exception)
                self.assertIn("does not use", message)
                self.assertIn("plan", message)
                self.assertIn("mode", message)

    async def test_stow_requires_a_stowable_mount(self):
        from nina_planner.server import load_sequence

        equipment = self._equipment(can_park=False, can_find_home=False)
        with patch(
            "nina_planner.server.get_site_equipment_status",
            AsyncMock(return_value=equipment),
        ):
            with self.assertRaises(ValueError) as ctx:
                await load_sequence(SequenceRequest(action="stow"))
        self.assertIn("neither park nor home", str(ctx.exception))
        self.assertIn("nothing to stow", str(ctx.exception))

    async def test_warm_requires_a_camera_with_a_cooler(self):
        from nina_planner.server import load_sequence

        equipment = self._equipment(has_cooler=False)
        with patch(
            "nina_planner.server.get_site_equipment_status",
            AsyncMock(return_value=equipment),
        ):
            with self.assertRaises(ValueError) as ctx:
                await load_sequence(SequenceRequest(action="warm"))
        self.assertIn("nothing to warm", str(ctx.exception))

    # load_sequence with a LIST of requests

    @staticmethod
    def _list_plan_dict(**overrides) -> dict:
        """A four-group plan with two pointings, one per list step."""
        data = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [
                {"ra_hours": 5.0, "dec_deg": 10.0},
                {"ra_hours": 5.5, "dec_deg": 10.5},
            ],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 3}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 3}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 3}],
            "bias_frames": [{"total_count": 3}],
        }
        data.update(overrides)
        return data

    @staticmethod
    def _progress(**remaining: int) -> dict:
        """A plan_progress result with the given remaining count per group."""
        total = 3

        def group(count: int, **fields: Any) -> list[dict]:
            return [
                dict(
                    fields,
                    total_count=total,
                    acquired_count=total - count,
                    remaining_count=count,
                )
            ]

        counts = {"light": 3, "flat": 3, "dark": 3, "bias": 3} | remaining
        return {
            "light": group(
                counts["light"], filter_name="L", exposure_time_seconds=60.0
            ),
            "flat": group(counts["flat"], filter_name="L", exposure_time_seconds=5.0),
            "dark": group(counts["dark"], exposure_time_seconds=60.0),
            "bias": group(counts["bias"]),
        }

    async def _load_list(
        self,
        requests,
        *,
        equipment: ObservatoryEquipment | None = None,
        profile: ObservatoryProfile | None = None,
        progress: Any = None,
    ):
        """Run load_sequence with the usual NINA doubles.

        Returns (result, api_post): every load POSTs through _api_post, so the
        await count is how many times NINA was sent a sequence. ``progress``
        replaces plan_progress — a dict for every entry, or a callable taking
        (plan, rows, pointing_index=...) when entries must differ.
        """
        from nina_planner.server import load_sequence

        api_post = AsyncMock(return_value={})
        with (
            patch(
                "nina_planner.server.get_site_profile",
                AsyncMock(return_value=profile or self._full_profile()),
            ),
            patch(
                "nina_planner.server._get_site_equipment_status",
                AsyncMock(return_value=equipment or self._equipment()),
            ),
            patch("nina_planner.server._read_metadata", AsyncMock(return_value=[])),
            patch("nina_planner.server._api_post", api_post),
        ):
            if progress is None:
                result = await load_sequence(requests)
            else:
                kwargs = (
                    {"side_effect": progress}
                    if callable(progress)
                    else {"return_value": progress}
                )
                with patch("nina_planner.server.plan_progress", **kwargs):
                    result = await load_sequence(requests)
        return result, api_post

    async def test_load_sequence_list_composes_one_sequence(self):
        """One list, one sequence: a single Start/Target/End with one deep-sky
        container per step, in list order, and an End that stows."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            requests = [
                SequenceRequest(plan=str(plan_path), action="light", pointing_index=1),
                SequenceRequest(plan=str(plan_path), action="light", pointing_index=2),
            ]
            result, api_post = await self._load_list(requests)

        self.assertEqual(api_post.await_count, 1, "one POST carries the whole night")
        path, payload = api_post.await_args.args
        self.assertEqual(path, "/sequence/load")

        areas = [item["Name"] for item in payload["Items"]["$values"]]
        self.assertEqual(areas, ["Start Sequence", "Target Sequence", "End Sequence"])

        steps = _target_items(payload)
        self.assertEqual(len(steps), 2, "one container per request, in order")
        self.assertTrue(all("DeepSkyObjectContainer" in s["$type"] for s in steps))
        self.assertIn("(plan-test123-1)", steps[0]["Target"]["TargetName"])
        self.assertIn("(plan-test123-2)", steps[1]["Target"]["TargetName"])

        end_types = [
            i["$type"] for i in payload["Items"]["$values"][2]["Items"]["$values"]
        ]
        self.assertTrue(any("ParkScope" in t for t in end_types), "End parks")
        self.assertTrue(any("WarmCamera" in t for t in end_types), "End warms")

        self.assertIn("request[0]:", result)
        self.assertIn("request[1]:", result)
        self.assertIn("Sequence loaded (2 of 2 step(s))", result)
        self.assertIn("start_sequence()", result)

    async def test_load_sequence_one_element_list_matches_single(self):
        """request=[x] must build byte-identical JSON to request=x."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            request = SequenceRequest(plan=str(plan_path), action="light")

            single, single_post = await self._load_list(request)
            listed, listed_post = await self._load_list([request])

        self.assertEqual(single_post.await_count, 1)
        self.assertEqual(listed_post.await_count, 1)
        self.assertEqual(
            json.dumps(single_post.await_args.args[1]),
            json.dumps(listed_post.await_args.args[1]),
        )
        # Only the summary format differs.
        self.assertIn("`light` sequence loaded", single)
        self.assertIn("Sequence loaded (1 of 1 step(s))", listed)

    async def test_load_sequence_list_skips_completed_entry(self):
        """An entry with nothing left contributes no container and is reported;
        the rest of the night still loads."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            requests = [
                SequenceRequest(plan=str(plan_path), action="light", pointing_index=1),
                SequenceRequest(plan=str(plan_path), action="light", pointing_index=2),
            ]

            def fake_progress(plan, rows, pointing_index=1, **_):
                return self._progress(light=0 if pointing_index == 1 else 3)

            result, api_post = await self._load_list(requests, progress=fake_progress)

        self.assertEqual(api_post.await_count, 1)
        steps = _target_items(api_post.await_args.args[1])
        self.assertEqual(len(steps), 1)
        self.assertIn("(plan-test123-2)", steps[0]["Target"]["TargetName"])

        self.assertIn("request[0]: `light` pointing 1 is complete", result)
        self.assertIn("nothing to load", result)
        self.assertIn("Sequence loaded (1 of 2 step(s))", result)

    async def test_load_sequence_list_all_complete_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            requests = [
                SequenceRequest(plan=str(plan_path), action="light"),
                SequenceRequest(plan=str(plan_path), action="dark"),
            ]
            result, api_post = await self._load_list(
                requests, progress=self._progress(light=0, dark=0)
            )

        api_post.assert_not_awaited()
        self.assertIn("is complete", result)
        self.assertIn("Nothing to load", result)
        self.assertNotIn("start_sequence()", result)

    async def test_load_sequence_list_warm_step_between_targets(self):
        """A warm step sits between two frame steps, and the next plan-driven
        step still cools the camera before it images."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(
                json.dumps(
                    self._list_plan_dict(cooler={"on": True, "setpoint_celsius": -15.0})
                )
            )
            requests = [
                SequenceRequest(plan=str(plan_path), action="light"),
                SequenceRequest(action="warm"),
                SequenceRequest(plan=str(plan_path), action="dark"),
            ]
            result, api_post = await self._load_list(requests)

        self.assertEqual(api_post.await_count, 1)
        payload = api_post.await_args.args[1]
        steps = _target_items(payload)
        self.assertEqual(len(steps), 3)
        self.assertIn("DeepSkyObjectContainer", steps[0]["$type"])
        self.assertIn("WarmCamera", steps[1]["$type"], "warm runs mid-list")
        self.assertIn("DeepSkyObjectContainer", steps[2]["$type"])

        # The warm step warms once here and once in the stow End area, while
        # both frame steps still re-chill to the plan's setpoint.
        warms = _find_types(payload, "WarmCamera")
        self.assertEqual(len(warms), 2, "mid-list step + the stow End area")
        cools = _find_types(payload, "CoolCamera")
        self.assertGreaterEqual(len(cools), 2, "lights and darks both cool")
        self.assertTrue(all(c["Temperature"] == -15.0 for c in cools))
        self.assertIn("start_sequence()", result)

    async def test_load_sequence_list_unpark_step(self):
        """unpark is allowed as a list step (the End area parks behind it) but
        only for a mount that can park."""
        requests = [
            SequenceRequest(action="park"),
            SequenceRequest(action="unpark"),
        ]
        result, api_post = await self._load_list(requests)

        self.assertEqual(api_post.await_count, 1)
        types = [s["$type"] for s in _target_items(api_post.await_args.args[1])]
        self.assertTrue(any("ParkScope" in t for t in types), "park step first")
        self.assertTrue(any("UnparkScope" in t for t in types), "unpark step second")
        self.assertIn("request[1]: `unpark` step", result)

        with self.assertRaises(ValueError) as ctx:
            await self._load_list(
                requests, equipment=self._equipment(can_park=False, can_find_home=True)
            )
        self.assertIn("request[1]:", str(ctx.exception))
        self.assertIn("nothing to unpark", str(ctx.exception))

    async def test_load_sequence_single_unpark_is_rejected(self):
        """As a lone request unpark would finish with the mount unparked and
        nothing running — a list's End area is what makes it safe."""
        from nina_planner.server import load_sequence

        with self.assertRaises(ValueError) as ctx:
            await load_sequence(SequenceRequest(action="unpark"))
        self.assertIn("list step only", str(ctx.exception))
        self.assertIn("unparked and nothing running", str(ctx.exception))

    async def test_load_sequence_list_rejects_stow(self):
        """The composed End area already parks and warms, so stow-in-a-list is
        a caller mistake rather than a redundant step."""
        with self.assertRaises(ValueError) as ctx:
            await self._load_list([SequenceRequest(action="stow")])
        message = str(ctx.exception)
        self.assertIn("cannot be combined", message)
        self.assertIn("End area", message)

    async def test_load_sequence_list_rejects_empty(self):
        with self.assertRaises(ValueError) as ctx:
            await self._load_list([])
        self.assertIn("empty", str(ctx.exception))

    async def test_load_sequence_list_error_names_the_entry(self):
        """A failure in entry i says `request[i]:`, so the agent knows which
        one to fix."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            requests = [
                SequenceRequest(plan=str(plan_path), action="light"),
                SequenceRequest(action="light"),  # no plan
            ]
            with self.assertRaises(ValueError) as ctx:
                await self._load_list(requests)
        message = str(ctx.exception)
        self.assertIn("request[1]:", message)
        self.assertIn("requires a plan", message)

    async def test_stop_sequence(self):
        from nina_planner.server import stop_sequence

        calls: list[str] = []

        async def fake_get(path: str):
            calls.append(path)
            if path == "/sequence/stop":
                return "Sequence stopped"
            # Report running on the first poll, then stopped.
            if calls.count("/sequence/json") == 1:
                return [{"Name": "Root", "Status": "RUNNING"}]
            return [{"Name": "Root", "Status": "CREATED"}]

        with patch("nina_planner.server._api_get", side_effect=fake_get):
            result = await stop_sequence()
        self.assertEqual(result, "Sequence stopped.")
        self.assertIn("/sequence/stop", calls)
        self.assertGreaterEqual(calls.count("/sequence/json"), 2)

    async def test_stop_sequence_when_not_running_does_not_post_stop(self):
        from nina_planner.server import stop_sequence

        calls: list[str] = []

        async def fake_get(path: str):
            calls.append(path)
            return [{"Name": "Root", "Status": "CREATED"}]

        with patch("nina_planner.server._api_get", side_effect=fake_get):
            result = await stop_sequence()
        self.assertEqual(result, "Sequence stopped.")
        self.assertNotIn("/sequence/stop", calls)

    async def test_load_sequence_surfaces_api_error_without_retry(self):
        """No local pre-check and no retry: NINA's own refusal is what propagates."""
        from nina_planner.server import _load_sequence_payload

        api_post = AsyncMock(side_effect=RuntimeError("Sequence is already running"))
        api_get = AsyncMock(return_value=[{"Name": "Root", "Status": "RUNNING"}])

        with (
            patch("nina_planner.server._api_post", api_post),
            patch("nina_planner.server._api_get", api_get),
        ):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                await _load_sequence_payload({"x": 1})
        api_post.assert_awaited_once()
        # The running-state probe is gone from the load path.
        api_get.assert_not_awaited()

    async def test_get_sequence_state_returns_json(self):
        from nina_planner.server import get_sequence_state

        expected = {"Sequence": {"Name": "Root"}}
        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value=expected),
        ):
            result = await get_sequence_state()
        self.assertEqual(result, expected)

    async def test_get_imaging_metadata_returns_widened(self):
        from nina_planner.server import get_imaging_metadata

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

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
        # A naive /time is normalized to an offset-aware value.
        self.assertIsNotNone(datetime.fromisoformat(result["nina_time"]).tzinfo)
        self.assertIsNotNone(datetime.fromisoformat(result["host_time"]).tzinfo)

    async def test_get_nina_time_with_offset(self):
        from nina_planner.server import get_nina_time

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(return_value="2026-09-22T03:00:00-05:00"),
        ):
            result = await get_nina_time()
        self.assertIn("delta_seconds", result)
        # N.I.N.A.'s own offset is preserved.
        self.assertEqual(result["nina_time"], "2026-09-22T03:00:00-05:00")

    async def test_reference_now_drops_offset_keeping_wall_clock(self):
        """N.I.N.A.'s offset is dropped, not converted through the host tz."""
        from nina_planner.server import _reference_now

        clock = {"nina_time": "2026-09-22T03:00:00-05:00"}
        with patch("nina_planner.server.get_nina_time", AsyncMock(return_value=clock)):
            ref = await _reference_now()
        self.assertEqual(ref, datetime(2026, 9, 22, 3, 0, 0))
        self.assertIsNone(ref.tzinfo)

    async def test_reference_now_falls_back_to_host_clock(self):
        from nina_planner.server import _reference_now

        with patch(
            "nina_planner.server.get_nina_time",
            AsyncMock(side_effect=RuntimeError("NINA down")),
        ):
            ref = await _reference_now()
        self.assertIsNone(ref.tzinfo)
        self.assertLess(abs((datetime.now() - ref).total_seconds()), 5)

    async def test_get_nina_status_running_with_api(self):
        """API responsive => running, with the clock populated."""
        from nina_planner.server import get_nina_status

        async def fake_time():
            return {
                "nina_time": "2026-09-22T03:00:00",
                "host_time": "2026-09-22T03:00:01",
                "delta_seconds": -1.0,
                "simulated": False,
            }

        with (
            patch("nina_planner.server.get_nina_time", side_effect=fake_time),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=True,
            ),
        ):
            result = await get_nina_status()

        self.assertTrue(result["running"])
        self.assertTrue(result["lifecycle_control_available"])
        self.assertEqual(result["nina_time"], "2026-09-22T03:00:00")
        self.assertEqual(result["host_time"], "2026-09-22T03:00:01")
        self.assertEqual(result["delta_seconds"], -1.0)
        self.assertFalse(result["simulated"])
        self.assertIsNone(result["probe_error"])
        self.assertIn("NINA running", result["summary"])
        self.assertIn("responsive", result["summary"])

    async def test_get_nina_status_api_down_is_not_running(self):
        """running follows the API: an unreachable REST API means NINA is not
        running, regardless of any local process visibility."""
        from nina_planner.server import get_nina_status

        with (
            patch(
                "nina_planner.server.get_nina_time",
                AsyncMock(side_effect=RuntimeError("connection refused")),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=True,
            ),
        ):
            result = await get_nina_status()

        self.assertFalse(result["running"])
        self.assertTrue(result["lifecycle_control_available"])
        self.assertIsNone(result["nina_time"])
        self.assertIsNone(result["host_time"])
        self.assertIsNone(result["delta_seconds"])
        self.assertIsNone(result["simulated"])
        # NINA's own reason is reported, not flattened to "unresponsive".
        self.assertEqual(result["probe_error"], "connection refused")
        self.assertIn("NINA not running", result["summary"])
        self.assertIn("unresponsive", result["summary"])
        self.assertIn("connection refused", result["summary"])

    async def test_get_nina_status_remote_running(self):
        """API responsive but no Windows interop: running, but lifecycle
        control is unavailable because NINA is remote."""
        from nina_planner.server import get_nina_status

        async def fake_time():
            return {
                "nina_time": "2026-09-22T03:00:00",
                "host_time": "2026-09-22T03:00:00",
                "delta_seconds": 0.0,
                "simulated": False,
            }

        with (
            patch("nina_planner.server.get_nina_time", side_effect=fake_time),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=False,
            ),
        ):
            result = await get_nina_status()

        self.assertTrue(result["running"])
        self.assertFalse(result["lifecycle_control_available"])
        self.assertIn("NINA running", result["summary"])
        self.assertIn("remote", result["summary"])
        self.assertIn("lifecycle control unavailable", result["summary"])

    async def test_get_nina_status_remote_down(self):
        """API unresponsive and no Windows interop: down and not restartable
        from this host."""
        from nina_planner.server import get_nina_status

        with (
            patch(
                "nina_planner.server.get_nina_time",
                AsyncMock(side_effect=RuntimeError("connection refused")),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=False,
            ),
        ):
            result = await get_nina_status()

        self.assertFalse(result["running"])
        self.assertFalse(result["lifecycle_control_available"])
        self.assertIn("NINA not running", result["summary"])
        self.assertIn("cannot be restarted", result["summary"])

    async def test_start_nina_simulated_get_nina_time_fails(self):
        """When get_nina_time() never returns the simulated date during
        capture-check, start_nina restores the host clock and raises.
        """
        from datetime import datetime as _dt

        from nina_planner.server import start_nina

        class FakeProcess:
            returncode = 0
            stdout = ""

        host_calls = {"n": 0, "restored": None}
        local_tz = _dt.fromisoformat("2026-09-26T16:55:00-05:00").tzinfo

        async def fake_read_windows_clock():
            host_calls["n"] += 1
            if host_calls["n"] == 1:
                return _dt.fromisoformat("2026-09-26T16:55:00-05:00")
            if host_calls["restored"] is not None:
                return host_calls["restored"]
            # Until restore is issued, report the shifted target.
            return _dt.fromisoformat("2026-10-15T23:15:00-05:00")

        def fake_run_blocking(argv, timeout=30.0):
            if argv[0] == "powershell.exe":
                command = argv[-1]
                if command != "Set-Date -Date '2026-10-15T23:15:00'":
                    restored_naive = _dt.fromisoformat(command.split("'")[1])
                    host_calls["restored"] = restored_naive.replace(tzinfo=local_tz)
            return FakeProcess()

        async def fake_poll_until_async(check, timeout, interval):
            return await check()

        with (
            patch(
                "nina_planner.server._read_windows_clock",
                side_effect=fake_read_windows_clock,
            ),
            patch(
                "nina_planner.server._run_blocking",
                side_effect=fake_run_blocking,
            ),
            patch(
                "nina_planner.server.write_nina_launcher",
                return_value=_STUB_LAUNCHER_PATHS,
            ),
            patch(
                "nina_planner.server.get_nina_time",
                AsyncMock(side_effect=RuntimeError("shift query failed")),
            ),
            patch(
                "nina_planner.server._nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=True,
            ),
            patch(
                "nina_planner.server._now_local",
                return_value=_dt(2026, 9, 26, 16, 55, tzinfo=local_tz),
            ),
            patch(
                "nina_planner.server._poll_until_async",
                side_effect=fake_poll_until_async,
            ),
            patch("nina_planner.server.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.server._log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await start_nina("2026-10-15T23:15:00")
        # Capture-check fails (NINA never reports simulated date); restore succeeds.
        self.assertIn("NINA never captured simulated date", str(ctx.exception))
        self.assertIsNotNone(host_calls["restored"])

    async def test_get_site_equipment_status_calls_log_payload(self):
        """When get_site_equipment_status runs, it calls _log_payload with
        the equipment dump. Don't mock _log_payload — let it run.
        """
        from nina_planner.server import get_site_equipment_status

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "test.log"
            with (
                patch(
                    "nina_planner.server.NINA_PLANNER_LOG",
                    str(log_path),
                ),
                patch(
                    "nina_planner.server._api_get",
                    AsyncMock(return_value={}),
                ),
            ):
                await get_site_equipment_status()
            content = log_path.read_text()
            self.assertIn("Equipment:", content)

    async def test_start_nina_simulated_progress_entry_fails(self):
        """When append_progress_entry raises in start_nina simulated path, the tool
        still returns successfully — the except branch swallows the error.
        """
        from datetime import datetime as _dt

        from nina_planner.server import start_nina

        class FakeProcess:
            returncode = 0
            stdout = ""

        host_calls = {"n": 0}

        async def fake_read_windows_clock():
            host_calls["n"] += 1
            # 1) real0, 2) shifted target (shift-check), 3+) restored target.
            if host_calls["n"] == 1:
                return _dt.fromisoformat("2026-09-26T16:55:00-05:00")
            if host_calls["n"] == 2:
                return _dt.fromisoformat("2026-10-15T23:15:00-05:00")
            return _dt.fromisoformat("2026-09-26T16:55:01-05:00")

        async def fake_get_nina_time():
            return {
                "nina_time": "2026-10-15T23:15:00",
                "host_time": "2026-09-26T16:55:00",
                "delta_seconds": 17_328_000.0,
                "simulated": True,
            }

        with (
            patch(
                "nina_planner.server._read_windows_clock",
                side_effect=fake_read_windows_clock,
            ),
            patch(
                "nina_planner.server._run_blocking",
                return_value=FakeProcess(),
            ),
            patch(
                "nina_planner.server.write_nina_launcher",
                return_value=_STUB_LAUNCHER_PATHS,
            ),
            patch(
                "nina_planner.server.get_nina_time",
                AsyncMock(side_effect=fake_get_nina_time),
            ),
            patch(
                "nina_planner.server._nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=True,
            ),
            patch(
                "nina_planner.server.append_progress_entry",
                AsyncMock(side_effect=OSError("disk full")),
            ),
            patch(
                "nina_planner.server._now_local",
                return_value=_dt.fromisoformat("2026-09-26T16:55:00-05:00"),
            ),
            patch("nina_planner.server.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.server._log_payload"),
        ):
            # Should not raise — the progress-entry failure is swallowed.
            result = await start_nina("2026-10-15T23:15:00")
        self.assertEqual(result["simulated"], True)

    async def test_start_nina_real_time_progress_entry_fails(self):
        """When append_progress_entry raises in start_nina real-time, the tool
        still returns — the except branch swallows the error.
        """
        from nina_planner.server import start_nina

        class FakeProcess:
            returncode = 0
            stdout = ""

        stub_launcher_paths = (
            Path("/tmp/launch_nina.cmd"),
            r"C:\Users\user\AppData\Local\Temp\launch_nina.cmd",
        )

        with (
            patch(
                "nina_planner.server._run_blocking",
                return_value=FakeProcess(),
            ),
            patch(
                "nina_planner.server.write_nina_launcher",
                return_value=stub_launcher_paths,
            ),
            patch(
                "nina_planner.server._nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=True,
            ),
            patch(
                "nina_planner.server.append_progress_entry",
                AsyncMock(side_effect=OSError("disk full")),
            ),
            patch("nina_planner.server._log_payload"),
        ):
            # Should not raise — the progress-entry failure is swallowed.
            result = await start_nina(None)
        self.assertEqual(result["simulated"], False)

    async def test_start_nina_already_running_raises(self):
        from nina_planner.server import start_nina

        with (
            patch(
                "nina_planner.server._nina_api_responsive",
                AsyncMock(return_value=True),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=True,
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await start_nina(None)
        self.assertIn("already running", str(ctx.exception))

    async def test_start_nina_remote_unavailable_raises(self):
        """API down and no Windows interop: cannot launch a remote NINA."""
        from nina_planner.server import start_nina

        with (
            patch(
                "nina_planner.server._nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=False,
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await start_nina(None)
        self.assertIn("cannot be launched", str(ctx.exception))

    async def test_stop_nina_api_down_is_noop(self):
        from nina_planner.server import stop_nina

        with (
            patch(
                "nina_planner.server._api_probe",
                AsyncMock(return_value=(False, "connection refused")),
            ),
            patch("nina_planner.server._log_payload"),
        ):
            result = await stop_nina()
        self.assertFalse(result["stopped"])
        self.assertIn("not running", result["summary"])
        self.assertIn("Probe error: connection refused", result["summary"])

    async def test_stop_nina_remote_unavailable_raises(self):
        from nina_planner.server import stop_nina

        with (
            patch(
                "nina_planner.server._api_probe",
                AsyncMock(return_value=(True, None)),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=False,
            ),
            patch("nina_planner.server._log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await stop_nina()
        self.assertIn("remote host", str(ctx.exception))

    async def test_stop_nina_terminates_local(self):
        from nina_planner.server import stop_nina

        class FakeProcess:
            returncode = 0
            stdout = ""

        with (
            patch(
                "nina_planner.server._api_probe",
                AsyncMock(return_value=(True, None)),
            ),
            patch(
                "nina_planner.server.windows_interop_available",
                return_value=True,
            ),
            patch(
                "nina_planner.server._run_blocking",
                AsyncMock(return_value=FakeProcess()),
            ),
            patch(
                "nina_planner.server.append_progress_entry",
                AsyncMock(),
            ),
            patch("nina_planner.server.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.server._log_payload"),
        ):
            result = await stop_nina()
        self.assertTrue(result["stopped"])
        self.assertIn("terminated", result["summary"])

    async def test_get_site_equipment_status_missing_device_data(self):
        """When a device is marked has_X=True but the raw response has no
        entry for it (empty dict), _build returns None without trying to
        construct the model."""
        from nina_planner.server import _get_site_equipment_status

        profile = self._minimal_profile().model_copy(
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
        # Raw doesn't include "Mount" at all → _build returns None.
        raw = {}

        with patch("nina_planner.server._api_get", AsyncMock(return_value=raw)):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsNone(equipment.mount)


class ScreenshotDashboardTest(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the screenshot_dashboard MCP tool."""

    # Minimal valid 1x1 RGBA PNG, used as the stand-in screenshot payload.
    _PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQG"
        "AhKmMIQAAAABJRU5ErkJggg=="
    )

    def setUp(self):
        # Screenshot output is redirected to a throwaway directory per test.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_dir = Path(self._tmp.name)
        patcher = patch("nina_planner.server.SCREENSHOT_DIR", self.out_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Unit tests do not need to wait on NINA's repaint.
        settle = patch("nina_planner.server.SCREENSHOT_SETTLE_SECONDS", 0.0)
        settle.start()
        self.addCleanup(settle.stop)

    def _payload(self) -> str:
        return base64.b64encode(self._PNG).decode()

    def _unpack(self, result):
        """screenshot_dashboard returns ``[path, Image]``; split and check it."""
        from mcp.server.fastmcp import Image

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        path, image = result
        self.assertIsInstance(path, str)
        self.assertIsInstance(image, Image)
        return path, image

    def test_tool_has_docstring(self):
        from nina_planner.server import screenshot_dashboard

        self.assertTrue(screenshot_dashboard.__doc__)

    async def test_returns_no_structured_output_schema(self):
        # The tool carries both a text and an image block, which cannot be
        # expressed as structured output, so it registers unstructured.
        from nina_planner.server import mcp

        tool = mcp._tool_manager.get_tool("screenshot_dashboard")
        self.assertIsNotNone(tool)
        self.assertIsNone(tool.output_schema)

    async def test_content_blocks_are_path_text_then_image(self):
        from mcp.types import ImageContent, TextContent

        from nina_planner.server import mcp

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(side_effect=["imaging tab switched", self._payload()]),
        ):
            blocks = await mcp._tool_manager.call_tool(
                "screenshot_dashboard", {}, convert_result=True
            )

        self.assertEqual(len(blocks), 2)
        text, image = blocks
        self.assertIsInstance(text, TextContent)
        self.assertEqual(Path(text.text).parent, self.out_dir)
        self.assertIsInstance(image, ImageContent)
        self.assertEqual(image.mimeType, "image/png")
        self.assertEqual(base64.b64decode(image.data), self._PNG)

    async def test_writes_png_and_returns_path_and_image(self):
        from nina_planner.server import screenshot_dashboard

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(side_effect=["imaging tab switched", self._payload()]),
        ):
            result = await screenshot_dashboard()

        path, image = self._unpack(result)
        self.assertEqual(image.data, self._PNG)
        path = Path(path)
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.suffix, ".png")
        self.assertTrue(path.name.startswith("dashboard_"))
        self.assertEqual(path.parent, self.out_dir)
        self.assertTrue(path.is_file())
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(data, self._PNG)

    async def test_waits_for_tab_repaint_before_capture(self):
        from nina_planner.server import screenshot_dashboard

        calls = []

        async def fake_api(path):
            calls.append(path)
            return self._payload()

        async def fake_sleep(seconds):
            calls.append(("sleep", seconds))

        with (
            patch("nina_planner.server._api_get", side_effect=fake_api),
            patch("nina_planner.server.SCREENSHOT_SETTLE_SECONDS", 0.5),
            patch("nina_planner.server.anyio.sleep", fake_sleep),
        ):
            await screenshot_dashboard()

        self.assertEqual(
            calls,
            [
                "/application/switch-tab?tab=imaging",
                ("sleep", 0.5),
                "/application/screenshot",
            ],
        )

    async def test_creates_screenshot_directory(self):
        from nina_planner.server import screenshot_dashboard

        missing = self.out_dir / "nested"
        with patch("nina_planner.server.SCREENSHOT_DIR", missing):
            with patch(
                "nina_planner.server._api_get",
                AsyncMock(side_effect=["imaging tab switched", self._payload()]),
            ):
                await screenshot_dashboard()

        self.assertTrue(missing.is_dir())

    async def test_each_call_returns_a_new_file(self):
        from nina_planner.server import screenshot_dashboard

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(
                side_effect=[
                    "imaging tab switched",
                    self._payload(),
                    "imaging tab switched",
                    self._payload(),
                ]
            ),
        ):
            first = self._unpack(await screenshot_dashboard())[0]
            second = self._unpack(await screenshot_dashboard())[0]

        self.assertNotEqual(first, second)
        self.assertTrue(Path(first).is_file())
        self.assertTrue(Path(second).is_file())

    async def test_switches_tab_before_capture(self):
        from nina_planner.server import screenshot_dashboard

        seen = []

        async def fake(path):
            seen.append(path)
            return self._payload()

        with patch("nina_planner.server._api_get", side_effect=fake):
            path = self._unpack(await screenshot_dashboard())[0]

        self.assertEqual(
            seen,
            ["/application/switch-tab?tab=imaging", "/application/screenshot"],
        )
        self.assertTrue(Path(path).is_file())

    async def test_propagates_api_failure(self):
        from nina_planner.server import screenshot_dashboard

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(side_effect=RuntimeError("API request failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "API request failed"):
                await screenshot_dashboard()

        self.assertEqual(list(self.out_dir.iterdir()), [])

    async def test_invalid_base64_raises(self):
        from nina_planner.server import screenshot_dashboard

        with patch(
            "nina_planner.server._api_get",
            AsyncMock(side_effect=["imaging tab switched", "not-base64!!"]),
        ):
            with self.assertRaises(binascii.Error):
                await screenshot_dashboard()

        # Decoding happens before anything is written, so no partial file is left.
        self.assertEqual(list(self.out_dir.iterdir()), [])


class AsyncMockSleep:
    """Async sleep stand-in that returns immediately."""

    async def __call__(self, *_args, **_kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
