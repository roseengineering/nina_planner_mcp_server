import asyncio
import os
import unittest

import pytest

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from nina_planner.server import (
    get_events,
    get_logs,
    get_nina_status,
    get_sequence_state,
    get_site_equipment_status,
    get_site_profile,
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
