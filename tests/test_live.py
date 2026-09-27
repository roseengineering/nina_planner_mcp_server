import asyncio
import os
import time
import unittest

import pytest

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from pathlib import Path

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
    load_sequence_from_plan,
    start_sequence,
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


def _nina_reachable() -> bool:
    if not _HTTPX_AVAILABLE:
        return False
    endpoint = os.environ.get("NINA_ENDPOINT", "127.0.0.1:1888")
    url = f"http://{endpoint}/v2/api/time"
    try:
        resp = httpx.get(url, timeout=5.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return False


@pytest.mark.live
class LiveNinaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _nina_reachable():
            pytest.skip("NINA not reachable at configured endpoint")

    def test_get_nina_status(self):
        result = asyncio.run(get_nina_status())
        self.assertIsInstance(result, dict)
        self.assertIn("process_running", result)
        self.assertIn("api_responsive", result)
        self.assertIn("simulated", result)

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

    def test_load_light_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence_from_plan(
                file_path=plan_path, frame_type="light", mode="full"
            )
        )
        self.assertIn("light", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))

    def test_load_dark_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence_from_plan(file_path=plan_path, frame_type="dark", mode="full")
        )
        self.assertIn("dark", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))

    def test_load_bias_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence_from_plan(file_path=plan_path, frame_type="bias", mode="full")
        )
        self.assertIn("bias", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))

    def test_load_dawn_flat_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence_from_plan(
                file_path=plan_path, frame_type="dawn_flat", mode="full"
            )
        )
        self.assertIn("dawn_flat", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))

    def test_load_dusk_flat_sequence(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(
            load_sequence_from_plan(
                file_path=plan_path, frame_type="dusk_flat", mode="full"
            )
        )
        self.assertIn("dusk_flat", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, (dict, list))

    def test_get_plan_progress(self):
        plan_path = str(_FIXTURES_DIR / "veil.json")
        result = asyncio.run(get_plan_progress(file_path=plan_path))
        self.assertIsInstance(result, dict)
        self.assertIn("plan_id", result)
        self.assertIn("pointing_index", result)
        self.assertIn("target", result)
        self.assertIn("frame_types", result)
        self.assertEqual(result["target"], "Veil Nebula")
        self.assertIsInstance(result["frame_types"], dict)
        for frame_type in ("light", "flat", "dark", "bias"):
            self.assertIn(frame_type, result["frame_types"])

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

    def test_start_sequence(self):
        asyncio.run(stop_sequence())
        _wait_for_sequence_running(expected=False)

        try:
            result = asyncio.run(enter_safety_standby())
            self.assertIn("standby", result)
            _wait_for_sequence_running(expected=True)
            asyncio.run(stop_sequence())
            _wait_for_sequence_running(expected=False)

            result = asyncio.run(start_sequence())
            self.assertEqual(result, "Sequence started.")
            state = _wait_for_sequence_running(expected=True)
            self.assertIsInstance(state, list)
        finally:
            asyncio.run(stop_sequence())
            _wait_for_sequence_running(expected=False)

    def test_enter_teardown(self):
        result = asyncio.run(stow_telescope())
        self.assertIn("Teardown", result)
        state = asyncio.run(get_sequence_state())
        self.assertIsInstance(state, list)
        self.assertTrue(len(state) > 0, "teardown sequence should be loaded")
        asyncio.run(stop_sequence())
