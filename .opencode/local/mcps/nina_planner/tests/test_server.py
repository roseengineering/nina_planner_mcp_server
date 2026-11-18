import asyncio
import json
import tempfile
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from nina_planner.client import log_payload as _log_payload
from nina_planner.models.camera import CameraDevice
from nina_planner.models.flat_device import FlatDevice
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
from nina_planner.plan_utils import (
    check_pointing_index as _check_pointing_index,
)
from nina_planner.plan_utils import (
    sanitize_filename as _sanitize_filename,
)
from nina_planner.plan_utils import (
    validate_filters as _validate_filters,
)
from nina_planner.plan_utils import (
    validate_position_angle as _validate_position_angle,
)
from nina_planner.tools.equipment import _convert_keys, _convert_to_met


def _target_items(payload: dict) -> list[dict]:
    """The direct children of a root sequence's Target area."""
    root = payload["Items"]["$values"]
    target = next(i for i in root if "TargetAreaContainer" in str(i.get("$type", "")))
    return target["Items"]["$values"]


def _end_park_items(payload: dict) -> list[dict]:
    """The teardown instructions of a root sequence's End area.

    The End area itself holds exactly one child — the ``Park Telescope``
    container — and the wait, connect, park/home and warm instructions live
    one level deeper inside it.
    """
    root = payload["Items"]["$values"]
    end = next(i for i in root if "EndAreaContainer" in str(i.get("$type", "")))
    park = end["Items"]["$values"]
    if len(park) != 1 or park[0].get("Name") != "Park Telescope":
        raise AssertionError(
            f"End area must hold one 'Park Telescope' container, got "
            f"{[i.get('Name') for i in park]}"
        )
    return park[0]["Items"]["$values"]


class ConvertToMetTest(unittest.TestCase):
    def _now(self) -> datetime:
        return datetime(2026, 9, 22, 3, 0, 0, tzinfo=UTC)

    def _assert_local_instant(self, rendered: str, utc: str):
        """``rendered`` is the local-zone display of the UTC instant ``utc``.

        The window filter runs on instants, so assert instants rather than
        literal strings — the latter would bake in the timezone of whatever
        machine runs the suite.
        """
        self.assertIsNotNone(
            datetime.fromisoformat(rendered).tzinfo,
            f"rendered timestamp must carry an offset: {rendered!r}",
        )
        self.assertEqual(
            datetime.fromisoformat(rendered),
            datetime.fromisoformat(utc.replace("Z", "+00:00")),
        )

    def test_utc_z_seconds(self):
        data = [{"Time": "2026-09-22T02:16:25Z"}]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
        self._assert_local_instant(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    def test_offset_shifted_to_utc_z(self):
        data = [{"Time": "2026-09-21T21:16:25-05:00"}]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
        self._assert_local_instant(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    def test_microseconds_stripped(self):
        data = [{"Time": "2026-09-22T02:16:25.123456Z"}]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
        self._assert_local_instant(res[0]["timestamp"], "2026-09-22T02:16:25Z")
        # Sub-second precision is gone, not carried into the display.
        self.assertNotIn(".", res[0]["timestamp"].split("T")[1].rsplit("-", 1)[0])

    def test_naive_uses_now_tzinfo(self):
        data = [{"Time": "2026-09-22 02:16:25"}]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
        self._assert_local_instant(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    def test_naive_now_tagged_local_with_z_events(self):
        """A naive `now` is tagged host-local so it can be compared against
        offset-aware event timestamps without raising."""
        data = [{"Time": "2026-09-22T03:00:00Z"}]
        res = _convert_to_met(
            data, since=86400, name="Time", now=datetime(2026, 9, 22, 3, 0, 0)
        )
        self.assertEqual(len(res), 1)
        self.assertEqual(
            datetime.fromisoformat(res[0]["timestamp"]),
            datetime.fromisoformat("2026-09-22T03:00:00Z"),
        )

    def test_aware_now_tags_naive_events(self):
        data = [{"Time": "2026-09-22 03:00:00"}]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
        self.assertEqual(len(res), 1)
        self._assert_local_instant(res[0]["timestamp"], "2026-09-22T03:00:00Z")

    def test_now_defaults_to_host_clock(self):
        """Omitting `now` anchors the window on the host clock, so a recent
        event survives and an old one does not."""
        now = datetime.now(UTC).replace(microsecond=0)
        recent = [{"Time": (now - timedelta(seconds=300)).isoformat()}]
        stale = [{"Time": (now - timedelta(seconds=7200)).isoformat()}]

        res = _convert_to_met(recent, since=3600, name="Time")
        self.assertEqual(len(res), 1)

        res = _convert_to_met(stale, since=3600, name="Time")
        self.assertEqual(res, [])

    def test_old_entries_filtered(self):
        data = [{"Time": "2026-09-20T00:00:00Z"}]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
        self.assertEqual(res, [])

    def test_missing_timestamp_record_dropped(self):
        # A record without the timestamp field cannot be sorted or windowed;
        # it is dropped instead of crashing the whole listing.
        data = [
            {"Time": "2026-09-22T02:16:25Z", "Message": "good"},
            {"Message": "no timestamp field"},
        ]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["message"], "good")

    def test_unusable_timestamp_record_dropped(self):
        data = [
            {"Time": "not-a-date", "Message": "bad"},
            {"Time": None, "Message": "null"},
            {"Time": "", "Message": "empty"},
            {"Time": 1758519385, "Message": "not-a-string"},
            {"Time": "2026-09-22T02:16:25Z", "Message": "good"},
        ]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["message"], "good")

    def test_all_records_dropped_returns_empty(self):
        data = [{"Message": "orphan"}]
        res = _convert_to_met(data, since=3600, name="Time", now=self._now())
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
            with patch("nina_planner.config.NINA_PLANNER_LOG", str(log_path)):
                _log_payload("Test", "payload")
            content = log_path.read_text()
            self.assertIn("Test", content)
            self.assertIn("payload", content)

    def test_log_payload_is_a_noop_when_no_env(self):
        # When NINA_PLANNER_LOG is unset, log_payload silently returns —
        # it never writes to stderr or anywhere else (client.py log_payload:
        # `if path is None: return`).
        import io
        from contextlib import redirect_stderr

        buf = io.StringIO()
        with (
            patch("nina_planner.config.NINA_PLANNER_LOG", None),
            redirect_stderr(buf),
        ):
            _log_payload("Test", "payload")  # should not raise
        self.assertEqual(buf.getvalue(), "")

    def test_log_payload_windows_path_relocated_on_posix(self):
        """The sample config's C:/... log path must not build a literal C: tree
        on macOS/WSL — it lands in the system temp dir under the same name."""
        import os

        if os.name == "nt":
            self.skipTest("Windows uses the configured path as-is")
        from nina_planner.client import _log_file as _log_file

        with patch(
            "nina_planner.config.NINA_PLANNER_LOG",
            "C:/Windows/Temp/nina-planner-debug.log",
        ):
            expected = Path(tempfile.gettempdir()) / "nina-planner-debug.log"
            self.assertEqual(_log_file(), expected)
            _log_payload("Test", "payload")
        self.assertIn("Test", expected.read_text())

    def test_log_payload_swallows_an_unwritable_log_path(self):
        """Debug logging runs AFTER the real work succeeded — an unwritable
        NINA_PLANNER_LOG must never fail the read-only tool that just did
        its job. Both failure modes are OSError: a directory where the log
        FILE should be (open fails), and a parent that is not a directory
        (mkdir fails)."""
        with tempfile.TemporaryDirectory() as tmp:
            # The log path IS a directory: append-open raises
            # IsADirectoryError.
            with patch("nina_planner.config.NINA_PLANNER_LOG", tmp):
                _log_payload("Test", "payload")  # must not raise

        with tempfile.TemporaryDirectory() as tmp:
            # The log's parent is a FILE: mkdir(parents=True) raises.
            blocker = Path(tmp) / "not-a-directory"
            blocker.write_text("x")
            log_path = blocker / "nested" / "nina-planner-debug.log"
            with patch("nina_planner.config.NINA_PLANNER_LOG", str(log_path)):
                _log_payload("Test", "payload")  # must not raise
            self.assertFalse(log_path.exists())

    def test_api_probe_reports_liveness_and_reason(self):
        from nina_planner.client import api_probe as _api_probe

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value="2026-10-07T09:00:00"),
        ):
            self.assertEqual(asyncio.run(_api_probe()), (True, None))

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(side_effect=RuntimeError("HTTP 500: sequencer wedged")),
        ):
            self.assertEqual(
                asyncio.run(_api_probe()), (False, "HTTP 500: sequencer wedged")
            )

    def test_api_post_logs_its_response(self):
        """A POST response is never handed back to the caller, so the debug
        log is its only home — /sequence/load's reply included."""
        import httpx

        from nina_planner.client import api_post as _api_post

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
                "nina_planner.client.httpx.AsyncClient",
                lambda **kwargs: _FakeClient(),
            ),
            patch("nina_planner.client.log_payload") as log,
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
        from nina_planner.client import unwrap_response as _unwrap_response

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
        from nina_planner.client import unwrap_response as _unwrap_response

        resp = self._api_response(502, text="upstream " + "x" * 600)
        with self.assertRaises(RuntimeError) as ctx:
            _unwrap_response("GET", "/time", resp)
        message = str(ctx.exception)
        self.assertIn("upstream ", message)
        self.assertLess(len(message), 700)

    def test_api_error_on_empty_body_reports_status(self):
        from nina_planner.client import unwrap_response as _unwrap_response

        resp = self._api_response(500)
        with self.assertRaisesRegex(
            RuntimeError, "failed with HTTP 500: an empty response body"
        ):
            _unwrap_response("GET", "/time", resp)

    def test_api_200_with_success_false_keeps_nina_error_verbatim(self):
        from nina_planner.client import unwrap_response as _unwrap_response

        resp = self._api_response(
            200, json_body={"Success": False, "Error": "Filter 'L' not found"}
        )
        with self.assertRaisesRegex(RuntimeError, "^Filter 'L' not found$"):
            _unwrap_response("GET", "/profile", resp)

    def test_api_200_with_non_json_body_reports_body(self):
        from nina_planner.client import unwrap_response as _unwrap_response

        resp = self._api_response(200, text="<html>gateway exploded</html>")
        with self.assertRaises(RuntimeError) as ctx:
            _unwrap_response("GET", "/time", resp)
        self.assertIn("non-JSON", str(ctx.exception))
        self.assertIn("gateway exploded", str(ctx.exception))

    def test_api_200_success_false_without_error_reports_full_context(self):
        # NINA always pairs Success:false with an Error string; a body that
        # does not still must not read as a success, and the reported error
        # must carry method, URL, status and body either way.
        from nina_planner.client import unwrap_response as _unwrap_response

        resp = self._api_response(200, json_body={"Success": False, "Response": ""})
        with self.assertRaises(RuntimeError) as ctx:
            _unwrap_response("GET", "/time", resp)
        message = str(ctx.exception)
        self.assertIn("GET", message)
        self.assertIn("/time", message)
        self.assertIn("200", message)
        self.assertIn("Success=false", message)
        self.assertNotEqual(message, "API request failed")

    def test_api_200_non_object_json_body_reports_full_context(self):
        # The sibling shape of the same branch: HTTP 200 whose body is JSON
        # but not an envelope object — also contextual, never bare.
        from nina_planner.client import unwrap_response as _unwrap_response

        resp = self._api_response(200, json_body=["not", "an", "envelope"])
        with self.assertRaises(RuntimeError) as ctx:
            _unwrap_response("POST", "/sequence/load", resp)
        message = str(ctx.exception)
        self.assertIn("POST", message)
        self.assertIn("/sequence/load", message)
        self.assertIn("200", message)
        self.assertIn("non-object JSON body", message)

    def test_run_blocking_runs_a_real_subprocess(self):
        import sys

        from nina_planner.client import run_blocking as _run_blocking

        result = asyncio.run(_run_blocking([sys.executable, "-c", "print('ok')"]))
        self.assertEqual(result.returncode, 0)
        self.assertIn("ok", result.stdout)

    def test_stop_running_sequence_and_wait_times_out(self):
        from nina_planner.client import stop_running_sequence_and_wait as _stop_wait

        # NINA keeps reporting RUNNING past the deadline: the wait must give
        # up loudly (the caller then knows a load would be rejected) instead
        # of polling forever.
        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value={"Status": "RUNNING"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not stop within 0s"):
                asyncio.run(_stop_wait(timeout=0.01))

    def _make_plan(self, **overrides):
        data = {
            "plan_id": "plan-test",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 5}],
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
            # Explicit rather than defaulted: a lights load rejects a rig
            # with no plate solver, and this factory feeds positive
            # validation tests (negative ones override it).
            plate_solver="ASTAP",
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
                has_flat_device=False,
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
            flat_frames=[{"filter_name": "L", "total_count": 1}],
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
            # Kept explicit: nothing about this rig is assumed. It reports no
            # solver, no guider and no focuser, so a light load built from it
            # is rejected on all three counts (the solver first).
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
                has_flat_device=False,
            ),
        )

    def _full_profile(self, filters=None, has_flat_device: bool = False):
        """The usual rig for load tests: filter wheel with a `Dark` slot, so
        the default (wheel) dark/bias variant passes its capability check.

        `filters` overrides the wheel contents for tests that need a
        different one (a profile with no `Dark` slot, say), and
        `has_flat_device` turns on the flat panel the `cover` variant needs.
        """
        if filters is None:
            filters = [
                FilterInfo(name="Dark", position=0),
                FilterInfo(name="L", position=1, focus_offset=0),
            ]
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
            # A lights load refuses a rig with no plate solver (every lights
            # step emits a slew/center), so the usual load-test rig reports
            # one: negative tests take it away explicitly.
            plate_solver="ASTAP",
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
                has_flat_device=has_flat_device,
            ),
        )

    @staticmethod
    def _equipment(
        can_park: bool = True, can_find_home: bool = False, has_cooler: bool = True
    ) -> ObservatoryEquipment:
        """Equipment for the plan-less actions: parkable mount, cooler camera."""
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

    @staticmethod
    def _payload_equipment() -> ObservatoryEquipment:
        """Equipment for the payload tests: the parkable mount and nothing else.

        load_sequence only sends a sequence a mount can actually park — every
        list ends with the End-area park — so a bare ``ObservatoryEquipment()``
        (no mount reported) is now rejected at load. These tests are about what
        the plan builds into the payload, so they carry just the mount the
        guard needs: no camera, so no cool/warm step is added to what they
        count.
        """
        return ObservatoryEquipment(
            mount=MountDevice(connected=True, name="m", can_park=True)
        )

    async def test_resolve_imaging_root(self):
        from nina_planner.imaging import windows_to_local
        from nina_planner.metadata import resolve_imaging_root as _resolve_imaging_root

        profile = self._full_profile()
        with patch(
            "nina_planner.tools.equipment.get_site_profile",
            AsyncMock(return_value=profile),
        ):
            result = await _resolve_imaging_root()
        expected = windows_to_local("C:\\NINA")
        self.assertEqual(result, expected)

    async def test_load_plan_with_relative_path(self):
        from nina_planner.plan_utils import load_plan as _load_plan

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            plan_filename = "subdir_plan.json"
            (project_dir / plan_filename).write_text(json.dumps(plan_dict))
            with patch("nina_planner.config.PROJECT_DIR", project_dir):
                result = await _load_plan(plan_filename)
        self.assertEqual(result.plan_id, "plan-test123")
        self.assertEqual(result.target, "M31")

    async def test_read_metadata_with_image_type(self):
        from nina_planner.metadata import read_metadata as _read_metadata

        def fake_read(root, image_type):
            return [{"image_type": image_type.upper(), "duration": "60.0"}]

        with (
            patch(
                "nina_planner.tools.equipment.get_site_profile",
                AsyncMock(return_value=self._full_profile()),
            ),
            patch("nina_planner.metadata.read_imaging_csv", side_effect=fake_read),
        ):
            rows = await _read_metadata("light")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_type"], "LIGHT")

    async def test_read_metadata_without_image_type(self):
        """One call, not four: image_type=None lets read_imaging_csv walk once."""
        from nina_planner.metadata import read_metadata as _read_metadata

        calls = []

        def fake_read(root, image_type=None):
            calls.append(image_type)
            return [{"image_type": "LIGHT"}]

        with (
            patch(
                "nina_planner.tools.equipment.get_site_profile",
                AsyncMock(return_value=self._full_profile()),
            ),
            patch("nina_planner.metadata.read_imaging_csv", side_effect=fake_read),
        ):
            rows = await _read_metadata()
        self.assertEqual(calls, [None])
        self.assertEqual(len(rows), 1)

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
            # GuiderSettings has Direct_Guider (Mount Dither), so has_guider=True.
            "GuiderSettings": {"GuiderName": "Direct_Guider"},
            "RotatorSettings": {"Id": "Rot1"},
            "DomeSettings": {},
            "WeatherDataSettings": {},
            "SafetyMonitorSettings": {},
            "FocuserSettings": {"Id": "Foc1"},
            "SwitchSettings": {},
        }
        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            result = await get_site_profile()
        self.assertEqual(result.profile_name, "Test")
        self.assertEqual(result.optics.focal_length_mm, 600)
        self.assertTrue(result.equipment.has_mount)
        self.assertTrue(result.equipment.has_camera)
        self.assertTrue(result.equipment.has_guider)
        self.assertEqual(result.guider_name, "Direct_Guider")

    async def test_get_site_profile_no_guider(self):
        from nina_planner.server import get_site_profile

        raw = {
            "Name": "Test",
            "Id": "abc",
            "Description": "test profile",
            "AstrometrySettings": {},
            "TelescopeSettings": {},
            "CameraSettings": {},
            "FilterWheelSettings": {"FilterWheelFilters": []},
            "ImageFileSettings": {"FilePath": "C:\\NINA", "FilePattern": None},
            "PlateSolveSettings": {},
            "FramingAssistantSettings": {},
            "GuiderSettings": {"GuiderName": "No_Guider"},
            "RotatorSettings": {},
            "DomeSettings": {},
            "WeatherDataSettings": {},
            "SafetyMonitorSettings": {},
            "FocuserSettings": {},
            "SwitchSettings": {},
        }
        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            result = await get_site_profile()
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
        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            result = await get_site_profile()
        self.assertEqual(len(result.filters), 2)
        self.assertEqual(result.filters[0].name, "L")
        self.assertEqual(result.filters[0].position, 1)
        self.assertEqual(result.filters[1].name, "R")

    async def test_get_site_profile_bad_filter_fields_do_not_crash(self):
        from nina_planner.server import get_site_profile

        filters = [
            {"Name": "L", "Position": 1, "FocusOffset": 0},
            # Position missing → row dropped, not thrown.
            {"Name": "R", "FocusOffset": 1},
            # Position unparseable → dropped too.
            {"Name": "G", "Position": "slot-two", "FocusOffset": 2},
            # Position as a numeric string is tolerated.
            {"Name": "OIII", "Position": "3", "FocusOffset": "0"},
            # Only FocusOffset bad → kept, with focus_offset=None.
            {"Name": "B", "Position": 4, "FocusOffset": "n/a"},
            # Null entry → ignored.
            None,
            # No name → ignored (pre-existing rule).
            {"Position": 5, "FocusOffset": 0},
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
        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            result = await get_site_profile()
        kept = {f.name: f for f in result.filters}
        self.assertEqual(list(kept), ["L", "OIII", "B"])
        self.assertEqual(kept["OIII"].position, 3)
        self.assertEqual(kept["B"].position, 4)
        self.assertIsNone(kept["B"].focus_offset)
        self.assertEqual(kept["L"].focus_offset, 0)

    async def test_get_site_equipment_status_returns_empty_when_all_false(self):
        from nina_planner.tools.equipment import (
            _get_site_equipment_status as _get_site_equipment_status,
        )

        profile = self._minimal_profile()
        with patch("nina_planner.client.api_get", AsyncMock(return_value={})):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsInstance(equipment, ObservatoryEquipment)
        self.assertIsNone(equipment.mount)
        self.assertIsNone(equipment.camera)

    async def test_get_site_equipment_status_unnamed_unconnected_device_is_none(self):
        # A device section that reports neither a connection nor a name
        # (NINA writing a stub entry for hardware it does not see) is not a
        # device: it must not be turned into a MountDevice with empty fields.
        from nina_planner.tools.equipment import (
            _get_site_equipment_status as _get_site_equipment_status,
        )

        profile = self._minimal_profile()
        raw = {"Mount": {"Connected": False, "Description": "no name reported"}}
        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsNone(equipment.mount)

    async def test_get_site_equipment_status_connects_missing_devices(self):
        from nina_planner.tools.equipment import (
            _get_site_equipment_status as _get_site_equipment_status,
        )

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
                    has_flat_device=False,
                )
            }
        )
        raw = {"Mount": {"Connected": False, "Name": "TestMount"}}

        async def fake(path, **kwargs):
            if "connect" in path:
                return {}
            return raw

        with patch("nina_planner.client.api_get", side_effect=fake):
            with self.assertRaises(RuntimeError) as ctx:
                await _get_site_equipment_status(profile_connected)
        self.assertIn("Mount", str(ctx.exception))

    async def test_get_site_equipment_status_connected_device(self):
        from nina_planner.tools.equipment import (
            _get_site_equipment_status as _get_site_equipment_status,
        )

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
                    has_flat_device=False,
                )
            }
        )
        raw = {"Mount": {"Connected": True, "Name": "TestMount"}}

        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsNotNone(equipment.mount)
        self.assertEqual(equipment.mount.name, "TestMount")
        self.assertTrue(equipment.mount.connected)

    async def test_get_site_equipment_status_connects_direct_guider(self):
        from nina_planner.tools.equipment import (
            _get_site_equipment_status as _get_site_equipment_status,
        )

        profile = self._minimal_profile().model_copy(
            update={
                "guider_name": "Direct_Guider",
                "equipment": EquipmentConfig(
                    has_mount=False,
                    has_camera=False,
                    has_focuser=False,
                    has_filter_wheel=False,
                    has_guider=True,
                    has_rotator=False,
                    has_dome=False,
                    has_weather=False,
                    has_safety_monitor=False,
                    has_switch=False,
                    has_flat_device=False,
                ),
            }
        )
        raw = {"Guider": {"Connected": False}}

        connect_called = False

        async def fake(path, **kwargs):
            nonlocal connect_called
            if "connect" in path:
                connect_called = True
                return {}
            return raw

        with patch("nina_planner.client.api_get", side_effect=fake):
            with self.assertRaises(RuntimeError) as ctx:
                await _get_site_equipment_status(profile)
        self.assertTrue(connect_called)
        self.assertIn("Guider", str(ctx.exception))

    async def test_get_events_returns_filtered(self):
        from nina_planner.server import get_events

        # The window is anchored on the host clock, so fixtures must be
        # relative: a recent event survives `since`, an old one is dropped.
        now = datetime.now(UTC).replace(microsecond=0)
        events = [
            {"Time": (now - timedelta(seconds=300)).isoformat(), "Type": "Info"},
            {"Time": (now - timedelta(seconds=7200)).isoformat(), "Type": "Info"},
        ]

        with patch("nina_planner.client.api_get", AsyncMock(return_value=events)):
            result = await get_events(since=3600)
        self.assertEqual(len(result), 1)
        # Rendered in the host's local zone; compare the instant.
        self.assertEqual(
            datetime.fromisoformat(result[0]["timestamp"]),
            (now - timedelta(seconds=300)).astimezone(),
        )

    async def test_get_logs_strips_internal_fields(self):
        from nina_planner.server import get_logs

        now = datetime.now(UTC).replace(microsecond=0)
        logs = [
            {
                "Timestamp": (now - timedelta(seconds=300)).isoformat(),
                "Message": "hello",
                "line": 42,
                "member": "M",
                "source": "test",
            }
        ]

        with patch("nina_planner.client.api_get", AsyncMock(return_value=logs)):
            result = await get_logs(since=3600)
        self.assertEqual(len(result), 1)
        self.assertIn("timestamp", result[0])
        self.assertNotIn("line", result[0])
        self.assertNotIn("member", result[0])
        self.assertNotIn("source", result[0])

    # The live image-history record N.I.N.A. serves for its current frame.
    _FRAME_RECORD = {
        "ExposureTime": 10,
        "ImageType": "LIGHT",
        "Filter": "LP",
        "RmsText": 'Tot: 0.42 (0.31")',
        "Temperature": 25.375,
        "CameraName": "Seestar Alpaca Camera",
        "TargetName": "NGC 1931",
        "Gain": 200,
        "Offset": -1,
        "Date": "2026-10-27T23:56:32.1190029-05:00",
        "TelescopeName": "seestar s30",
        "FocalLength": 150,
        "StDev": 250.64,
        "Mean": 1880.75,
        "Median": 1888,
        "Min": 1008,
        "Max": 65520,
        "Stars": 50,
        "HFR": 1.45,
        "HFRStDev": 0.1297,
        "IsBayered": True,
        "Filename": "x.fits.fz",
    }

    async def test_get_frame_stats_returns_full_record(self):
        from nina_planner.server import get_frame_stats

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value=[self._FRAME_RECORD]),
        ) as api_get:
            result = await get_frame_stats()
        # No image_type -> no imageType query parameter.
        api_get.assert_awaited_once_with("/image-history")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["image_type"], "LIGHT")
        self.assertIsNone(result["note"])
        frame = result["frame"]
        self.assertEqual(frame["stars"], 50)
        self.assertAlmostEqual(frame["hfr"], 1.45)
        self.assertAlmostEqual(frame["hfr_st_dev"], 0.1297)
        self.assertEqual(frame["rms_text"], 'Tot: 0.42 (0.31")')
        self.assertEqual(frame["median"], 1888)
        # The capture timestamp is parsed to a datetime, then dumped as ISO.
        self.assertEqual(frame["date"], "2026-10-27T23:56:32.119002-05:00")

    async def test_get_frame_stats_passes_image_type_filter(self):
        from nina_planner.server import get_frame_stats

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value=[self._FRAME_RECORD]),
        ) as api_get:
            result = await get_frame_stats("light")
        api_get.assert_awaited_once_with("/image-history?imageType=LIGHT")
        self.assertEqual(result["count"], 1)

    async def test_get_frame_stats_no_matching_type_is_empty_not_raised(self):
        from nina_planner.server import get_frame_stats

        # NINA answers an unmatchable imageType with a failed envelope that
        # unwrap_response turns into RuntimeError("Index out of range").
        with patch(
            "nina_planner.client.api_get",
            AsyncMock(side_effect=RuntimeError("Index out of range")),
        ):
            result = await get_frame_stats("dark")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["image_type"], "DARK")
        self.assertIsNone(result["frame"])
        self.assertIn("no DARK frame", result["note"])

    async def test_get_frame_stats_zero_stars_is_a_real_reading(self):
        from nina_planner.server import get_frame_stats

        record = dict(self._FRAME_RECORD, Stars=0)
        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value=[record]),
        ):
            result = await get_frame_stats()
        # A star-count failure is Stars=0, which must survive as 0, not None.
        self.assertEqual(result["frame"]["stars"], 0)

    async def test_get_frame_stats_empty_history_is_empty(self):
        from nina_planner.server import get_frame_stats

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value=[]),
        ):
            result = await get_frame_stats()
        self.assertEqual(result["count"], 0)
        self.assertIsNone(result["frame"])
        self.assertIn("no frame record", result["note"])

    async def test_get_frame_stats_accepts_a_bare_record_object(self):
        # The endpoint is documented as a one-element list, but a bare
        # object is tolerated rather than read as "no frame".
        from nina_planner.server import get_frame_stats

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value=dict(self._FRAME_RECORD)),
        ):
            result = await get_frame_stats()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["frame"]["stars"], self._FRAME_RECORD["Stars"])

    async def test_get_frame_stats_unrecognised_payload_reads_as_empty(self):
        from nina_planner.server import get_frame_stats

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value="unexpected shape"),
        ):
            result = await get_frame_stats()
        self.assertEqual(result["count"], 0)
        self.assertIsNone(result["frame"])
        self.assertIsNone(result["image_type"])

    async def test_get_frame_stats_reraises_an_unexpected_api_error(self):
        # "Index out of range" is the expected empty state; every other
        # failure is a fault the caller must see.
        from nina_planner.server import get_frame_stats

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(side_effect=RuntimeError("gateway exploded")),
        ):
            with self.assertRaisesRegex(RuntimeError, "gateway exploded"):
                await get_frame_stats()

    async def test_write_plan_file_creates_file(self):
        from nina_planner.server import write_plan_file

        plan = ObservationPlan(
            plan_id="plan-test123",
            target="M31",
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0}],
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat_frames=[{"filter_name": "L", "total_count": 1}],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias_frames=[{"total_count": 1}],
        )

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "sub"
            project_dir.mkdir()
            (project_dir / "progress.md").write_text("")
            with (
                patch("nina_planner.config.PROJECT_DIR", project_dir),
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
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
            flat_frames=[{"filter_name": "L", "total_count": 1}],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias_frames=[{"total_count": 1}],
        )

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "sub"
            project_dir.mkdir()
            (project_dir / "progress.md").write_text("")
            with (
                patch("nina_planner.config.PROJECT_DIR", project_dir),
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
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
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.metadata.read_metadata",
                AsyncMock(return_value=[]),
            ):
                result = await get_plan_progress(str(plan_path))
        self.assertEqual(result["plan_id"], "plan-test123")
        self.assertEqual(result["target"], "M31")
        self.assertEqual(result["pointing_count"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["pointings"]), 1)
        # Single-pointing plan: the per-pointing id is the bare plan-level id.
        self.assertEqual(result["pointings"][0]["plan_id"], "plan-test123")
        self.assertIn("light", result["pointings"][0]["frame_types"])
        # Calibration groups carry the window state so a zero remaining can be
        # told apart from a closed window; lights do not.
        dark = result["pointings"][0]["frame_types"]["dark"][0]
        self.assertTrue(dark["window_open"])
        self.assertIn("window_end", dark)
        self.assertNotIn(
            "window_open", result["pointings"][0]["frame_types"]["light"][0]
        )

    @staticmethod
    def _quality_light_rows(hfrs):
        """Light rows attributed to plan-test123, one per HFR value."""
        return [
            {
                "image_type": "LIGHT",
                "frame_type": "LIGHT",
                "filter_name": "L",
                "duration": "60.0",
                "file_path": f"C:/NINA/LIGHT/M31 _plan-test123__60.00s_{i:04d}.fits",
                "exposure_start_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "hfr": str(hfr),
                "detected_stars": "25",
            }
            for i, hfr in enumerate(hfrs, start=1)
        ]

    @staticmethod
    def _quality_plan_dict(**extra):
        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {
                    "filter_name": "L",
                    "exposure_time_seconds": 60.0,
                    "total_count": 2,
                }
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }
        plan_dict.update(extra)
        return plan_dict

    async def test_get_plan_progress_applies_plan_quality(self):
        """The plan's `quality` section — not a call argument — filters lights.

        Two attributed lights, one over the plan's max_hfr: only the good one
        counts, so the plan reports a deficit it still needs to acquire.
        """
        from nina_planner.server import get_plan_progress

        plan_dict = self._quality_plan_dict(quality={"max_hfr": 2.5})

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.metadata.read_metadata",
                AsyncMock(return_value=self._quality_light_rows([1.5, 4.0])),
            ):
                result = await get_plan_progress(str(plan_path))
        light = result["pointings"][0]["frame_types"]["light"][0]
        self.assertEqual(light["acquired_count"], 1)
        self.assertEqual(light["remaining_count"], 1)
        # The active thresholds ride along, so the counts can be read knowing
        # which rules produced them.
        self.assertEqual(result["quality"], {"max_hfr": 2.5})

    async def test_get_plan_progress_without_quality_counts_all(self):
        """A plan with no `quality` section behaves exactly as before."""
        from nina_planner.server import get_plan_progress

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._quality_plan_dict()))

            with patch(
                "nina_planner.metadata.read_metadata",
                AsyncMock(return_value=self._quality_light_rows([1.5, 4.0])),
            ):
                result = await get_plan_progress(str(plan_path))
        light = result["pointings"][0]["frame_types"]["light"][0]
        # Both lights count — including the one the quality test's threshold
        # rejects — because nothing is set.
        self.assertEqual(light["acquired_count"], 2)
        self.assertEqual(light["remaining_count"], 0)
        self.assertEqual(result["quality"], {})

    def test_get_plan_progress_takes_no_threshold_arguments(self):
        """Contract guard: thresholds live in the plan file only.

        Re-adding a parameter here would split the source of truth — the
        counts would then depend on whether the caller remembered to pass
        the same values load_sequence got.
        """
        import inspect

        from nina_planner.server import get_plan_progress

        params = list(inspect.signature(get_plan_progress).parameters)
        self.assertEqual(params, ["file_path"])

    async def test_get_plan_progress_refuses_unknown_arguments(self):
        """Unknown args are refused on the ARG MODEL — FastMCP drops them.

        The function's only parameter is `file_path`, and FastMCP builds
        its argument model with extra keys IGNORED, so any stray key —
        not just the four quality thresholds — must be refused by name
        instead of silently vanishing.
        """
        from mcp.server.fastmcp.exceptions import ToolError

        from nina_planner.server import mcp as server_mcp

        with self.assertRaises(ToolError) as ctx:
            await server_mcp.call_tool(
                "get_plan_progress",
                {"file_path": "plans/m31.json", "max_hfr": 2.5},
            )
        message = str(ctx.exception)
        self.assertIn("max_hfr", message)
        # Not a migration hint — a plain invalid-argument refusal.
        self.assertIn("Extra inputs are not permitted", message)
        # A non-threshold stray key is refused the same way.
        with self.assertRaises(ToolError) as ctx:
            await server_mcp.call_tool(
                "get_plan_progress",
                {"file_path": "plans/m31.json", "nonsense": 1},
            )
        self.assertIn("nonsense", str(ctx.exception))

    async def test_get_plan_progress_flags_overshoot_with_warning(self):
        """`complete: true` plus `overshot` is how a truncated mode=full
        re-shoot becomes visible.

        The plan's frames genuinely exist, so complete stays true — but more
        of them exist than the plan ever asked for, which is the only trace
        here that a second run happened at all.
        """
        from nina_planner.server import get_plan_progress

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }
        # Three lights attributed where the plan asks for one, plus enough
        # calibration to finish the plan outright — so the story below is a
        # genuinely complete plan that overshot, not a partial one.
        #
        # Stamps are relative to the host clock, not hardcoded: the
        # calibration window is 7 days wide, so a fixed date silently ages
        # out of it and the calibration stops counting (the plan then reads
        # incomplete and this test fails for a reason that has nothing to do
        # with what it checks).
        stamp = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = [
            {
                "image_type": "LIGHT",
                "file_path": f"C:/NINA/LIGHT/M31 _plan-test123__60.00s_{i:04d}.fits",
                "duration": "60.0",
                "filter_name": "L",
                "hfr": "1.50",
                "detected_stars": "100",
                "exposure_start_utc": stamp,
            }
            for i in range(3)
        ] + [
            {
                "image_type": "FLAT",
                "file_path": "C:/NINA/FLAT/M31 _plan-test123__0.10s_0000.fits",
                "filter_name": "L",
                "exposure_start_utc": stamp,
            },
            {
                "image_type": "DARK",
                "file_path": "C:/NINA/DARK/M31 _plan-test123__60.00s_0000.fits",
                "duration": "60.0",
                "exposure_start_utc": stamp,
            },
            {
                "image_type": "BIAS",
                "file_path": "C:/NINA/BIAS/M31 _plan-test123__0.00s_0000.fits",
                "exposure_start_utc": stamp,
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.metadata.read_metadata",
                AsyncMock(return_value=rows),
            ):
                result = await get_plan_progress(str(plan_path))

        light = result["pointings"][0]["frame_types"]["light"][0]
        # The plan is satisfied, and stays reported as such...
        self.assertTrue(result["complete"])
        self.assertEqual(light["acquired_count"], 3)
        self.assertEqual(light["remaining_count"], 0)
        # ...but the surplus is surfaced rather than silently swallowed.
        self.assertEqual(light["surplus_count"], 2)
        self.assertTrue(result["overshot"])
        self.assertIn("2 light frame(s)", result["warning"])
        # The warning has to say what to do next, not just that something is
        # odd — complete:true alone is what an operator sees after a
        # re-shoot lost frames.
        self.assertIn("audit_plans", result["warning"])
        self.assertIn("does NOT", result["warning"])

    async def test_get_plan_progress_no_overshoot_when_within_total(self):
        from nina_planner.server import get_plan_progress

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 2}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }
        rows = [
            {
                "image_type": "LIGHT",
                "file_path": "C:/NINA/LIGHT/M31 _plan-test123__60.00s_0000.fits",
                "duration": "60.0",
                "filter_name": "L",
                "hfr": "1.50",
                "detected_stars": "100",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.metadata.read_metadata",
                AsyncMock(return_value=rows),
            ):
                result = await get_plan_progress(str(plan_path))

        light = result["pointings"][0]["frame_types"]["light"][0]
        self.assertEqual(light["surplus_count"], 0)
        self.assertFalse(result["overshot"])
        self.assertIsNone(result["warning"])

    async def test_get_plan_progress_expired_window_is_not_complete(self):
        """A calibration window that closed with frames missing must not
        read `complete: true`.

        `remaining_count` is deliberately 0 once the window closes (no frame
        shot now could be credited), so completion is decided from
        acquired-vs-total and window state instead. The expired deficit is
        reported apart from `incomplete` because its fix — a raised
        calibration_window_days or a new plan — is not "shoot more frames".
        """
        from nina_planner.server import get_plan_progress

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 2}],
            "bias_frames": [{"total_count": 1}],
        }
        # Lights complete, bias present, one of two darks present — and the
        # whole session a month old, so the default 7-day calibration
        # window has closed around a real 1-dark deficit.
        old = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = [
            {
                "image_type": "LIGHT",
                "file_path": "C:/NINA/LIGHT/M31 _plan-test123__60.00s_0000.fits",
                "duration": "60.0",
                "filter_name": "L",
                "hfr": "1.50",
                "detected_stars": "100",
                "exposure_start_utc": old,
            },
            {
                "image_type": "FLAT",
                "file_path": "C:/NINA/FLAT/M31 _plan-test123__0.10s_0000.fits",
                "filter_name": "L",
                "exposure_start_utc": old,
            },
            {
                "image_type": "DARK",
                "file_path": "C:/NINA/DARK/M31 _plan-test123__60.00s_0000.fits",
                "duration": "60.0",
                "exposure_start_utc": old,
            },
            {
                "image_type": "BIAS",
                "file_path": "C:/NINA/BIAS/M31 _plan-test123__0.00s_0000.fits",
                "duration": "0.00",
                "exposure_start_utc": old,
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.metadata.read_metadata",
                AsyncMock(return_value=rows),
            ):
                result = await get_plan_progress(str(plan_path))

        pane = result["pointings"][0]
        dark = pane["frame_types"]["dark"][0]
        # The window-closed contract still holds: nothing to re-acquire...
        self.assertFalse(dark["window_open"])
        self.assertEqual(dark["remaining_count"], 0)
        # ...but the shortfall is not laundered into completion.
        self.assertEqual(dark["deficit_count"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(pane["status"], "expired_with_deficit")
        self.assertFalse(pane["complete"])
        self.assertTrue(result["expired_with_deficit"])
        # The warning names the remedy, since shooting cannot fix this.
        self.assertIn("dark", result["warning"])
        self.assertIn("calibration_window_days", result["warning"])
        # Lights were never in doubt: their status signal is unchanged.
        self.assertNotIn("window_open", pane["frame_types"]["light"][0])
        self.assertEqual(pane["frame_types"]["light"][0]["deficit_count"], 0)

    async def test_get_plan_progress_open_window_deficit_stays_incomplete(self):
        """Same counts as the expired test, but inside the window: the pane
        is `incomplete` (shoot more), not `expired_with_deficit`, and
        deficit_count agrees with remaining_count because no window
        adjustment applies."""
        from nina_planner.server import get_plan_progress

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 2}],
            "bias_frames": [{"total_count": 1}],
        }
        stamp = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = [
            {
                "image_type": "LIGHT",
                "file_path": "C:/NINA/LIGHT/M31 _plan-test123__60.00s_0000.fits",
                "duration": "60.0",
                "filter_name": "L",
                "hfr": "1.50",
                "detected_stars": "100",
                "exposure_start_utc": stamp,
            },
            {
                "image_type": "DARK",
                "file_path": "C:/NINA/DARK/M31 _plan-test123__60.00s_0000.fits",
                "duration": "60.0",
                "exposure_start_utc": stamp,
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))

            with patch(
                "nina_planner.metadata.read_metadata",
                AsyncMock(return_value=rows),
            ):
                result = await get_plan_progress(str(plan_path))

        pane = result["pointings"][0]
        dark = pane["frame_types"]["dark"][0]
        self.assertTrue(dark["window_open"])
        self.assertEqual(dark["remaining_count"], 1)
        self.assertEqual(dark["deficit_count"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(pane["status"], "incomplete")
        self.assertFalse(result["expired_with_deficit"])
        self.assertIsNone(result["warning"])

    def _mosaic_plan(self, *, pa: float = 0.0, coverage=(100.0, 60.0)):
        """A plan that asks write_plan_file to derive its grid from coverage.

        Coverage 100'x60' against the `_full_profile` rig (64.63'x43.09'
        panes, 20% overlap -> 51.70'x34.47' steps) derives exactly 2x2.
        """
        return ObservationPlan(
            target="NGC 7000",
            intent="North America Nebula",
            pointings=[],
            mosaic={
                "center_ra_hours": 20.981667,
                "center_dec_deg": 44.33,
                "coverage_width_arcmins": coverage[0],
                "coverage_height_arcmins": coverage[1],
                "overlap_pct": 20.0,
                "pa_deg": pa,
            },
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 2}
            ],
            flat_frames=[{"filter_name": "L", "total_count": 1}],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias_frames=[{"total_count": 1}],
        )

    async def test_write_plan_file_mosaic_derives_grid_from_coverage(self):
        from nina_planner.server import write_plan_file

        plan = self._mosaic_plan()
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            with (
                patch("nina_planner.config.PROJECT_DIR", project_dir),
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
            ):
                result = await write_plan_file(plan)

            # The response reports the derived grid and what it covers.
            self.assertIn("mosaic 2x2 (4 pointings)", result)
            self.assertIn("pane FOV", result)
            self.assertIn("covers", result)
            self.assertNotIn("WARNING", result)

            written = list(project_dir.glob("*.json"))
            self.assertEqual(len(written), 1)
            data = json.loads(written[0].read_text())

            # Expanded pointings: r{row}_c{col}, 1-based, row-major, and the
            # centers sit around the requested mosaic center.
            self.assertEqual(len(data["pointings"]), 4)
            self.assertEqual(
                [p["label"] for p in data["pointings"]],
                ["r1_c1", "r1_c2", "r2_c1", "r2_c2"],
            )
            self.assertEqual(
                [(p["row"], p["column"]) for p in data["pointings"]],
                [(1, 1), (1, 2), (2, 1), (2, 2)],
            )
            for p in data["pointings"]:
                self.assertAlmostEqual(p["dec_deg"], 44.33, delta=1.0)
                self.assertAlmostEqual(p["ra_hours"], 20.981667, delta=0.15)

            # Provenance stamped back: derived cols/rows + the pane FOV the
            # grid was sized against (the profile's rig, in arcmins).
            self.assertEqual(data["mosaic"]["cols"], 2)
            self.assertEqual(data["mosaic"]["rows"], 2)
            self.assertAlmostEqual(
                data["mosaic"]["pane_fov_arcmins"][0], 64.63, places=2
            )
            self.assertAlmostEqual(
                data["mosaic"]["pane_fov_arcmins"][1], 43.09, places=2
            )

            # Content-derived id: each pane then attributes independently
            # via _{plan_id}-{pointing_index}_.
            self.assertTrue(data["plan_id"].startswith("plan-"))

            # End to end: the written file passes the load-time pointing
            # guard (plan files always carry their pointings).
            from nina_planner.plan_utils import load_plan

            loaded = await load_plan(str(written[0]))
            self.assertEqual(len(loaded.pointings), 4)
            self.assertEqual(loaded.mosaic.cols, 2)

    async def test_write_plan_file_mosaic_missing_optics_raises(self):
        # No guessed FOV: a mosaic sized against missing optics would be
        # sized wrong, so every missing value is named instead.
        from nina_planner.server import write_plan_file

        profile = self._full_profile().model_copy(update={"optics": OpticalTrainInfo()})
        plan = self._mosaic_plan()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("nina_planner.config.PROJECT_DIR", Path(tmp)),
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=profile),
                ),
                self.assertRaises(ValueError) as ctx,
            ):
                await write_plan_file(plan)
            msg = str(ctx.exception)
            self.assertIn("focal_length_mm", msg)
            self.assertIn("pixel_size_microns", msg)
            # Nothing written: the failure happens before the file is created.
            self.assertEqual(list(Path(tmp).glob("*.json")), [])

    async def test_write_plan_file_mosaic_zero_pa_without_rotator_ok(self):
        from nina_planner.server import write_plan_file

        profile = self._full_profile().model_copy(
            update={
                "equipment": self._full_profile().equipment.model_copy(
                    update={"has_rotator": False}
                )
            }
        )
        plan = self._mosaic_plan(pa=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("nina_planner.config.PROJECT_DIR", Path(tmp)),
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=profile),
                ),
            ):
                result = await write_plan_file(plan)
            self.assertIn("mosaic 2x2 (4 pointings)", result)

    async def test_write_plan_file_mosaic_nonzero_pa_without_rotator_raises(self):
        # PA can't be enforced without a rotator, so a plan that asks for
        # a nonzero position angle fails loudly.
        from nina_planner.server import write_plan_file

        profile = self._full_profile().model_copy(
            update={
                "equipment": self._full_profile().equipment.model_copy(
                    update={"has_rotator": False}
                )
            }
        )
        plan = self._mosaic_plan(pa=90.0)
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("nina_planner.config.PROJECT_DIR", Path(tmp)),
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=profile),
                ),
                self.assertRaises(ValueError) as ctx,
            ):
                await write_plan_file(plan)
            self.assertIn("no rotator", str(ctx.exception))
            self.assertEqual(list(Path(tmp).glob("*.json")), [])

    async def test_write_plan_file_mosaic_single_pane_warns(self):
        # A coverage that fits inside one pane derives 1x1 — the classic
        # symptom of degrees passed where arcmins were expected — so the
        # response warns, and the plan behaves as an ordinary
        # single-pointing plan (bare id shared with its calibration).
        from nina_planner.server import write_plan_file

        plan = self._mosaic_plan(coverage=(5.0, 5.0))
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            with (
                patch("nina_planner.config.PROJECT_DIR", project_dir),
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
            ):
                result = await write_plan_file(plan)
            self.assertIn("1x1", result)
            self.assertIn("WARNING", result)
            self.assertIn("arcmins, not degrees", result)

            [written] = project_dir.glob("*.json")
            data = json.loads(written.read_text())
            self.assertEqual(len(data["pointings"]), 1)
            self.assertEqual(data["mosaic"]["cols"], 1)
            self.assertEqual(data["mosaic"]["rows"], 1)
            loaded = ObservationPlan.model_validate(data)
            self.assertEqual(loaded.effective_plan_id(1), loaded._base_plan_id())

    async def test_write_plan_file_mosaic_with_pointings_passes_through(self):
        # Both present means the pointings are already expanded: they are
        # the authority, nothing re-derives, and the stamp stays as it was.
        from nina_planner.models.pointing import Pointing
        from nina_planner.server import write_plan_file

        plan = self._mosaic_plan()
        plan = plan.model_copy(
            update={"pointings": [Pointing(ra_hours=1.5, dec_deg=-2.25)]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            with (
                patch("nina_planner.config.PROJECT_DIR", project_dir),
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
            ):
                result = await write_plan_file(plan)
            self.assertNotIn("mosaic 2x2", result)
            [written] = project_dir.glob("*.json")
            data = json.loads(written.read_text())
            self.assertEqual(len(data["pointings"]), 1)
            self.assertEqual(data["pointings"][0]["ra_hours"], 1.5)
            self.assertEqual(data["pointings"][0]["dec_deg"], -2.25)
            self.assertIsNone(data["mosaic"]["cols"])

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
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))
            with patch(
                "nina_planner.metadata.read_metadata",
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
        """Nothing outstanding in any group: remaining mode reports the plan
        complete and loads nothing."""
        outcome, _ = await self._load_entry(
            "light",
            request={"pointing_index": 1},
            progress=self._progress(light=0, flat=0, dark=0, bias=0),
        )
        self.assertIn("is complete", outcome)
        self.assertIn("nothing to load", outcome)

    async def test_load_sequence_unpinned_plan_keeps_its_token_in_remaining_mode(self):
        """Remaining mode rewrites frame counts, but the emitted plan token
        must stay the id the plan FILE derives — otherwise frames
        re-attribute every night as remaining shrinks and the plan never
        completes."""
        import re

        # Deliberately NO plan_id: the id is derived from this content.
        plan_dict = self._entry_plan_dict(
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 4}
            ]
        )
        plan_dict.pop("plan_id")
        file_base = ObservationPlan.model_validate(plan_dict)._base_plan_id()

        # One of the four lights already attributed under the FILE's token,
        # so remaining mode really rewrites the plan (4 -> 3).
        rows = [
            {
                "image_type": "LIGHT",
                "file_path": (f"C:\\NINA\\LIGHT\\2026-11-01\\shot _{file_base}_.fits"),
                "filter_name": "L",
                "duration": 60.0,
            }
        ]
        outcome, api_post = await self._load_entry(
            "light", plan=plan_dict, metadata=rows
        )

        self.assertIn("3 frame(s) to acquire", outcome)
        api_post.assert_awaited_once()
        body = json.dumps(api_post.await_args.args[1])
        tokens = set(re.findall(r"plan-[0-9a-f]{12}", body))
        self.assertEqual(
            tokens,
            {file_base},
            msg=(
                "every plan token in the emitted sequence must be the id of "
                f"the plan FILE as written ({file_base}); got {tokens}"
            ),
        )

    async def test_load_sequence_remaining_mode_uses_plan_quality(self):
        """The plan's `quality` section decides what load_sequence acquires.

        Two lights are attributed to the plan but one fails its max_hfr, so
        only one counts — remaining mode must ask N.I.N.A. for the missing
        frame rather than declaring the plan done.
        """
        outcome, api_post = await self._load_entry(
            "light",
            plan=self._quality_plan_dict(quality={"max_hfr": 2.5}),
            metadata=self._quality_light_rows([1.5, 4.0]),
        )
        self.assertTrue(
            api_post.await_args_list, "load_sequence never asked N.I.N.A. to load"
        )
        self.assertEqual(api_post.await_args_list[0].args[0], "/sequence/load")
        self.assertIn("1 frame(s) to acquire", outcome)
        self.assertNotIn("nothing to load", outcome)

    async def test_load_sequence_without_quality_counts_all(self):
        """Same rows, no `quality` section: both frames count, nothing to load."""
        outcome, _ = await self._load_entry(
            "light",
            plan=self._quality_plan_dict(),
            metadata=self._quality_light_rows([1.5, 4.0]),
        )
        self.assertIn("is complete", outcome)
        self.assertIn("nothing to load", outcome)

    def test_load_sequence_request_refuses_each_stray_key(self):
        """An unknown request field fails loudly instead of being ignored —
        pydantic would drop it, and the caller would believe it was filtering
        while every frame counted."""
        for key in (
            "max_hfr",
            "max_hfr_st_dev",
            "min_detected_stars",
            "max_guiding_rms_arcsec",
        ):
            with self.subTest(key=key):
                with self.assertRaises(Exception) as ctx:
                    SequenceRequest(plan="plan.json", action="light", **{key: 1})
                self.assertIn(key, str(ctx.exception))
                self.assertIn("Extra inputs are not permitted", str(ctx.exception))

    async def test_load_sequence_read_metadata_fails(self):
        """Remaining mode cannot count frames without metadata: the error
        names mode='full' as the escape hatch."""
        outcome, _ = await self._load_entry(
            "light",
            request={"pointing_index": 1},
            metadata=RuntimeError("imaging dir missing"),
        )
        self.assertIsInstance(outcome, ValueError)
        self.assertIn("Cannot compute remaining frames", str(outcome))
        self.assertIn("Pass mode='full'", str(outcome))

    async def test_load_sequence_missing_plan_file_names_the_entry(self):
        """A plan file that cannot be READ gets the `request[i]:` prefix too —
        the 0-based entry position must wrap FileNotFoundError, not just
        ValueError."""
        from nina_planner.server import load_sequence

        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "does-not-exist.json")
            with self.assertRaises(ValueError) as ctx:
                await load_sequence([SequenceRequest(plan=missing, action="light")])
        message = str(ctx.exception)
        self.assertTrue(
            message.startswith("request[0]: "), f"unprefixed message: {message}"
        )
        self.assertIn("does-not-exist.json", message)

    async def test_load_sequence_invalid_mode(self):
        """Only `remaining` and `full` are modes; anything else is rejected."""
        outcome, _ = await self._load_entry(
            "light",
            request={"pointing_index": 1, "mode": "invalid"},
        )
        self.assertIsInstance(outcome, ValueError)
        self.assertIn("Unsupported mode", str(outcome))

    async def test_load_sequence_request_must_be_a_list(self):
        # A single object (or any non-list) is a caller bug, caught before a
        # plan is read: the error names the type so the shape is obvious.
        from nina_planner.server import load_sequence

        with self.assertRaises(ValueError) as ctx:
            await load_sequence(request={"plan": "plan.json", "action": "light"})
        self.assertIn("must be a list", str(ctx.exception))
        self.assertIn("dict", str(ctx.exception))

    async def test_load_sequence_requires_plan(self):
        """`plan` is optional in the schema, but every action needs one."""
        from nina_planner.server import load_sequence

        with self.assertRaises(ValueError) as ctx:
            await load_sequence([SequenceRequest(action="light")])
        self.assertIn("action='light' requires a plan", str(ctx.exception))
        self.assertIn("request.plan", str(ctx.exception))

    async def test_load_sequence_dark_frame(self):
        """A dark loads plan-level: one darks step, no pointing in the report."""
        from unittest.mock import MagicMock as _MM

        darks_step = _MM(return_value=[{"$type": "darks"}])
        with patch("nina_planner.tools.sequence.target_payload_darks", new=darks_step):
            result, api_post = await self._load_entry("dark")
        darks_step.assert_called_once()
        self.assertEqual(api_post.await_args.args[0], "/sequence/load")
        self.assertIn("`dark` — ", result)
        self.assertNotIn("pointing", result, "calibration is plan-level")
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)
        self.assertIn("start_sequence()", result)

    async def test_load_sequence_dark_skipped_when_window_closed(self):
        """A plan whose lights are older than `calibration_window_days` cannot
        credit any dark shot now, so `mode="remaining"` must report the darks
        as done rather than re-shoot them forever."""
        from unittest.mock import MagicMock as _MM

        old_light = {
            "image_type": "LIGHT",
            "filter_name": "L",
            "duration": "60.0",
            "exposure_start_utc": "2020-01-01T20:00:00Z",
            "file_path": "C:/NINA/LIGHT/M31 _plan-test123-1___60.00s_0000.fits",
        }
        darks_step = _MM(return_value=[{"$type": "darks"}])
        with patch("nina_planner.tools.sequence.target_payload_darks", new=darks_step):
            result, _ = await self._load_entry("dark", metadata=[old_light])

        # The window closed with the dark still missing: nothing loads (a
        # frame shot now could not be credited), and the report names the
        # expired deficit instead of calling the plan complete.
        self.assertIn("EXPIRED", result)
        self.assertIn("nothing to load", result.lower())
        darks_step.assert_not_called()

    async def test_load_sequence_bias_frame(self):
        """A bias loads plan-level via the wheel darks builder with bias=True."""
        captured_kwargs = {}

        def fake_darks(plan, equipment, profile, bias=False, variant="wheel"):
            captured_kwargs["bias"] = bias
            captured_kwargs["variant"] = variant
            captured_kwargs["profile"] = profile
            return [{"$type": "darks"}]

        with patch(
            "nina_planner.tools.sequence.target_payload_darks",
            side_effect=fake_darks,
        ):
            result, _ = await self._load_entry("bias")
        self.assertTrue(captured_kwargs["bias"])
        # No variant asked for: darks/bias fall back to the wheel method.
        self.assertEqual(captured_kwargs["variant"], "wheel")
        self.assertEqual(captured_kwargs["profile"].profile_id, "abc")
        self.assertIn("`bias` — ", result)
        self.assertNotIn("pointing", result, "calibration is plan-level")
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)

    async def test_load_sequence_pointing_index_rejected_for_calibration(self):
        """Calibration is plan-level: any pointing_index — including the
        habit-shaped pointing_index=1 — is rejected with a teaching error,
        while lights range-check theirs instead."""
        from unittest.mock import MagicMock as _MM

        darks_step = _MM(return_value=[{"$type": "darks"}])
        lights_step = _MM(return_value=[{"$type": "lights"}])
        with (
            patch("nina_planner.tools.sequence.target_payload_darks", new=darks_step),
            patch("nina_planner.tools.sequence.target_payload_lights", new=lights_step),
        ):
            for bad_index in (1, 99):
                outcome, _ = await self._load_entry(
                    "dark", request={"pointing_index": bad_index}
                )
                self.assertIsInstance(outcome, ValueError)
                self.assertIn("Remove pointing_index from this request", str(outcome))
            outcome, _ = await self._load_entry("light", request={"pointing_index": 99})
            self.assertIsInstance(outcome, ValueError)
            self.assertIn("pointing_index=99 out of range", str(outcome))
            # Omitting the index on calibration loads plan-level.
            result, _ = await self._load_entry("dark")
        darks_step.assert_called_once()
        lights_step.assert_not_called()
        self.assertIn("`dark` — ", result)
        self.assertNotIn("pointing", result)

    async def test_load_sequence_dawn_flat(self):
        """A dawn flat builds its flats step with dusk=False."""
        captured_kwargs = {}

        def fake_flats(plan, equipment, profile, dusk=False):
            captured_kwargs["dusk"] = dusk
            return [{"$type": "flats"}]

        with patch(
            "nina_planner.tools.sequence.target_payload_flats",
            side_effect=fake_flats,
        ):
            result, _ = await self._load_entry("flat", variant="dawn")
        self.assertFalse(captured_kwargs["dusk"])
        self.assertIn("`flat` (dawn) — ", result)
        self.assertNotIn("pointing", result, "calibration is plan-level")
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)

    async def test_load_sequence_dusk_flat(self):
        """A dusk flat builds its flats step with dusk=True."""
        captured_kwargs = {}

        def fake_flats(plan, equipment, profile, dusk=False):
            captured_kwargs["dusk"] = dusk
            return [{"$type": "flats"}]

        with patch(
            "nina_planner.tools.sequence.target_payload_flats",
            side_effect=fake_flats,
        ):
            result, _ = await self._load_entry("flat", variant="dusk")
        self.assertTrue(captured_kwargs["dusk"])
        self.assertIn("`flat` (dusk) — ", result)
        self.assertNotIn("pointing", result, "calibration is plan-level")
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)

    async def test_load_light_sequence_requires_targetname_file_pattern(self):
        """Light frames attribute through $$TARGETNAME$$, so a profile whose
        FilePattern lacks it fails the load before anything is POSTed."""
        outcome, api_post = await self._load_entry(
            "light",
            profile=self._full_profile().model_copy(update={"file_pattern": None}),
            request={"mode": "full"},
        )
        self.assertIsInstance(outcome, ValueError)
        self.assertIn("N.I.N.A. FilePattern must contain $$TARGETNAME$$", str(outcome))
        api_post.assert_not_awaited()

    async def test_load_sequence_full_mode(self):
        """A full-mode light load reads exactly one thing from N.I.N.A.
        first — the plugin list — then one POST carries the sequence."""
        mocks: dict[str, Any] = {}
        result, api_post = await self._load_entry(
            "light",
            request={"pointing_index": 1, "mode": "full"},
            metadata=FileNotFoundError("no drive"),
            mocks=mocks,
        )
        self.assertIn("`light` pointing 1", result)
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)
        api_post.assert_awaited_once()
        self.assertEqual(api_post.await_args.args[0], "/sequence/load")
        # The load's own API traffic is exactly one read: the plugin list
        # (Sequencer+ must be installed before a light/flat payload is
        # POSTed). No running-state pre-check, no start.
        self.assertEqual(
            [call.args[0] for call in mocks["api_get"].await_args_list],
            ["/application/plugins"],
        )

    async def test_full_mode_note_swallows_every_metadata_failure(self):
        """`_full_mode_note` is advisory: any metadata read/parse failure
        (unreadable file, bad UTF-8, malformed row) returns "" instead of
        aborting the load."""
        from nina_planner.plan_utils import load_plan
        from nina_planner.tools.sequence import _full_mode_note

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }
        failures: dict[str, Exception] = {
            "OSError": OSError("cannot read ImageMetaData.csv"),
            "UnicodeDecodeError": UnicodeDecodeError(
                "utf-8", b"\xff\xfe", 0, 1, "invalid start byte"
            ),
            "TypeError": TypeError("to_snake(None)"),
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))
            plan = await load_plan(str(plan_path))
            now = datetime.now(UTC).replace(microsecond=0)
            for label, exc in failures.items():
                with self.subTest(label):
                    with patch(
                        "nina_planner.metadata.read_metadata",
                        new=AsyncMock(side_effect=exc),
                    ):
                        note = await _full_mode_note(plan, "light", 1, 5, now)
                    self.assertEqual(note, "")

    async def test_full_mode_note_scans_the_imaging_root_once(self):
        """One scan: the advisory note asks for light rows only.

        `_full_mode_note` reads `read_metadata("light")` — and an
        unfiltered `read_metadata()` also scans once (image_type=None is
        matched in a single walk) — so one default (full-mode) light
        command never pays more than one pass over the imaging root.
        """
        from nina_planner import imaging
        from nina_planner.plan_utils import load_plan
        from nina_planner.tools.sequence import _full_mode_note

        plan_dict = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }
        scanned: list[str] = []
        real_rglob = imaging.visible_rglob

        def counting_rglob(root: Path, name: str) -> list[Path]:
            scanned.append(name)
            return real_rglob(root, name)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan_dict))
            plan = await load_plan(str(plan_path))
            with (
                patch(
                    "nina_planner.metadata.resolve_imaging_root",
                    AsyncMock(return_value=root),
                ),
                patch(
                    "nina_planner.imaging.visible_rglob",
                    side_effect=counting_rglob,
                ),
            ):
                note = await _full_mode_note(
                    plan, "light", 1, 5, datetime.now(UTC).replace(microsecond=0)
                )

        # Empty root → nothing attributed → frames outstanding → no warning.
        self.assertEqual(note, "")
        self.assertEqual(
            len(scanned),
            1,
            f"expected one type-scan of the root, got {len(scanned)}: {scanned}",
        )

    async def test_load_sequence_full_mode_warns_when_already_complete(self):
        """A full-mode re-shoot over an already-complete plan says so at load:
        nothing downstream can verify the outcome afterwards, so the load
        line is the only place the operator is told."""
        # One already-attributed light, so the group reads complete.
        rows = [
            {
                "image_type": "LIGHT",
                "file_path": (
                    r"Z:\landing\M31 _plan-test123_\2026-10-22"
                    r"\LIGHT\frame_0000.fits.fz"
                ),
                "duration": "60.0",
                "filter_name": "L",
                "hfr": "1.50",
                "detected_stars": "100",
            }
        ]
        result, _ = await self._load_entry(
            "light",
            request={"pointing_index": 1, "mode": "full"},
            metadata=rows,
        )

        self.assertIn("1 frame(s), mode=full", result)
        self.assertIn("already complete", result)
        # The warning must never block the load itself.
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)

    async def test_load_sequence_full_mode_silent_when_frames_outstanding(self):
        """No warning when the group still has frames to acquire — that is an
        ordinary full-mode load, not a re-shoot over a finished plan."""
        plan = self._entry_plan_dict(
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
            ]
        )
        result, _ = await self._load_entry(
            "light", plan=plan, request={"pointing_index": 1, "mode": "full"}
        )

        self.assertIn("5 frame(s), mode=full", result)
        self.assertNotIn("already complete", result)
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)

    async def test_load_sequence_full_mode_calibration_never_warns(self):
        """Calibration is pool-credited across plans, so a dark group reads
        "already complete" on nearly every plan — warning there would fire on
        every routine calibration load and mean nothing."""
        # Every frame type already present and inside its window, so the dark
        # group reads complete — the exemption under test is the only reason
        # no warning appears. The stamp is relative to the host clock: the
        # calibration window is 7 days wide, so a hardcoded date ages out of
        # it and the dark stops counting (the exemption would then never be
        # exercised, and this test would pass for the wrong reason).
        rows = [
            {
                "image_type": "DARK",
                "file_path": r"Z:\landing\M31 _plan-test123_\2026-10-22\DARK\d.fits.fz",
                "duration": "60.0",
                "exposure_start_utc": (datetime.now(UTC) - timedelta(hours=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
        ]
        result, _ = await self._load_entry(
            "dark", request={"mode": "full"}, metadata=rows
        )

        self.assertIn("1 frame(s), mode=full", result)
        self.assertNotIn("already complete", result)

    async def test_start_sequence(self):
        """start_sequence waits for the start to take: the start GET, then a
        `/sequence/json` read proving a node left CREATED — it returns once
        the sequencer has actually started, not when N.I.N.A. accepted the
        request (see client.wait_until_sequence_started)."""
        from nina_planner.server import start_sequence

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(return_value={"Status": "RUNNING"}),
        ) as api_get:
            result = await start_sequence()
        self.assertEqual(result, "Sequence started.")
        paths = [call.args[0] for call in api_get.await_args_list]
        self.assertEqual(paths[0], "/sequence/start")
        self.assertIn("/sequence/json", paths, "the start is verified")

    async def test_start_sequence_raises_when_nothing_leaves_created(self):
        """A start N.I.N.A. accepts but never runs (every node still CREATED
        after the budget) must not be reported as started."""
        from nina_planner.server import start_sequence

        with (
            patch(
                "nina_planner.client.api_get",
                AsyncMock(return_value={"Status": "CREATED"}),
            ),
            patch("nina_planner.client.SEQUENCE_START_TIMEOUT", 0.01),
            self.assertRaisesRegex(RuntimeError, "did not start within 0s") as ctx,
        ):
            await start_sequence()

        message = str(ctx.exception)
        self.assertIn("CREATED", message)
        self.assertIn("get_logs()", message)

    async def test_start_sequence_accepts_a_sequence_that_finished_first(self):
        """A short park can legitimately complete between the start and the
        first read: FINISHED nodes prove it ran, so this is not a failure."""
        from nina_planner.server import start_sequence

        reads = iter(
            [
                "Sequence started",  # GET /sequence/start — return value unused
                {"Status": "FINISHED"},  # first state read: already done
            ]
        )
        with patch(
            "nina_planner.client.api_get",
            AsyncMock(side_effect=lambda path: next(reads)),
        ) as api_get:
            result = await start_sequence()

        self.assertEqual(result, "Sequence started.")
        self.assertEqual(api_get.await_count, 2)

    async def test_load_sequence_empty_list_parks_without_starting(self):
        """The default (and only) park: no arguments loads the closing
        sequence — empty Target area, End area parks then warms — but never
        starts it."""
        with patch(
            "nina_planner.client.api_get",
            AsyncMock(side_effect=AssertionError("no NINA reads expected")),
        ):
            result, api_post = await self._load_list([])

        self.assertIn("`park` sequence loaded", result)
        self.assertIn("start_sequence()", result)
        path, payload = api_post.await_args.args
        self.assertEqual(path, "/sequence/load")
        areas = payload["Items"]["$values"]
        self.assertEqual(areas[1]["Items"]["$values"], [], "empty target area")
        end_types = [i["$type"] for i in _end_park_items(payload)]
        self.assertTrue(any("ParkScope" in t for t in end_types), "End parks")
        self.assertTrue(any("WarmCamera" in t for t in end_types), "End warms")

    async def test_load_sequence_rejects_a_mount_that_cannot_park(self):
        """No park, no fallback: `can_park=false` is rejected outright even
        when the mount reports `can_find_home=true`, and nothing is POSTed."""
        with self.assertRaises(ValueError) as ctx:
            await self._load_list(
                [], equipment=self._equipment(can_park=False, can_find_home=True)
            )
        self.assertIn("cannot park", str(ctx.exception))
        self.assertIn("nothing to park", str(ctx.exception))

    async def test_load_sequence_park_is_safe_gated_and_not_a_run(self):
        """The park waits one bounded, safety-gated pass in its End area and
        carries no RUN-* bracket, so it never reads as a finished run."""
        _, api_post = await self._load_list([])

        _, payload = api_post.await_args.args
        text = json.dumps(payload)
        self.assertNotIn("RUN-STARTING", text)
        self.assertNotIn("RUN-FINISHED", text)
        self.assertEqual(text.count("SafetyMonitorCondition"), 1)
        self.assertEqual(text.count("LoopCondition"), 1)
        self.assertEqual(text.count("WaitForTimeSpan"), 1)

    async def test_park_without_a_parkable_mount_sends_nothing(self):
        """An empty park still needs a parkable mount: rejected with a
        'cannot park' / 'nothing to park' error before anything is POSTed."""
        from nina_planner.server import load_sequence

        api_post = AsyncMock(return_value={})
        with (
            patch(
                "nina_planner.tools.equipment.get_site_profile",
                AsyncMock(return_value=self._full_profile()),
            ),
            patch(
                "nina_planner.tools.equipment._get_site_equipment_status",
                AsyncMock(
                    return_value=self._equipment(can_park=False, can_find_home=False)
                ),
            ),
            patch("nina_planner.client.api_post", api_post),
            self.assertRaises(ValueError) as ctx,
        ):
            await load_sequence([])
        self.assertIn("cannot park", str(ctx.exception))
        self.assertIn("nothing to park", str(ctx.exception))
        api_post.assert_not_awaited()

    async def test_load_sequence_with_an_unparkable_mount_sends_nothing(self):
        """The park guardrail is the sequence guardrail: every loaded list
        ends with the End-area park, so a mount that cannot park fails a
        real plan exactly as it fails the empty park — before anything
        reaches N.I.N.A."""
        from nina_planner.server import load_sequence

        api_post = AsyncMock(return_value={})
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            with (
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
                patch(
                    "nina_planner.tools.equipment._get_site_equipment_status",
                    AsyncMock(
                        return_value=self._equipment(
                            can_park=False, can_find_home=False
                        )
                    ),
                ),
                patch(
                    "nina_planner.metadata.read_metadata", AsyncMock(return_value=[])
                ),
                patch("nina_planner.client.api_post", api_post),
                self.assertRaises(ValueError) as ctx,
            ):
                await load_sequence(
                    [SequenceRequest(plan=str(plan_path), action="light")]
                )

        self.assertIn("cannot park", str(ctx.exception))
        self.assertIn("nothing to park", str(ctx.exception))
        self.assertIn("Nothing was loaded", str(ctx.exception))
        api_post.assert_not_awaited()

    async def test_load_sequence_with_no_mount_reported_sends_nothing(self):
        """NINA reporting no Mount is the same rejection, park or plan: with
        nothing that can park, the End-area park is a no-op, and there is no
        scope sitting unparked through the wait stages either."""
        from nina_planner.server import load_sequence

        api_post = AsyncMock(return_value={})
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            with (
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
                patch(
                    "nina_planner.tools.equipment._get_site_equipment_status",
                    AsyncMock(return_value=ObservatoryEquipment()),
                ),
                patch(
                    "nina_planner.metadata.read_metadata", AsyncMock(return_value=[])
                ),
                patch("nina_planner.client.api_post", api_post),
            ):
                with self.assertRaises(ValueError) as park_ctx:
                    await load_sequence([])
                with self.assertRaises(ValueError) as plan_ctx:
                    await load_sequence(
                        [SequenceRequest(plan=str(plan_path), action="light")]
                    )

        for ctx in (park_ctx, plan_ctx):
            self.assertIn("nothing to park", str(ctx.exception))
            self.assertIn("Nothing was loaded", str(ctx.exception))
        api_post.assert_not_awaited()

    # capability guards: what a lights step emits must exist on the rig

    @staticmethod
    def _entry_plan_dict(**overrides) -> dict:
        """The usual entry-test plan: one pointing, one frame per group."""
        data = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [{"filter_name": "L", "total_count": 1}],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }
        data.update(overrides)
        return data

    async def _load_entry(
        self,
        action: str,
        *,
        variant: str | None = None,
        profile: ObservatoryProfile | None = None,
        equipment: ObservatoryEquipment | None = None,
        plugins: Any = None,
        plan: dict | None = None,
        request: dict[str, Any] | None = None,
        metadata: Any = None,
        progress: Any = None,
        mocks: dict | None = None,
    ) -> tuple[Any, AsyncMock]:
        """One ``load_sequence`` entry with every N.I.N.A. read stubbed.

        Returns ``(outcome, api_post)`` where ``outcome`` is the tool's
        result **or the exception it raised** — never raises itself — so each
        test can assert both what was said and whether anything was POSTed.
        ``plugins`` is what ``GET /application/plugins`` answers and
        ``metadata`` what ``read_metadata`` returns (an Exception passed to
        either makes that read raise); ``progress`` replaces plan_progress
        (a dict result, or a callable); ``request`` carries extra request
        fields (``pointing_index``, ``mode``); ``plan`` replaces the plan
        file's content (default ``_entry_plan_dict()``); and ``mocks``
        receives the ``api_get`` mock for tests that assert on the load's
        reads.
        """
        from nina_planner.server import load_sequence

        api_post = AsyncMock(return_value={})
        api_get = (
            AsyncMock(side_effect=plugins)
            if isinstance(plugins, Exception)
            else AsyncMock(return_value=plugins)
        )
        if mocks is not None:
            mocks["api_get"] = api_get
        progress_kwargs: dict[str, Any] = (
            {"side_effect": progress}
            if callable(progress)
            else {"return_value": progress}
        )
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(
                json.dumps(self._entry_plan_dict() if plan is None else plan)
            )
            entry: dict[str, Any] = {"plan": str(plan_path), "action": action}
            if variant is not None:
                entry["variant"] = variant
            entry.update(request or {})
            with (
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=profile or self._full_profile()),
                ),
                patch(
                    "nina_planner.tools.equipment._get_site_equipment_status",
                    AsyncMock(return_value=equipment or self._payload_equipment()),
                ),
                patch(
                    "nina_planner.metadata.read_metadata",
                    AsyncMock(side_effect=metadata)
                    if isinstance(metadata, Exception)
                    else AsyncMock(return_value=[] if metadata is None else metadata),
                ),
                patch("nina_planner.client.api_post", api_post),
                patch("nina_planner.client.api_get", api_get),
                (
                    patch(
                        "nina_planner.tools.sequence.plan_progress", **progress_kwargs
                    )
                    if progress is not None
                    else nullcontext()
                ),
            ):
                try:
                    outcome: Any = await load_sequence([entry])
                except Exception as exc:  # returned for the caller to assert on
                    outcome = exc
        return outcome, api_post

    async def test_light_load_rejects_a_rig_without_a_plate_solver(self):
        """Every lights step emits a slew/center, so no solver at all —
        neither plate_solver nor blind_plate_solver — fails the load."""
        profile = self._full_profile().model_copy(
            update={"plate_solver": None, "blind_plate_solver": None}
        )
        outcome, api_post = await self._load_entry("light", profile=profile)

        self.assertIsInstance(outcome, ValueError)
        message = str(outcome)
        self.assertIn("request[0]", message, "the entry is named")
        self.assertIn("plate solver", message)
        self.assertIn("slew/center", message)
        api_post.assert_not_awaited()

    async def test_light_load_rejects_a_rig_without_a_guider(self):
        """start_guiding/stop_guiding are emitted unconditionally, so a
        profile reporting no guider fails the load instead of the run."""
        profile = self._full_profile().model_copy(
            update={
                "equipment": self._full_profile().equipment.model_copy(
                    update={"has_guider": False}
                )
            }
        )
        outcome, api_post = await self._load_entry("light", profile=profile)

        self.assertIsInstance(outcome, ValueError)
        message = str(outcome)
        self.assertIn("request[0]", message)
        self.assertIn("guider", message)
        self.assertIn("start_guiding", message)
        api_post.assert_not_awaited()

    async def test_light_load_rejects_a_rig_without_a_focuser(self):
        """run_autofocus is emitted unconditionally too, so a profile
        reporting no focuser fails the load rather than the night."""
        profile = self._full_profile().model_copy(
            update={
                "equipment": self._full_profile().equipment.model_copy(
                    update={"has_focuser": False}
                )
            }
        )
        outcome, api_post = await self._load_entry("light", profile=profile)

        self.assertIsInstance(outcome, ValueError)
        message = str(outcome)
        self.assertIn("request[0]", message)
        self.assertIn("focuser", message)
        self.assertIn("run_autofocus", message)
        api_post.assert_not_awaited()

    async def test_calibration_loads_are_not_capability_checked(self):
        """Darks, bias and flats never emit center/guiding/autofocus, so the
        lights capability check must leave them alone — the same rig with no
        solver, no guider and no focuser still loads all three."""
        profile = self._full_profile().model_copy(
            update={
                "plate_solver": None,
                "blind_plate_solver": None,
                "equipment": self._full_profile().equipment.model_copy(
                    update={"has_guider": False, "has_focuser": False}
                ),
            }
        )
        for action, variant in (("dark", None), ("bias", None), ("flat", "dawn")):
            with self.subTest(action=action):
                outcome, api_post = await self._load_entry(
                    action, variant=variant, profile=profile, plugins=["Sequencer+"]
                )
                self.assertNotIsInstance(outcome, Exception, outcome)
                self.assertIn("Sequence loaded", outcome)
                api_post.assert_awaited_once()

    # Sequencer+ plugin presence at load

    async def test_light_load_requires_the_sequencer_plus_plugin(self):
        """A readable plugin list without Sequencer+ refuses the load, naming
        the plugin and the two actions that need it — before the POST."""
        outcome, api_post = await self._load_entry(
            "light", plugins=["Advanced API", "Session Metadata"]
        )

        self.assertIsInstance(outcome, ValueError)
        message = str(outcome)
        self.assertIn("Sequencer+", message)
        self.assertIn("light", message)
        self.assertIn("flat", message)
        self.assertIn("Advanced API", message, "the actual list is shown")
        api_post.assert_not_awaited()

    async def test_flat_load_requires_the_sequencer_plus_plugin(self):
        """Flats carry the safetynet's Sequencer+ End Instruction Set, so they
        are checked exactly like lights."""
        outcome, api_post = await self._load_entry(
            "flat", variant="dusk", plugins=["Advanced API"]
        )

        self.assertIsInstance(outcome, ValueError)
        self.assertIn("Sequencer+", str(outcome))
        api_post.assert_not_awaited()

    async def test_light_loads_when_the_sequencer_plus_plugin_is_present(self):
        outcome, api_post = await self._load_entry(
            "light", plugins=["Advanced API", "Sequencer+", "Session Metadata"]
        )

        self.assertNotIsInstance(outcome, Exception, outcome)
        self.assertIn("Sequence loaded", outcome)
        api_post.assert_awaited_once()

    async def test_light_load_accepts_the_alternate_plugin_spelling(self):
        """Both known spellings count as installed: README says "Sequencer+",
        the plugin's own csproj installs it as "Sequencer Plus", and no rig
        response has confirmed which one /application/plugins reports."""
        outcome, api_post = await self._load_entry(
            "light", plugins=["Advanced API", "Sequencer Plus"]
        )

        self.assertNotIsInstance(outcome, Exception, outcome)
        self.assertIn("Sequence loaded", outcome)
        api_post.assert_awaited_once()

    async def test_dark_load_never_reads_the_plugin_list(self):
        """Darks/bias emit no Sequencer+ node, so the empty-park rule extends
        to them: no plugin read, and an absent Sequencer+ changes nothing."""
        outcome, api_post = await self._load_entry("dark", plugins=[])

        self.assertNotIsInstance(outcome, Exception, outcome)
        self.assertIn("Sequence loaded", outcome)
        api_post.assert_awaited_once()

    async def test_load_continues_when_the_plugin_list_cannot_be_read(self):
        """An endpoint we cannot read — it 404s, or answers something that is
        not a list of names — must not block the night: verification is best
        effort, so the load proceeds with a logged warning instead."""
        unreadable = (RuntimeError("HTTP 404 Not Found"), {}, "", None)
        for answer in unreadable:
            with self.subTest(answer=answer):
                outcome, api_post = await self._load_entry("light", plugins=answer)

                self.assertNotIsInstance(outcome, Exception, outcome)
                self.assertIn("Sequence loaded", outcome)
                api_post.assert_awaited_once()

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
            "flat_frames": [{"filter_name": "L", "total_count": 3}],
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
            "flat": group(counts["flat"], filter_name="L"),
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
                "nina_planner.tools.equipment.get_site_profile",
                AsyncMock(return_value=profile or self._full_profile()),
            ),
            patch(
                "nina_planner.tools.equipment._get_site_equipment_status",
                AsyncMock(return_value=equipment or self._equipment()),
            ),
            patch("nina_planner.metadata.read_metadata", AsyncMock(return_value=[])),
            patch("nina_planner.client.api_post", api_post),
            # Light and flat loads read GET /application/plugins to prove the
            # Sequencer+ plugin is installed before anything is POSTed; the
            # usual rig has it. Tests that care override this patch.
            patch(
                "nina_planner.client.api_get",
                AsyncMock(return_value=["Sequencer+"]),
            ),
        ):
            if progress is None:
                result = await load_sequence(requests)
            else:
                kwargs = (
                    {"side_effect": progress}
                    if callable(progress)
                    else {"return_value": progress}
                )
                with patch("nina_planner.tools.sequence.plan_progress", **kwargs):
                    result = await load_sequence(requests)
        return result, api_post

    async def test_load_sequence_list_composes_one_sequence(self):
        """One list, one sequence: a single Start/Target/End with one deep-sky
        container per step, in list order, and an End that parks."""
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
        self.assertIn("_plan-test123-1_", steps[0]["Target"]["TargetName"])
        self.assertIn("_plan-test123-2_", steps[1]["Target"]["TargetName"])

        end_types = [i["$type"] for i in _end_park_items(payload)]
        self.assertTrue(any("ParkScope" in t for t in end_types), "End parks")
        self.assertTrue(any("WarmCamera" in t for t in end_types), "End warms")

        self.assertIn("request[0]:", result)
        self.assertIn("request[1]:", result)
        self.assertIn("Sequence loaded (2 of 2 step(s))", result)
        self.assertIn("start_sequence()", result)

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
        self.assertIn("_plan-test123-2_", steps[0]["Target"]["TargetName"])

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

    async def test_load_sequence_all_complete_needs_no_park(self):
        """Nothing sent means nothing guarded: with every entry complete the
        load POSTs no sequence, so an unparkable mount is only an error when
        a sequence — and its End-area park — would actually load."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            requests = [SequenceRequest(plan=str(plan_path), action="light")]
            result, api_post = await self._load_list(
                requests,
                progress=self._progress(light=0),
                equipment=self._equipment(can_park=False, can_find_home=False),
            )

        api_post.assert_not_awaited()
        self.assertIn("is complete", result)
        self.assertIn("Nothing to load", result)

    async def test_load_sequence_park_action_is_rejected(self):
        """There is no park action — an empty request list is the park."""
        from nina_planner.server import load_sequence

        with self.assertRaises(ValueError) as ctx:
            await load_sequence([{"action": "park"}])
        message = str(ctx.exception)
        self.assertIn("request[0]", message)
        self.assertIn("action", message)

    async def test_load_sequence_flat_requires_variant(self):
        """Dawn or dusk is a real choice, so `action='flat'` alone is an
        error naming the entry rather than an arbitrary default."""
        from nina_planner.server import load_sequence

        with self.assertRaises(ValueError) as ctx:
            await load_sequence([{"action": "flat"}])
        message = str(ctx.exception)
        self.assertIn("request[0]", message)
        self.assertIn("variant", message)

    async def test_load_sequence_variant_must_fit_the_action(self):
        """A variant belonging to another action fails loudly instead of
        being silently ignored."""
        from nina_planner.server import load_sequence

        with self.assertRaises(ValueError) as ctx:
            await load_sequence([{"action": "light", "variant": "dusk"}])
        message = str(ctx.exception)
        self.assertIn("request[0]", message)
        self.assertIn("variant", message)
        self.assertIn("light", message)

    async def test_load_sequence_dark_cover_variant_reaches_the_builder(self):
        """The requested `cover` variant reaches the darks builder unchanged
        (and never as a bias), on a rig whose flat device can cover."""
        captured_kwargs = {}

        def fake_darks(plan, equipment, profile, bias=False, variant="wheel"):
            captured_kwargs["bias"] = bias
            captured_kwargs["variant"] = variant
            return [{"$type": "darks"}]

        equipment = ObservatoryEquipment(
            mount=MountDevice(connected=True, name="m", can_park=True),
            flat_device=FlatDevice(
                connected=True,
                cover_state="Closed",
                supports_open_close=True,
            ),
        )
        with patch(
            "nina_planner.tools.sequence.target_payload_darks",
            side_effect=fake_darks,
        ):
            result, _ = await self._load_entry(
                "dark",
                variant="cover",
                profile=self._full_profile(has_flat_device=True),
                equipment=equipment,
            )
        self.assertFalse(captured_kwargs["bias"])
        self.assertEqual(captured_kwargs["variant"], "cover")
        self.assertIn("`dark` (cover) — ", result)
        self.assertNotIn("pointing", result, "calibration is plan-level")
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)

    async def test_load_sequence_dark_shutter_variant_reaches_the_builder(self):
        """The requested `shutter` variant reaches the darks builder unchanged
        (and never as a bias), on a rig whose camera has a shutter."""
        captured_kwargs = {}

        def fake_darks(plan, equipment, profile, bias=False, variant="wheel"):
            captured_kwargs["bias"] = bias
            captured_kwargs["variant"] = variant
            return [{"$type": "darks"}]

        equipment = ObservatoryEquipment(
            mount=MountDevice(connected=True, name="m", can_park=True),
            camera=CameraDevice(connected=True, name="cam", has_shutter=True),
        )
        with patch(
            "nina_planner.tools.sequence.target_payload_darks",
            side_effect=fake_darks,
        ):
            result, _ = await self._load_entry(
                "dark", variant="shutter", equipment=equipment
            )
        self.assertFalse(captured_kwargs["bias"])
        self.assertEqual(captured_kwargs["variant"], "shutter")
        self.assertIn("`dark` (shutter) — ", result)
        self.assertNotIn("pointing", result, "calibration is plan-level")
        self.assertIn("Sequence loaded (1 of 1 step(s))", result)

    # dark/bias variant capability checks (fail closed at load time)

    async def test_load_sequence_dark_wheel_needs_a_dark_filter_slot(self):
        """The default (wheel) variant is checked like an explicit one: a
        profile with no `Dark` slot fails at load, naming what this rig can
        run instead of letting N.I.N.A. discover it mid-sequence."""
        no_dark_slot = self._full_profile(
            filters=[FilterInfo(name="L", position=1, focus_offset=0)],
            has_flat_device=True,
        )
        # The rest of the rig is capable, so only `wheel` fails and the
        # message lists the two variants this rig could run instead.
        capable_rig = ObservatoryEquipment(
            camera=CameraDevice(connected=True, name="cam", has_shutter=True),
            flat_device=FlatDevice(
                connected=True, cover_state="Open", supports_open_close=True
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            with self.assertRaises(ValueError) as ctx:
                await self._load_list(
                    [SequenceRequest(plan=str(plan_path), action="dark")],
                    profile=no_dark_slot,
                    equipment=capable_rig,
                )
        message = str(ctx.exception)
        self.assertIn("request[0]", message)
        self.assertIn("'Dark' filter slot", message)
        self.assertIn("Supported here: cover, shutter", message)

    async def test_load_sequence_dark_cover_needs_a_flat_device(self):
        """`cover` closes a motorized cover, so a profile without one fails
        the load rather than emitting CloseCover instructions N.I.N.A. cannot
        run."""
        # Camera reports a shutter so only `cover` is unsupported here.
        capable_rig = ObservatoryEquipment(
            camera=CameraDevice(connected=True, name="cam", has_shutter=True)
        )
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            with self.assertRaises(ValueError) as ctx:
                await self._load_list(
                    [
                        SequenceRequest(
                            plan=str(plan_path), action="dark", variant="cover"
                        )
                    ],
                    equipment=capable_rig,
                )
        message = str(ctx.exception)
        self.assertIn("request[0]", message)
        self.assertIn("no flat device configured", message)
        self.assertIn("Supported here: wheel, shutter", message)

    async def test_load_sequence_dark_shutter_needs_a_camera_shutter(self):
        """A camera reporting HasShutter=false cannot cut the light, so
        `shutter` fails instead of producing light-contaminated darks that
        would otherwise look like a successful run."""
        # The rest of the rig is capable, so the message lists `cover` too.
        no_shutter = ObservatoryEquipment(
            camera=CameraDevice(connected=True, name="cam", has_shutter=False),
            flat_device=FlatDevice(
                connected=True, cover_state="Open", supports_open_close=True
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            with self.assertRaises(ValueError) as ctx:
                await self._load_list(
                    [
                        SequenceRequest(
                            plan=str(plan_path), action="bias", variant="shutter"
                        )
                    ],
                    profile=self._full_profile(has_flat_device=True),
                    equipment=no_shutter,
                )
        message = str(ctx.exception)
        self.assertIn("request[0]", message)
        self.assertIn("HasShutter=false", message)
        self.assertIn("Supported here: wheel, cover", message)

    async def test_load_sequence_dark_guard_fires_before_metadata_read(self):
        """The capability check runs ahead of the remaining-mode metadata
        read, so a rig that cannot take the dark fails on the variant even
        when the imaging root is unreadable — the two errors must not be
        able to mask each other."""
        from nina_planner.server import load_sequence

        no_shutter = ObservatoryEquipment(
            camera=CameraDevice(connected=True, name="cam", has_shutter=False)
        )
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(self._list_plan_dict()))
            with (
                patch(
                    "nina_planner.tools.equipment.get_site_profile",
                    AsyncMock(return_value=self._full_profile()),
                ),
                patch(
                    "nina_planner.tools.equipment._get_site_equipment_status",
                    AsyncMock(return_value=no_shutter),
                ),
                patch(
                    "nina_planner.metadata.read_metadata",
                    AsyncMock(side_effect=FileNotFoundError("no imaging root")),
                ),
                patch("nina_planner.client.api_post", AsyncMock(return_value={})),
            ):
                with self.assertRaises(ValueError) as ctx:
                    await load_sequence(
                        [
                            SequenceRequest(
                                plan=str(plan_path),
                                action="dark",
                                variant="shutter",
                            )
                        ]
                    )
        message = str(ctx.exception)
        self.assertIn("HasShutter=false", message)
        self.assertNotIn("imaging root", message)

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

        with patch("nina_planner.client.api_get", side_effect=fake_get):
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

        with patch("nina_planner.client.api_get", side_effect=fake_get):
            result = await stop_sequence()
        self.assertEqual(result, "Sequence stopped.")
        self.assertNotIn("/sequence/stop", calls)

    async def test_load_sequence_surfaces_api_error_without_retry(self):
        """No local pre-check and no retry: NINA's own refusal is what propagates."""
        from nina_planner.client import load_sequence_payload as _load_sequence_payload

        api_post = AsyncMock(side_effect=RuntimeError("Sequence is already running"))
        api_get = AsyncMock(return_value=[{"Name": "Root", "Status": "RUNNING"}])

        with (
            patch("nina_planner.client.api_post", api_post),
            patch("nina_planner.client.api_get", api_get),
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
            "nina_planner.client.api_get",
            AsyncMock(return_value=expected),
        ):
            result = await get_sequence_state(detail="raw")
        self.assertEqual(result, expected)

    async def test_get_sequence_state_rejects_unknown_detail(self):
        from nina_planner.server import get_sequence_state

        with self.assertRaisesRegex(ValueError, 'detail must be "summary" or "raw"'):
            await get_sequence_state(detail="verbose")
        # The bad detail is rejected before anything is fetched.
        with patch("nina_planner.client.api_get", AsyncMock()) as api_get:
            with self.assertRaises(ValueError):
                await get_sequence_state(detail="")
            api_get.assert_not_awaited()

    async def test_get_sequence_state_without_sequence_json(self):
        # /sequence/json is an optional merge: when it fails the summary is
        # still built from /sequence/state, with no horizon data.
        from nina_planner.server import get_sequence_state

        with patch(
            "nina_planner.client.api_get",
            AsyncMock(side_effect=[[], RuntimeError("no sequencer json")]),
        ):
            result = await get_sequence_state()
        self.assertEqual(result["step_count"], 0)
        self.assertEqual(result["steps"], [])

    async def test_get_nina_status_running_with_api(self):
        """API responsive => running."""
        from nina_planner.server import get_nina_status

        with (
            patch(
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(True, None)),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
        ):
            result = await get_nina_status()

        self.assertTrue(result["running"])
        self.assertTrue(result["lifecycle_control_available"])
        self.assertIsNone(result["probe_error"])
        self.assertIn("NINA running", result["summary"])
        self.assertIn("responsive", result["summary"])

    async def test_get_nina_status_api_down_is_not_running(self):
        """running follows the API: an unreachable REST API means NINA is not
        running, regardless of any local process visibility."""
        from nina_planner.server import get_nina_status

        with (
            patch(
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(False, "connection refused")),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
        ):
            result = await get_nina_status()

        self.assertFalse(result["running"])
        self.assertTrue(result["lifecycle_control_available"])
        # NINA's own reason is reported, not flattened to "unresponsive".
        self.assertEqual(result["probe_error"], "connection refused")
        self.assertIn("NINA not running", result["summary"])
        self.assertIn("unresponsive", result["summary"])
        self.assertIn("connection refused", result["summary"])

    async def test_get_nina_status_remote_running(self):
        """API responsive but no Windows interop: running, but lifecycle
        control is unavailable because NINA is remote."""
        from nina_planner.server import get_nina_status

        with (
            patch(
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(True, None)),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
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
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(False, "connection refused")),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=False,
            ),
        ):
            result = await get_nina_status()

        self.assertFalse(result["running"])
        self.assertFalse(result["lifecycle_control_available"])
        self.assertIn("NINA not running", result["summary"])
        self.assertIn("cannot be restarted", result["summary"])

    async def test_get_site_equipment_status_calls_log_payload(self):
        """When get_site_equipment_status runs, it calls _log_payload with
        the equipment dump. Don't mock _log_payload — let it run.
        """
        from nina_planner.server import get_site_equipment_status

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "test.log"
            with (
                patch(
                    "nina_planner.config.NINA_PLANNER_LOG",
                    str(log_path),
                ),
                patch(
                    "nina_planner.client.api_get",
                    AsyncMock(return_value={}),
                ),
            ):
                await get_site_equipment_status()
            content = log_path.read_text()
            self.assertIn("Equipment:", content)

    async def test_get_site_equipment_status_missing_device_data(self):
        """When a device is marked has_X=True but the raw response has no
        entry for it (empty dict), _build returns None without trying to
        construct the model."""
        from nina_planner.tools.equipment import (
            _get_site_equipment_status as _get_site_equipment_status,
        )

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
                    has_flat_device=False,
                )
            }
        )
        # Raw doesn't include "Mount" at all → _build returns None.
        raw = {}

        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsNone(equipment.mount)

    # flat device / camera shutter: the capabilities the dark variants check

    async def test_get_site_profile_reads_the_flat_device_settings(self):
        from nina_planner.server import get_site_profile

        def raw_with(flat_settings: dict | None) -> dict:
            raw = {
                "Name": "Test",
                "Id": "abc",
                "Description": "",
                "AstrometrySettings": {},
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
            if flat_settings is not None:
                raw["FlatDeviceSettings"] = flat_settings
            return raw

        cases = [
            ({"Id": "abc-flat"}, True),  # a flat panel is configured
            ({"Id": "No_Device"}, False),  # N.I.N.A.'s "no device" marker
            (None, False),  # section absent from the payload entirely
        ]
        for flat_settings, expected in cases:
            with self.subTest(flat_settings=flat_settings):
                with patch(
                    "nina_planner.client.api_get",
                    AsyncMock(return_value=raw_with(flat_settings)),
                ):
                    profile = await get_site_profile()
                self.assertEqual(profile.equipment.has_flat_device, expected)

    async def test_get_site_equipment_status_reads_the_flat_device(self):
        from nina_planner.tools.equipment import _get_site_equipment_status

        profile = self._full_profile(has_flat_device=True)
        # The live shape: N.I.N.A. always reports a FlatDevice block, and it
        # carries no Name at all when nothing is connected.
        raw = {
            "FlatDevice": {
                "Connected": True,
                "CoverState": "Closed",
                "SupportsOpenClose": True,
                "SupportsOnOff": True,
                "LightOn": False,
                "Brightness": 12,
            }
        }
        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            equipment = await _get_site_equipment_status(profile)
        flat = equipment.flat_device
        self.assertIsNotNone(flat)
        self.assertTrue(flat.connected)
        self.assertEqual(flat.cover_state, "Closed")
        self.assertTrue(flat.supports_open_close)
        self.assertEqual(flat.brightness, 12)

        # No FlatDevice block at all → None, which the cover check rejects
        # rather than guessing.
        with patch("nina_planner.client.api_get", AsyncMock(return_value={})):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsNone(equipment.flat_device)

    async def test_get_site_equipment_status_reads_camera_has_shutter(self):
        from nina_planner.tools.equipment import _get_site_equipment_status

        profile = self._full_profile()
        raw = {"Camera": {"Connected": True, "Name": "cam", "HasShutter": False}}
        with patch("nina_planner.client.api_get", AsyncMock(return_value=raw)):
            equipment = await _get_site_equipment_status(profile)
        self.assertIsNotNone(equipment.camera)
        self.assertIs(equipment.camera.has_shutter, False)


if __name__ == "__main__":
    unittest.main()
