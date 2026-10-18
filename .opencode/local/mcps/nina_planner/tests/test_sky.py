import unittest
from unittest.mock import patch

from astropy.coordinates import SkyCoord
from astropy.time import Time

from nina_planner.tools import sky


class AltitudesTest(unittest.TestCase):
    def test_polaris_altitude_equals_latitude_and_sorted(self):
        when = Time("2026-10-18T02:00:00")
        rows = sky._altitudes(
            [
                {"name": "south", "ra_hours": 0.0, "dec_deg": -60.0},
                {"name": "polaris", "ra_hours": 2.53, "dec_deg": 89.26},
            ],
            45.0,
            -75.0,
            100.0,
            when,
        )
        self.assertEqual([r["name"] for r in rows], ["polaris", "south"])
        self.assertAlmostEqual(rows[0]["alt_deg"], 45.0, delta=1.5)
        self.assertLess(rows[1]["alt_deg"], 0)

    def test_name_only_uses_resolver(self):
        coord = SkyCoord(2.53, 89.26, unit=("hourangle", "deg"))
        with patch.object(sky, "_resolve_name", return_value=coord) as r:
            rows = sky._altitudes([{"name": "X"}], 45.0, -75.0, 0.0, Time("2026-10-18"))
        r.assert_called_once_with("X")
        self.assertAlmostEqual(rows[0]["alt_deg"], 45.0, delta=1.5)

    def test_unresolvable_name_raises(self):
        with patch.object(sky, "_resolve_name", side_effect=RuntimeError("offline")):
            with self.assertRaises(ValueError):
                sky._altitudes([{"name": "X"}], 45.0, -75.0, 0.0, Time("2026-10-18"))


if __name__ == "__main__":
    unittest.main()
