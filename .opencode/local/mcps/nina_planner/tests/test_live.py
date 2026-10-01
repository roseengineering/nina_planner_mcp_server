import asyncio
import time
import unittest
from pathlib import Path

import pytest
from conftest import requires_windows_interop, windows_interop_available

from nina_planner.server import (
    enter_safety_standby,
    get_events,
    get_imaging_metadata,
    get_logs,
    get_nina_status,
    get_plan_progress,
    get_sequence_state,
    get_site_equipment_status,
    get_site_profile,
    run_plan,
    start_nina,
    stop_nina,
    stop_sequence,
    stow_telescope,
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


def _wait_for_nina_status(
    *, process_running: bool, api_responsive: bool, timeout: float = 120.0
):
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = asyncio.run(get_nina_status())
        if (
            status["process_running"] is process_running
            and status["api_responsive"] is api_responsive
        ):
            return status
        time.sleep(0.5)
    raise AssertionError(
        "NINA did not reach the expected status "
        f"(process_running={process_running}, api_responsive={api_responsive}) "
        f"within {timeout}s; last status: {status!r}"
    )


@pytest.mark.live
class LiveNinaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status = asyncio.run(get_nina_status())
        if status["api_responsive"]:
            # NINA is up, so the REST-only tests can run even when Windows
            # interop is unavailable (tasklist.exe cannot confirm the process).
            pass
        elif windows_interop_available():
            if not status["process_running"]:
                asyncio.run(start_nina())
            _wait_for_nina_status(process_running=True, api_responsive=True)
        else:
            # Nothing can be tested: NINA is down and there is no cmd.exe /
            # tasklist.exe available to launch it. Skip instead of erroring so
            # the cmd.exe-dependent tests do not take the whole class down.
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
        self.assertIn("process_running", result)
        self.assertIn("api_responsive", result)
        self.assertIn("simulated", result)

    @requires_windows_interop
    def test_zz_stop_and_restart_nina(self):
        initial_status = asyncio.run(get_nina_status())
        if not (initial_status["process_running"] and initial_status["api_responsive"]):
            self.skipTest("NINA must already be running and reachable for this test")

        try:
            result = asyncio.run(stop_nina())
            self.assertTrue(result["stopped"], result["summary"])
            _wait_for_nina_status(process_running=False, api_responsive=False)

            result = asyncio.run(start_nina())
            self.assertIn("launched NINA", result["summary"])
            _wait_for_nina_status(process_running=True, api_responsive=True)
        finally:
            status = asyncio.run(get_nina_status())
            if not status["process_running"]:
                asyncio.run(start_nina())
            _wait_for_nina_status(process_running=True, api_responsive=True)

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

    def test_get_sequence_state(self):
        result = asyncio.run(get_sequence_state())
        self.assertIsInstance(result, (dict, list))

    def test_get_events(self):
        result = asyncio.run(get_events(since=60))
        self.assertIsInstance(result, list)

    def test_get_logs(self):
        result = asyncio.run(get_logs(since=60))
        self.assertIsInstance(result, list)

    def test_stop_sequence(self):
        result = asyncio.run(stop_sequence())
        self.assertIsInstance(result, str)

    def test_run_light_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            run_plan(file_path=plan_path, frame_type="light", mode="full")
        )
        self.assertIn("started", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_run_dark_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            run_plan(file_path=plan_path, frame_type="dark", mode="full")
        )
        self.assertIn("started", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_run_bias_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            run_plan(file_path=plan_path, frame_type="bias", mode="full")
        )
        self.assertIn("started", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_run_dawn_flat_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            run_plan(file_path=plan_path, frame_type="dawn_flat", mode="full")
        )
        self.assertIn("started", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))
        asyncio.run(stop_sequence())

    def test_run_dusk_flat_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            run_plan(file_path=plan_path, frame_type="dusk_flat", mode="full")
        )
        self.assertIn("started", result)
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

    def test_start_safety_standby(self):
        result = asyncio.run(enter_safety_standby())
        self.assertIn("standby", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, list)
        self.assertTrue(_is_running(state), "standby sequence should be running")
        asyncio.run(stop_sequence())

    def test_enter_teardown(self):
        result = asyncio.run(stow_telescope())
        self.assertIn("Teardown", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, list)
        self.assertTrue(len(state) > 0, "teardown sequence should be loaded")
        asyncio.run(stop_sequence())
