import unittest
from datetime import UTC, datetime

from nina_planner.server import _convert_to_met


class ConvertToMetTest(unittest.TestCase):
    def _now(self) -> datetime:
        return datetime(2026, 9, 22, 3, 0, 0, tzinfo=UTC)

    def test_utc_z_seconds(self):
        now = self._now()
        data = [{"Time": "2026-09-22T02:16:25Z"}]
        res = _convert_to_met(data, since=3600, now=now, name="Time")
        self.assertEqual(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    def test_offset_shifted_to_utc_z(self):
        now = self._now()
        data = [{"Time": "2026-09-21T21:16:25-05:00"}]
        res = _convert_to_met(data, since=3600, now=now, name="Time")
        self.assertEqual(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    def test_microseconds_stripped(self):
        now = self._now()
        data = [{"Time": "2026-09-22T02:16:25.123456Z"}]
        res = _convert_to_met(data, since=3600, now=now, name="Time")
        self.assertEqual(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    def test_naive_uses_now_tzinfo(self):
        now = self._now()
        data = [{"Time": "2026-09-22 02:16:25"}]
        res = _convert_to_met(data, since=3600, now=now, name="Time")
        self.assertEqual(res[0]["timestamp"], "2026-09-22T02:16:25Z")

    def test_old_entries_filtered(self):
        now = self._now()
        data = [{"Time": "2026-09-20T00:00:00Z"}]
        res = _convert_to_met(data, since=3600, now=now, name="Time")
        self.assertEqual(res, [])


if __name__ == "__main__":
    unittest.main()