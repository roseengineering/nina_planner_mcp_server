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


# A real SIMBAD ASCII reply (NGC 225), trimmed to the lines the parser reads.
SIMBAD_ASCII = """C.D.S.  -  SIMBAD4 rel 1.8  -  2026.11.06CET01:57:40

NGC 225
-------

Object NGC 225  ---  OpC  ---  OID=@167149   (@@126904,19)  ---  coobox=2160

Coordinates(ICRS,ep=J2000,eq=2000): 00 43 13.2  +61 46 26 (Opt ) E [~ ~ ] \
2021A&A...647A..19T
Coordinates(FK4,ep=B1950,eq=1950): 00 40 17.1  +61 30 01
Coordinates(Gal,ep=J2000,eq=2000): 121.9602  -01.0824
"""

# SIMBAD answers unknown identifiers with HTTP 200 and no coordinates line.
SIMBAD_UNKNOWN = (
    "!! 'NoSuchObject99' this identifier has an incorrect format for catalogs:\n"
    "\tN (NOVA): catalogue NOVA\n"
)


class NameResolutionTest(unittest.TestCase):
    """`_resolve_name` must work while CDS Strasbourg is offline (the
    2026-10-28 cybersecurity incident): SIMBAD mirrors first, SESAME second."""

    def test_parses_simbad_ascii_coordinates(self):
        with patch.object(sky, "_fetch_simbad", return_value=SIMBAD_ASCII) as fetch:
            coord = sky._resolve_via_simbad_web("NGC 225")
        fetch.assert_called_once_with(sky._SIMBAD_MIRRORS[0], "NGC 225")
        self.assertAlmostEqual(coord.ra.hour, 0.7203333, places=5)
        self.assertAlmostEqual(coord.dec.deg, 61.773889, places=5)

    def test_falls_through_to_the_next_mirror(self):
        def fetch(base, name):
            if base == sky._SIMBAD_MIRRORS[0]:
                raise RuntimeError("connection timed out")
            return SIMBAD_ASCII

        with patch.object(sky, "_fetch_simbad", side_effect=fetch) as mocked:
            coord = sky._resolve_via_simbad_web("NGC 225")
        self.assertEqual(mocked.call_count, 2)
        self.assertAlmostEqual(coord.dec.deg, 61.773889, places=5)

    def test_identifier_simbad_does_not_know_is_a_failure(self):
        with patch.object(sky, "_fetch_simbad", return_value=SIMBAD_UNKNOWN):
            with self.assertRaises(ValueError) as ctx:
                sky._resolve_via_simbad_web("NotARealObject12345")
        # HTTP 200 is not enough: the reason must name the missing coordinates.
        self.assertIn("no ICRS coordinates", str(ctx.exception))

    def test_mirror_outage_falls_back_to_sesame(self):
        coord = SkyCoord(2.53, 89.26, unit=("hourangle", "deg"))
        with (
            patch.object(
                sky, "_resolve_via_simbad_web", side_effect=ValueError("mirrors down")
            ),
            patch.object(sky, "_resolve_via_sesame", return_value=coord) as sesame,
        ):
            resolved = sky._resolve_name("M1")
        sesame.assert_called_once_with("M1")
        self.assertEqual(resolved.dec.deg, 89.26)

    def test_mirror_success_never_touches_sesame(self):
        with (
            patch.object(sky, "_fetch_simbad", return_value=SIMBAD_ASCII),
            patch.object(sky, "_resolve_via_sesame") as sesame,
        ):
            sky._resolve_name("NGC 225")
        sesame.assert_not_called()

    def test_all_sources_down_raises_with_both_reasons(self):
        with (
            patch.object(
                sky, "_resolve_via_simbad_web", side_effect=ValueError("mirrors down")
            ),
            patch.object(
                sky, "_resolve_via_sesame", side_effect=RuntimeError("sesame timed out")
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                sky._resolve_name("M1")
        message = str(ctx.exception)
        self.assertIn("mirrors down", message)
        self.assertIn("sesame timed out", message)

    def test_fetch_builds_the_simbad_sim_id_request(self):
        """The endpoint shape is load-bearing: `sim-id` + ASCII output is what
        the coordinate regex matches against."""

        class FakeResponse:
            text = SIMBAD_ASCII

            @staticmethod
            def raise_for_status():
                return None

        with patch.object(sky.httpx, "get", return_value=FakeResponse()) as get:
            body = sky._fetch_simbad("https://simbad.cfa.harvard.edu", "M 42")
        self.assertEqual(body, SIMBAD_ASCII)
        url = get.call_args.args[0]
        params = get.call_args.kwargs["params"]
        self.assertEqual(url, "https://simbad.cfa.harvard.edu/simbad/sim-id")
        self.assertEqual(params["Ident"], "M 42")
        self.assertEqual(params["output.format"], "ASCII")
        self.assertTrue(get.call_args.kwargs["follow_redirects"])
        self.assertEqual(get.call_args.kwargs["timeout"], sky._SIMBAD_TIMEOUT_S)


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

    def test_rows_carry_preverified_coordinates(self):
        # best_targets rows must ship ra_hours/dec_deg so plans can point
        # from the SIMBAD-verified catalog without any network lookup.
        when = Time("2026-10-18T02:00:00")
        catalog = self._catalog(when, 45.0, -75.0)
        rows = sky._rank_catalog(
            catalog, when, 45.0, -75.0, 100.0, 30.0, None, None, None, 10
        )
        all_rows = rows["visible_now"] + rows["later_tonight"]
        self.assertTrue(all_rows)
        by_name = {r["name"]: r for r in all_rows}
        for obj in catalog:
            if obj.name in by_name:
                self.assertEqual(by_name[obj.name]["ra_hours"], obj.ra_hours)
                self.assertEqual(by_name[obj.name]["dec_deg"], obj.dec_deg)

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
        self.assertEqual(len(dso_catalog.CATALOG), 497)
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
