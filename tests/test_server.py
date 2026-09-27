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

    async def test_naive_uses_now_tzinfo_with_z_events(self):
        """Regression: naive /time response paired with Z-suffix event
        timestamps. Before the fix, ``datetime.fromisoformat('/time')``
        returned a naive datetime, and subtracting it from the aware Z-suffix
        event timestamp raised TypeError. The fix tags the naive /time with
        the host's local tz so the subtraction works.
        """
        # Event at the same wall-clock moment as /time. We use `since=86400`
        # so the filter accepts regardless of the host's TZ offset (max ±14h,
        # always within a 24h window).
        data = [{"Time": "2026-09-22T03:00:00Z"}]

        async def fake(path):
            if path == "/time":
                return "2026-09-22 03:00:00"  # naive (no tzinfo)
            return data

        with patch("nina_planner.server._api_get", side_effect=fake):
            res = await _convert_to_met(data, since=86400, name="Time")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["timestamp"], "2026-09-22T03:00:00Z")

    async def test_aware_uses_now_tzinfo_with_naive_events(self):
        """Regression: aware /time response paired with naive event
        timestamps. The naive event gets tagged with the host's tz (via the
        aware /time's tzinfo) so the cutoff comparison works.
        """
        now = self._now()  # aware datetime with tz=UTC
        data = [{"Time": "2026-09-22 03:00:00"}]  # naive event at same moment

        async def fake(path):
            if path == "/time":
                return now.isoformat()  # "2026-09-22T03:00:00+00:00"
            return data

        with patch("nina_planner.server._api_get", side_effect=fake):
            res = await _convert_to_met(data, since=3600, name="Time")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["timestamp"], "2026-09-22T03:00:00Z")


if __name__ == "__main__":
    unittest.main()
