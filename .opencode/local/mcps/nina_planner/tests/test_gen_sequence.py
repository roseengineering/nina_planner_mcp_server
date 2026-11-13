"""CLI tests for scripts/gen_sequence.py — the plan → sequence generator.

The script lives in the repo's ``scripts/`` directory, outside the package,
so it is loaded by path here. Everything it builds is the package's own code
(``target_payload_*`` → ``build_sequence_many``); only the two live N.I.N.A.
reads — profile and equipment — are stubbed, so no test run needs a
reachable N.I.N.A.

stdout is asserted to be pure JSON in every successful case: that is the
script's contract (notes, warnings and errors belong on stderr), and it is
what makes ``> seq.json`` and ``--load`` safe to combine.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from nina_planner.models.camera import CameraDevice
from nina_planner.models.mount import MountDevice
from nina_planner.models.observatory import ObservatoryEquipment
from nina_planner.models.profile import FilterInfo, ObservatoryProfile

# tests/ → nina_planner/ → mcps/ → local/ → .opencode/ → repo root
SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "gen_sequence.py"

PLAN_DATA = {
    "plan_id": "plan-test123",
    "target": "Test Target",
    "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
    "light_frames": [
        {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
    ],
    "flat_frames": [{"filter_name": "L", "total_count": 5}],
    "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 5}],
    "bias_frames": [{"total_count": 5}],
}


def _load_script():
    spec = importlib.util.spec_from_file_location("gen_sequence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile(*, file_pattern: str = "$$TARGETNAME$$") -> ObservatoryProfile:
    """The rig the stubs report: filters the plan uses, a parking mount."""
    return ObservatoryProfile(
        profile_name="test",
        profile_id="profile-test123",
        site={},
        optics={},
        filters=[
            FilterInfo(name="L", position=3),
            FilterInfo(name="Dark", position=4),
        ],
        image_save_path="C:\\images",
        file_pattern=file_pattern,
        equipment={
            "has_mount": True,
            "has_camera": True,
            "has_focuser": True,
            "has_guider": True,
            "has_filter_wheel": True,
            "has_flat_device": False,
            "has_rotator": False,
            "has_dome": False,
            "has_weather": False,
            "has_safety_monitor": False,
            "has_switch": False,
        },
    )


def _equipment() -> ObservatoryEquipment:
    return ObservatoryEquipment(
        mount=MountDevice(connected=True, name="m", can_park=True),
        camera=CameraDevice(connected=True, name="cam", can_set_temperature=True),
    )


@unittest.skipUnless(SCRIPT.is_file(), f"{SCRIPT} is not present")
class GenSequenceScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = _load_script()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.plan = Path(tmp.name) / "plan.json"
        self.plan.write_text(json.dumps(PLAN_DATA), encoding="utf-8")

    def _run(self, *argv: str, profile=None, equipment=None):
        """Run ``main(argv)`` with the live N.I.N.A. reads stubbed.

        Returns ``(exit_code, stdout, stderr)``. argparse's own usage errors
        raise ``SystemExit(2)`` out of ``main`` — the caller asserts on that.
        """
        profile = profile or _profile()
        equipment = equipment or _equipment()

        async def fake_profile():
            return profile

        async def fake_equipment(_profile_arg):
            return equipment

        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(self.script, "get_site_profile", fake_profile),
            patch.object(self.script, "_get_site_equipment_status", fake_equipment),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = self.script.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    # ------------------------------------------------------------ defaults

    def test_default_action_is_lights_written_to_stdout(self):
        code, out, err = self._run(str(self.plan))

        self.assertEqual(code, 0)
        # stdout is exactly the sequence JSON — parseable, nothing else.
        sequence = json.loads(out)
        self.assertIsInstance(sequence, dict)
        # The lights container and the plan's attribution token.
        self.assertIn("Wait For Object", out)
        self.assertIn("_plan-test123_", out)
        self.assertIn("mode=full", err)
        self.assertNotIn("✅", err)

    def test_sequence_shape_matches_what_load_sequence_would_send(self):
        code, out, _ = self._run(str(self.plan))

        self.assertEqual(code, 0)
        sequence = json.loads(out)
        # One root carrying Start/Target/End areas, with the closing park in
        # the End area — the shape load_sequence POSTs.
        self.assertEqual(sequence["Name"], "Root Sequence")
        areas = sequence["Items"]["$values"]
        self.assertEqual(
            [area.get("Name") for area in areas],
            ["Start Sequence", "Target Sequence", "End Sequence"],
        )

    # ------------------------------------------------------------ actions

    def test_action_dark_builds_a_dark_sequence(self):
        code, out, err = self._run(str(self.plan), "--action", "dark")

        self.assertEqual(code, 0)
        self.assertIn("Dark Sequence", out)
        self.assertNotIn("Wait For Object", out)
        self.assertIn("`dark`", err)

    def test_action_bias_builds_a_bias_sequence(self):
        code, out, err = self._run(str(self.plan), "--action", "bias")

        self.assertEqual(code, 0)
        self.assertIn("Bias Sequence", out)
        self.assertIn("`bias`", err)

    def test_action_flat_dawn_builds_dawn_flats(self):
        code, out, err = self._run(
            str(self.plan), "--action", "flat", "--variant", "dawn"
        )

        self.assertEqual(code, 0)
        self.assertIn("Dawn Flats", out)
        self.assertIn("`flat` (dawn)", err)

    # ------------------------------------------------------- usage errors

    def test_flat_without_variant_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(str(self.plan), "--action", "flat")

        self.assertEqual(ctx.exception.code, 2)

    def test_variant_on_a_light_action_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(str(self.plan), "--action", "light", "--variant", "wheel")

        self.assertEqual(ctx.exception.code, 2)

    def test_pointing_index_on_calibration_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(str(self.plan), "--action", "dark", "--pointing-index", "2")

        self.assertEqual(ctx.exception.code, 2)

    # -------------------------------------------------------- refusals

    def test_missing_plan_file_exits_1_with_empty_stdout(self):
        missing = self.plan.parent / "nope.json"
        code, out, err = self._run(str(missing))

        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("✗", err)

    def test_light_without_targetname_in_filepattern_is_refused(self):
        profile = _profile(file_pattern="$$DATETIME$$")
        code, out, err = self._run(str(self.plan), profile=profile)

        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("$$TARGETNAME$$", err)

    def test_allow_unattributed_bypasses_the_filepattern_guard(self):
        profile = _profile(file_pattern="$$DATETIME$$")
        code, out, err = self._run(
            str(self.plan), "--allow-unattributed", profile=profile
        )

        self.assertEqual(code, 0)
        self.assertIn("Wait For Object", out)
        self.assertIn("mode=full", err)

    def test_pointing_index_out_of_range_exits_1(self):
        code, out, err = self._run(str(self.plan), "--pointing-index", "9")

        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("out of range", err)

    def test_mount_that_cannot_park_is_refused(self):
        equipment = ObservatoryEquipment(
            mount=MountDevice(connected=True, name="m", can_park=False),
            camera=CameraDevice(connected=True, name="cam"),
        )
        code, out, err = self._run(str(self.plan), equipment=equipment)

        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("cannot park", err)

    # ------------------------------------------------------- remaining mode

    def test_remaining_mode_with_no_frames_acquired_generates_them(self):
        async def no_rows():
            return []

        with patch.object(self.script.metadata, "read_metadata", no_rows):
            code, out, err = self._run(str(self.plan), "--mode", "remaining")

        self.assertEqual(code, 0)
        self.assertIn("Wait For Object", out)
        self.assertIn("5 frame(s) to acquire", err)

    def test_remaining_mode_reports_a_complete_plan_and_prints_nothing(self):
        async def no_rows():
            return []

        def fake_progress(_plan, _rows, **_kwargs):
            return {
                "light": [
                    {
                        "remaining_count": 0,
                        "total_count": 5,
                        "acquired_count": 5,
                    }
                ],
                "flat": [],
                "dark": [],
                "bias": [],
            }

        with (
            patch.object(self.script.metadata, "read_metadata", no_rows),
            patch.object(self.script, "plan_progress", fake_progress),
        ):
            code, out, err = self._run(str(self.plan), "--mode", "remaining")

        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("is complete", err)

    def test_remaining_mode_unreadable_metadata_suggests_full(self):
        async def broken_rows():
            raise FileNotFoundError("imaging root is not mounted")

        with patch.object(self.script.metadata, "read_metadata", broken_rows):
            code, out, err = self._run(str(self.plan), "--mode", "remaining")

        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("--mode full", err)

    # -------------------------------------------------------------- --load

    def test_load_posts_the_sequence_and_reports_acceptance(self):
        posted: list[dict] = []

        async def fake_load(sequence):
            posted.append(sequence)

        with patch.object(self.script.client, "load_sequence_payload", fake_load):
            code, out, err = self._run(str(self.plan), "--load")

        self.assertEqual(code, 0)
        self.assertEqual(len(posted), 1)
        # What N.I.N.A. received is exactly what stdout printed.
        self.assertEqual(posted[0], json.loads(out))
        self.assertIn("accepted by N.I.N.A.", err)

    def test_load_refusal_is_reported_with_ninas_own_error(self):
        async def refuse(_sequence):
            raise RuntimeError("Sequence is running")

        with patch.object(self.script.client, "load_sequence_payload", refuse):
            code, out, err = self._run(str(self.plan), "--load")

        self.assertEqual(code, 1)
        # The JSON is still printed: the build succeeded, the load did not.
        self.assertIn("Wait For Object", out)
        self.assertIn("refused", err)
        self.assertIn("Sequence is running", err)

    def test_without_load_nothing_is_posted(self):
        async def unexpected_load(_sequence):
            raise AssertionError("load_sequence_payload must not be called")

        with patch.object(self.script.client, "load_sequence_payload", unexpected_load):
            code, out, _ = self._run(str(self.plan))

        self.assertEqual(code, 0)
        self.assertIn("Wait For Object", out)


# ---------------------------------------------------------------- over HTTP
#
# The tests above stub the script's own callables; this last group runs the
# script as a real subprocess against a local stub of N.I.N.A.'s Advanced
# API, so the whole wire path is exercised: NINA_ENDPOINT → GET profile →
# GET equipment → build → POST /v2/api/sequence/load → N.I.N.A.'s envelope.
# That is the path `--load` exists for: "does N.I.N.A. accept this?".

_PROFILE_PAYLOAD = {
    "Name": "stub",
    "Id": "stub-profile",
    "AstrometrySettings": {
        "Latitude": 51.5,
        "Longitude": -0.1,
        "Elevation": 50,
        "HorizonFilePath": "",
    },
    "TelescopeSettings": {
        "Name": "scope",
        "MountName": "mount",
        "FocalLength": 500,
        "FocalRatio": 5.0,
    },
    "CameraSettings": {"Id": "cam", "PixelSize": 2.4, "Gain": 100},
    "FilterWheelSettings": {
        "Id": "fw",
        "FilterWheelFilters": [
            {"Name": "L", "Position": 3, "FocusOffset": 0},
            {"Name": "Dark", "Position": 4, "FocusOffset": 0},
        ],
    },
    "ImageFileSettings": {
        "FilePath": "C:/images",
        "FilePattern": "$$TARGETNAME$$",
    },
    "PlateSolveSettings": {},
    "FramingAssistantSettings": {"CameraWidth": 1920, "CameraHeight": 1080},
    "GuiderSettings": {},
    "RotatorSettings": {},
    "DomeSettings": {},
    "WeatherDataSettings": {},
    "SafetyMonitorSettings": {},
    "FocuserSettings": {},
    "SwitchSettings": {},
    "FlatDeviceSettings": {},
}

_EQUIPMENT_INFO = {
    "Mount": {
        "Connected": True,
        "Name": "mount",
        "CanPark": True,
        "CanFindHome": True,
    },
    "Camera": {"Connected": True, "Name": "cam", "CanSetTemperature": True},
}


class _StubNina(BaseHTTPRequestHandler):
    """Just enough of N.I.N.A.'s Advanced API for gen_sequence.py."""

    loaded: list = []
    load_error: str | None = None

    def log_message(self, *_args):  # keep pytest's output clean
        pass

    def _send(self, response=None, error: str = "", status: int = 200) -> None:
        body = json.dumps(
            {
                "Response": response if response is not None else "",
                "Error": error,
                "StatusCode": status,
                "Success": not error,
                "Type": "API",
            }
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/v2/api/profile/show":
            self._send(_PROFILE_PAYLOAD)
        elif path == "/v2/api/profile/horizon":
            self._send({})
        elif path == "/v2/api/equipment/info":
            self._send(_EQUIPMENT_INFO)
        else:
            self._send(error=f"Unknown endpoint {path}", status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/v2/api/sequence/load":
            self._send(error=f"Unknown endpoint {self.path}", status=404)
            return
        if self.load_error:
            self._send(error=self.load_error, status=409)
            return
        self.loaded.append(body)
        self._send("Sequence loaded")


@unittest.skipUnless(SCRIPT.is_file(), f"{SCRIPT} is not present")
class GenSequenceLoadOverHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        _StubNina.loaded = []
        _StubNina.load_error = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubNina)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # LIFO: shut the serving loop down first, then close the socket.
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.plan = Path(tmp.name) / "plan.json"
        self.plan.write_text(json.dumps(PLAN_DATA), encoding="utf-8")

    def _run_subprocess(self, *argv: str) -> subprocess.CompletedProcess:
        port = self.server.server_address[1]
        env = dict(os.environ, NINA_ENDPOINT=f"127.0.0.1:{port}")
        return subprocess.run(
            [sys.executable, str(SCRIPT), *argv],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    def test_load_posts_the_built_sequence_to_nina(self):
        proc = self._run_subprocess(str(self.plan), "--load")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("accepted by N.I.N.A.", proc.stderr)
        # What the stub received over the wire is exactly what stdout holds.
        self.assertEqual(_StubNina.loaded, [json.loads(proc.stdout)])
        self.assertIn("Wait For Object", proc.stdout)

    def test_load_surfaces_ninas_own_refusal(self):
        _StubNina.load_error = "Sequence is running"
        proc = self._run_subprocess(str(self.plan), "--load")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("refused", proc.stderr)
        self.assertIn("Sequence is running", proc.stderr)
        # The build succeeded, so the JSON is still on stdout.
        self.assertIn("Wait For Object", proc.stdout)
        self.assertEqual(_StubNina.loaded, [])

    def test_without_load_nothing_is_posted(self):
        proc = self._run_subprocess(str(self.plan))

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Wait For Object", proc.stdout)
        self.assertEqual(_StubNina.loaded, [])


if __name__ == "__main__":
    unittest.main()
