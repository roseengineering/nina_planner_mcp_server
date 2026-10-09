import asyncio
import time
import unittest
from pathlib import Path

import pytest
from conftest import requires_windows_interop, windows_interop_available

from nina_planner.models.request import SequenceRequest
from nina_planner.server import (
    _api_get,
    get_events,
    get_imaging_metadata,
    get_logs,
    get_nina_status,
    get_plan_progress,
    get_sequence_state,
    get_site_equipment_status,
    get_site_profile,
    list_site_profiles,
    load_sequence,
    screenshot_dashboard,
    start_nina,
    stop_nina,
    stop_sequence,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _is_running(state) -> bool:
    if isinstance(state, dict):
        if state.get("Status") == "RUNNING":
            return True
        return any(_is_running(v) for v in state.values())
    if isinstance(state, list):
        return any(_is_running(item) for item in state)
    return False


def _wait_for_sequence_running(expected: bool, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = asyncio.run(get_sequence_state())
        if _is_running(state) is expected:
            return state
        time.sleep(0.25)
    raise AssertionError(
        f"sequence did not become {'running' if expected else 'stopped'} "
        f"within {timeout}s; last state: {state!r}"
    )


def _wait_for_nina_status(*, running: bool, timeout: float = 120.0):
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = asyncio.run(get_nina_status())
        if status["running"] is running:
            return status
        time.sleep(0.5)
    raise AssertionError(
        "NINA did not reach the expected status "
        f"(running={running}) within {timeout}s; last status: {status!r}"
    )


@pytest.mark.live
class LiveNinaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status = asyncio.run(get_nina_status())
        if status["running"]:
            # NINA is up, so the REST-only tests can run even when Windows
            # interop is unavailable (a remote NINA is simply not visible as a
            # local process; the REST API is authoritative).
            pass
        elif windows_interop_available():
            if not status["running"]:
                asyncio.run(start_nina())
            _wait_for_nina_status(running=True)
        else:
            # Nothing can be tested: NINA is down and this host has no Windows
            # interop to launch it (e.g. a Mac driving a remote endpoint). Skip
            # instead of erroring so the interop tests do not take the class down.
            raise unittest.SkipTest(
                "NINA is not running (REST API unresponsive) and Windows interop "
                "is unavailable to launch it (no cmd.exe/tasklist.exe on PATH)."
            )
        for _ in range(10):
            try:
                asyncio.run(get_site_equipment_status())
                break
            except RuntimeError:
                time.sleep(3)

    def test_get_nina_status(self):
        result = asyncio.run(get_nina_status())
        self.assertIsInstance(result, dict)
        self.assertIn("running", result)
        self.assertIn("lifecycle_control_available", result)
        self.assertIn("simulated", result)

    @pytest.mark.skip
    @requires_windows_interop
    def test_zz_stop_and_restart_nina(self):
        initial_status = asyncio.run(get_nina_status())
        if not initial_status["running"]:
            self.skipTest("NINA must already be running and reachable for this test")

        try:
            result = asyncio.run(stop_nina())
            self.assertTrue(result["stopped"], result["summary"])
            _wait_for_nina_status(running=False)

            result = asyncio.run(start_nina())
            self.assertIn("launched NINA", result["summary"])
            _wait_for_nina_status(running=True)
        finally:
            status = asyncio.run(get_nina_status())
            if not status["running"]:
                asyncio.run(start_nina())
            _wait_for_nina_status(running=True)

    def test_get_site_equipment_status(self):
        result = asyncio.run(get_site_equipment_status())
        result_dict = result.model_dump()
        self.assertIn("mount", result_dict)
        self.assertIn("camera", result_dict)

    def test_get_site_profile(self):
        result = asyncio.run(get_site_profile())
        result_dict = result.model_dump()
        self.assertIn("profile_name", result_dict)
        self.assertIn("site", result_dict)

    def test_list_site_profiles(self):
        result = asyncio.run(list_site_profiles())
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0, "at least one profile should exist")
        for profile in result:
            self.assertTrue(profile.profile_id)
            self.assertTrue(profile.profile_name)
        active = [p for p in result if p.active]
        self.assertEqual(len(active), 1, "exactly one profile should be active")
        # The active profile from the list should match get_site_profile.
        current = asyncio.run(get_site_profile())
        self.assertEqual(active[0].profile_id, current.profile_id)

    def test_get_sequence_state(self):
        result = asyncio.run(get_sequence_state())
        self.assertIsInstance(result, (dict, list))

    def test_get_events(self):
        result = asyncio.run(get_events(since=60))
        self.assertIsInstance(result, list)

    def test_get_logs(self):
        result = asyncio.run(get_logs(since=60))
        self.assertIsInstance(result, list)

    def test_screenshot_dashboard(self):
        # Force a known-different tab first, so the assertion below cannot pass
        # vacuously when NINA was already left on the Imaging tab.
        asyncio.run(_api_get("/application/switch-tab?tab=equipment"))
        self.assertEqual(asyncio.run(_api_get("/application/get-tab")), "equipment")

        result = asyncio.run(screenshot_dashboard())
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        path, image = result
        self.assertIsInstance(path, str)
        from mcp.server.fastmcp import Image

        self.assertIsInstance(image, Image)
        png = Path(path)
        self.assertTrue(png.is_absolute())
        self.assertEqual(png.suffix, ".png")
        self.assertTrue(png.is_file())
        # A real dashboard capture is ~150 KB; guard against a blank stub.
        data = png.read_bytes()
        self.assertGreater(len(data), 10_000)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        # The inline image block carries exactly the bytes written to disk.
        self.assertEqual(image.data, data)

        # The switch really happened: get-tab now reports the imaging tab.
        self.assertEqual(asyncio.run(_api_get("/application/get-tab")), "imaging")

    def test_stop_sequence(self):
        result = asyncio.run(stop_sequence())
        self.assertIsInstance(result, str)

    def test_run_light_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence(SequenceRequest(plan=plan_path, action="light", mode="full"))
        )
        self.assertIn("sequence loaded", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_run_dark_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence(SequenceRequest(plan=plan_path, action="dark", mode="full"))
        )
        self.assertIn("sequence loaded", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_run_bias_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence(SequenceRequest(plan=plan_path, action="bias", mode="full"))
        )
        self.assertIn("sequence loaded", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_run_dawn_flat_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence(
                SequenceRequest(plan=plan_path, action="dawn_flat", mode="full")
            )
        )
        self.assertIn("sequence loaded", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_run_dusk_flat_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence(
                SequenceRequest(plan=plan_path, action="dusk_flat", mode="full")
            )
        )
        self.assertIn("sequence loaded", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_get_plan_progress(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(get_plan_progress(file_path=plan_path))
        self.assertIsInstance(result, dict)
        self.assertIn("plan_id", result)
        self.assertIn("pointing_count", result)
        self.assertIn("target", result)
        self.assertIn("pointings", result)
        self.assertEqual(result["target"], "Veil Nebula")
        self.assertIsInstance(result["pointings"], list)
        first = result["pointings"][0]
        self.assertIn("pointing_index", first)
        self.assertIsInstance(first["frame_types"], dict)
        for frame_type in ("light", "flat", "dark", "bias"):
            self.assertIn(frame_type, first["frame_types"])

    def test_get_imaging_metadata(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(get_imaging_metadata(file_path=plan_path))
        self.assertIsInstance(result, dict)
        self.assertIn("plan_id", result)
        self.assertIn("pointing_index", result)
        self.assertIn("target", result)
        self.assertEqual(result["target"], "Veil Nebula")
        self.assertEqual(result["pointing_index"], 1)

    def test_enter_teardown(self):
        result = asyncio.run(load_sequence(SequenceRequest(action="stow")))
        self.assertIn("`stow` sequence loaded", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, list)
        self.assertTrue(len(state) > 0, "teardown sequence should be loaded")
        asyncio.run(stop_sequence())
