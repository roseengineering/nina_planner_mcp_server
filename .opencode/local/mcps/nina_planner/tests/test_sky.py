import math
import unittest
from unittest.mock import patch

from astropy.coordinates import SkyCoord
from astropy.time import Time

from nina_planner import dso_catalog
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


class SurfaceBrightnessTest(unittest.TestCase):
    def test_m57_ranks_easier_than_m33_despite_fainter_mag(self):
        # M33: mag 5.7 spread over 62x44 arcmin. M57: mag 8.8 in 1.4x1.0.
        m33 = sky._surface_brightness(5.7, 62.0, 44.0)
        m57 = sky._surface_brightness(8.8, 1.4, 1.0)
        self.assertGreater(m33, 13.5)  # advanced-tier surface
        self.assertLess(m57, 9.5)  # bright, concentrated
        self.assertLess(m57, m33)

    def test_round_object_treats_missing_minor_as_major(self):
        expected = 8.0 + 2.5 * math.log10(math.pi / 4.0 * 10.0 * 10.0)
        self.assertAlmostEqual(
            sky._surface_brightness(8.0, 10.0, 0.0), expected, places=6
        )


class DifficultyTest(unittest.TestCase):
    def test_extended_buckets(self):
        self.assertEqual(sky._difficulty(14.0, False), "beginner")
        self.assertEqual(sky._difficulty(15.0, False), "intermediate")
        self.assertEqual(sky._difficulty(16.0, False), "advanced")

    def test_compact_objects_use_magnitude_thresholds(self):
        self.assertEqual(sky._difficulty(10.5, True), "beginner")
        self.assertEqual(sky._difficulty(11.5, True), "intermediate")
        self.assertEqual(sky._difficulty(12.5, True), "advanced")

    def test_easiness_clamped_to_unit_interval(self):
        self.assertEqual(sky._easiness(5.0, False), 1.0)
        self.assertEqual(sky._easiness(20.0, False), 0.0)


class SunEventsTest(unittest.TestCase):
    # Mid-latitude site; October gives comfortable twilight spacing.
    LOCATION = None  # set in setUpClass

    @classmethod
    def setUpClass(cls):
        from astropy import units as u
        from astropy.coordinates import EarthLocation

        cls.LOCATION = EarthLocation(
            lat=45.0 * u.deg, lon=-75.0 * u.deg, height=100 * u.m
        )

    def test_twilight_ordering_and_window(self):
        # 2026-10-18 02:00 UTC = 22:00 local Oct 17: deep in the night.
        when = Time("2026-10-18T02:00:00")
        out = sky._sun_events(when, self.LOCATION)
        ev = out["events"]
        # Every event found (mid-lat October, no polar weirdness).
        for name, value in ev.items():
            self.assertIsNotNone(value, msg=f"{name} missing")
        # Sun descends through the thresholds in order after now...
        self.assertLess(ev["sunset"], ev["civil_dusk"])
        self.assertLess(ev["civil_dusk"], ev["nautical_dusk"])
        self.assertLess(ev["nautical_dusk"], ev["astronomical_dusk"])
        # ...and rises through them in reverse the next morning.
        self.assertLess(ev["astronomical_dawn"], ev["nautical_dawn"])
        self.assertLess(ev["nautical_dawn"], ev["civil_dawn"])
        self.assertLess(ev["civil_dawn"], ev["sunrise"])
        # The darkness window contains now: started in the past, ends at dawn.
        self.assertIsNotNone(out["darkness"]["start_utc"])
        self.assertLess(out["darkness"]["start_utc"], out["time_utc"])
        self.assertLess(out["time_utc"], out["darkness"]["end_utc"])
        # Astronomical dawn (window end) precedes sunrise.
        self.assertLess(out["darkness"]["end_utc"], ev["sunrise"])

    def test_night_phase_classification(self):
        # Night: phase dark, window in progress.
        night = sky._sun_events(Time("2026-10-18T02:00:00"), self.LOCATION)
        self.assertTrue(night["is_dark_now"])
        self.assertEqual(night["night_phase"], "dark")
        # Morning: 14:00 UTC = 10:00 local, hours past astronomical dawn.
        morning = sky._sun_events(Time("2026-10-18T14:00:00"), self.LOCATION)
        self.assertFalse(morning["is_dark_now"])
        self.assertEqual(morning["night_phase"], "morning")
        # Evening: 23:00 UTC = 19:00 local, before sunset let alone dusk.
        evening = sky._sun_events(Time("2026-10-18T23:00:00"), self.LOCATION)
        self.assertFalse(evening["is_dark_now"])
        self.assertEqual(evening["night_phase"], "evening")

    def test_sunset_after_noon_and_before_midnight_utc(self):
        out = sky._sun_events(Time("2026-10-18T14:00:00"), self.LOCATION)
        # Local solar noon ~17:00 UTC at lon -75; sunset must follow it.
        self.assertGreater(out["events"]["sunset"], "2026-10-18T17:00")


class RankCatalogTest(unittest.TestCase):
    def _catalog(self, when, lat, lon):
        lst = float(when.sidereal_time("apparent", longitude=lon).hour)

        def obj(name, ra_hours, dec, mag=8.0, major=10.0, minor=10.0):
            return dso_catalog.Dso(name, name, "open", ra_hours, dec, mag, major, minor)

        return [
            obj("up", lst, 60.0),  # culminating near zenith
            obj("rising", (lst + 6.0) % 24.0, 45.0),  # below now, up in ~6h
            obj("down", (lst + 12.0) % 24.0, -30.0),  # peaks at 15 deg all night
        ]

    def test_visible_later_and_too_low_partition(self):
        when = Time("2026-10-18T02:00:00")
        rows = sky._rank_catalog(
            self._catalog(when, 45.0, -75.0),
            when,
            45.0,
            -75.0,
            100.0,
            30.0,
            None,
            None,
            None,
            10,
        )
        visible = [r["name"] for r in rows["visible_now"]]
        later = [r["name"] for r in rows["later_tonight"]]
        self.assertEqual(visible, ["up"])
        self.assertEqual(later, ["rising"])
        self.assertNotIn(
            "down",
            visible + later,
            msg="peaks below the floor all night, must be excluded",
        )
        rising = rows["later_tonight"][0]
        self.assertGreaterEqual(rising["peak_alt_deg"], 30.0)
        self.assertIn("T", rising["peak_utc"])

    def test_daytime_yields_no_visible_now(self):
        # 2026-10-18 14:00 UTC = 10:00 local: sun well up, nothing visible.
        when = Time("2026-10-18T14:00:00")
        rows = sky._rank_catalog(
            self._catalog(when, 45.0, -75.0),
            when,
            45.0,
            -75.0,
            100.0,
            30.0,
            None,
            None,
            None,
            10,
        )
        self.assertFalse(rows["is_dark_now"])
        self.assertEqual(rows["visible_now"], [])
        # Peaks must still be reported, but only within darkness: the
        # culminating object ("up" near local noon) may only peak after dusk.
        for row in rows["later_tonight"]:
            self.assertGreaterEqual(
                row["peak_utc"],
                "2026-10-18T22:00",  # after that evening's dusk
                msg=f"{row['name']} peak {row['peak_utc']} outside darkness",
            )

    def test_level_and_kind_filters(self):
        when = Time("2026-10-18T02:00:00")
        catalog = self._catalog(when, 45.0, -75.0)
        base = dict(
            when=when,
            lat_deg=45.0,
            lon_deg=-75.0,
            elevation_m=100.0,
            min_altitude=30.0,
            fov_deg=None,
            level=None,
            kinds=None,
            top=10,
        )
        # "up" is 10x10 arcmin at mag 8: mu ~ 12.7 -> beginner.
        rows = sky._rank_catalog(catalog, **{**base, "level": "advanced"})
        self.assertEqual(rows["visible_now"], [])
        rows = sky._rank_catalog(catalog, **{**base, "kinds": ["galaxy"]})
        self.assertEqual(rows["visible_now"], [])
        rows = sky._rank_catalog(catalog, **{**base, "top": 1})
        self.assertLessEqual(len(rows["visible_now"]), 1)


class BuiltinCatalogTest(unittest.TestCase):
    def test_catalog_is_loaded_from_csv(self):
        self.assertEqual(len(dso_catalog.CATALOG), 496)
        self.assertEqual(
            dso_catalog.CATALOG[0],
            dso_catalog.Dso(
                "Berkeley 68", "Berkeley 68", "open", 4.7369, 42.1339, 9.8, 12, 12
            ),
        )


class BestTargetsValidationTest(unittest.TestCase):
    def test_bad_level_raises_before_profile_fetch(self):
        import asyncio

        with self.assertRaises(ValueError):
            asyncio.run(sky.best_targets(level="expert"))


if __name__ == "__main__":
    unittest.main()
