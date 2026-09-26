import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from nina_planner.server import _convert_to_met


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

    async def test_old_entries_filtered(self):
        now = self._now()
        data = [{"Time": "2026-09-20T00:00:00Z"}]
        with self._patch_api_get(now, data):
            res = await _convert_to_met(data, since=3600, name="Time")
        self.assertEqual(res, [])


if __name__ == "__main__":
    unittest.main()
