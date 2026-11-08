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


class MoonEventsTest(unittest.TestCase):
    """`_moon_events` against published ephemerides.

    Phase instants are the USNO-published UTC times (identical everywhere on
    Earth); moonrise/moonset are Ottawa's published table, which is local
    EST (UTC-5) after the 2026-11-01 DST end.
    """

    LOCATION = None  # set in setUpClass

    @classmethod
    def setUpClass(cls):
        from astropy import units as u
        from astropy.coordinates import EarthLocation

        cls.LOCATION = EarthLocation(
            lat=45.42 * u.deg, lon=-75.70 * u.deg, height=70 * u.m
        )

    def _minutes(self, iso_a: str, iso_b: str) -> float:
        """Absolute difference between two UTC instants, in minutes."""
        from astropy import units as u

        return abs((Time(iso_a) - Time(iso_b)).to_value(u.min))

    def test_phase_instants_match_published_november_2026(self):
        out = sky._moon_events(Time("2026-11-07T04:00:00"), self.LOCATION)
        ev = out["events"]
        published = {
            "new_moon": "2026-11-09T07:02:00",
            "first_quarter": "2026-11-17T11:47:00",
            "full_moon": "2026-11-24T14:53:00",
            "last_quarter": "2026-12-01T06:09:00",
        }
        for name, expected in published.items():
            self.assertIsNotNone(ev[name], msg=f"{name} missing")
            # Hourly sampling + linear interpolation lands well inside 2 min.
            self.assertLess(
                self._minutes(ev[name], expected),
                2.0,
                msg=f"{name}: got {ev[name]}, published {expected}",
            )

    def test_phase_instants_are_ordered_and_in_the_future(self):
        when = Time("2026-11-07T04:00:00")
        ev = sky._moon_events(when, self.LOCATION)["events"]
        # Every instant is strictly after now, and they advance in phase order.
        for name in ("new_moon", "first_quarter", "full_moon", "last_quarter"):
            self.assertGreater(ev[name], when.utc.isot, msg=name)
        self.assertLess(ev["new_moon"], ev["first_quarter"])
        self.assertLess(ev["first_quarter"], ev["full_moon"])
        self.assertLess(ev["full_moon"], ev["last_quarter"])
        # Successive principal phases are ~7.4 d apart on average, but the
        # real spacing swings roughly 6.8-8.2 d either side of that (this
        # month's new->first quarter ran 8.2 d), so assert the range, not
        # the mean.
        from astropy import units as u

        gaps = [
            (Time(ev[b]) - Time(ev[a])).to_value(u.day)
            for a, b in (
                ("new_moon", "first_quarter"),
                ("first_quarter", "full_moon"),
                ("full_moon", "last_quarter"),
            )
        ]
        for gap in gaps:
            self.assertGreater(gap, 6.5, msg=f"gap {gap} d too short")
            self.assertLess(gap, 8.5, msg=f"gap {gap} d too long")

    def test_moonrise_and_moonset_match_published_ottawa_table(self):
        # fullmoonphase.com's Ottawa table, Nov 2026, local EST (UTC-5) after
        # the Nov 1 DST end. Asking at local midnight returns that day's rise
        # AND set in one call (both fall after local midnight), so each day
        # costs a single ephemeris pass. Worst observed error across these
        # four days plus an 11-day sweep: 0.7 min.
        from datetime import datetime, timedelta

        published = {
            6: ("04:01", "15:10"),
            7: ("05:11", "15:30"),
            8: ("06:21", "15:54"),
            9: ("07:31", "16:22"),
        }
        est = timedelta(hours=5)  # EST = UTC-5, so UTC = local + 5h
        for day, (rise_local, set_local) in published.items():
            midnight_utc = datetime(2026, 11, day) + est

            def expected(local: str, midnight: datetime = midnight_utc) -> str:
                h, m = map(int, local.split(":"))
                return (midnight + timedelta(hours=h, minutes=m)).isoformat()

            ev = sky._moon_events(Time(midnight_utc), self.LOCATION)["events"]
            self.assertIsNotNone(ev["moonrise"], msg=f"Nov {day} rise missing")
            self.assertIsNotNone(ev["moonset"], msg=f"Nov {day} set missing")
            self.assertLess(
                self._minutes(ev["moonrise"], expected(rise_local)),
                3.0,
                msg=f"Nov {day} moonrise {ev['moonrise']} vs {rise_local} local",
            )
            self.assertLess(
                self._minutes(ev["moonset"], expected(set_local)),
                3.0,
                msg=f"Nov {day} moonset {ev['moonset']} vs {set_local} local",
            )

    def test_moon_up_now_agrees_with_event_order(self):
        # Moon below the horizon: the next crossing must be the rise.
        down = sky._moon_events(Time("2026-11-07T04:00:00"), self.LOCATION)
        self.assertFalse(down["moon_up_now"])
        self.assertLess(down["events"]["moonrise"], down["events"]["moonset"])
        # Moon above it: the set comes first, then the next day's rise.
        up = sky._moon_events(Time("2026-10-18T02:00:00"), self.LOCATION)
        self.assertTrue(up["moon_up_now"])
        self.assertGreater(up["altitude_deg"], 0.0)
        self.assertLess(up["events"]["moonset"], up["events"]["moonrise"])

    def test_every_event_is_in_the_future(self):
        when = Time("2026-11-07T04:00:00")
        ev = sky._moon_events(when, self.LOCATION)["events"]
        for name, value in ev.items():
            self.assertIsNotNone(value, msg=f"{name} missing")
            self.assertGreater(value, when.utc.isot, msg=name)

    def test_illumination_and_phase_name_at_principal_instants(self):
        # Straight off the elongation, no ephemeris needed.
        cases = {
            0.0: ("new", 0.0, False),
            90.0: ("first_quarter", 0.5, True),
            180.0: ("full", 1.0, False),
            270.0: ("last_quarter", 0.5, False),
            45.0: ("waxing_crescent", None, True),
            135.0: ("waxing_gibbous", None, True),
            225.0: ("waning_gibbous", None, False),
            315.0: ("waning_crescent", None, False),
        }
        for elong, (name, illum, waxing) in cases.items():
            phase = sky._phase_from_elongation(elong)
            self.assertEqual(phase["name"], name, msg=f"elong={elong}")
            self.assertEqual(phase["waxing"], waxing, msg=f"elong={elong}")
            if illum is not None:
                self.assertAlmostEqual(phase["illumination_fraction"], illum, places=3)
            self.assertGreaterEqual(phase["elongation_deg"], 0.0)
            self.assertLess(phase["elongation_deg"], 360.0)

    def test_illumination_bands_split_on_fraction_not_angle(self):
        # The band is on ILLUMINATION, not elongation: half-lit (k=0.5) is
        # exactly 90 deg, so "quarter" means "looks like a half disc". That
        # matches published daily calendars, which a45 deg-sector split does
        # not -- on 2026-11-16 (elong ~72 deg, 35% lit) almanacs say
        # "waxing crescent", and on 11-18 (elong ~97 deg, 56% lit) they say
        # "waxing gibbous", while equal sectors would call both "quarter".
        # Quarter band is k in [0.47, 0.53] = 86.6-93.4 deg.
        self.assertEqual(sky._phase_from_elongation(88.0)["name"], "first_quarter")
        self.assertEqual(sky._phase_from_elongation(92.0)["name"], "first_quarter")
        # Just outside it, still under half lit: a crescent.
        self.assertEqual(sky._phase_from_elongation(85.0)["name"], "waxing_crescent")
        self.assertEqual(sky._phase_from_elongation(70.0)["name"], "waxing_crescent")
        # Just past the band on the far side: already gibbous.
        self.assertEqual(sky._phase_from_elongation(95.0)["name"], "waxing_gibbous")
        # The new/full plateaus are wide enough to cover their fuzzy edges.
        self.assertEqual(sky._phase_from_elongation(355.0)["name"], "new")
        self.assertEqual(sky._phase_from_elongation(175.0)["name"], "full")
        # Just past the new plateau: already a crescent again.
        self.assertEqual(sky._phase_from_elongation(330.0)["name"], "waning_crescent")

    def test_elongation_wraps_into_half_open_range(self):
        # 360 is congruent to 0 (new) and must never be reported as 360.0.
        phase = sky._phase_from_elongation(360.0)
        self.assertEqual(phase["elongation_deg"], 0.0)
        self.assertEqual(phase["name"], "new")
        # Negative input normalises too (unwrapped series can go below zero).
        self.assertEqual(sky._phase_from_elongation(-90.0)["name"], "last_quarter")
        # A hair under 360 rounds to 360 before the modulo -- re-wrap it.
        self.assertLess(sky._phase_from_elongation(359.9999)["elongation_deg"], 360.0)

    def test_age_days_is_zero_at_new_moon_and_half_at_full(self):
        # Age counts back to the PREVIOUS new moon, so at a new moon it is a
        # whole synodic month (~29.5 d), not zero, and small just after one.
        at_new = sky._moon_events(Time("2026-11-09T07:02:11"), self.LOCATION)
        self.assertAlmostEqual(at_new["phase"]["age_days"], 29.53, delta=0.3)
        just_after = sky._moon_events(Time("2026-11-10T00:00:00"), self.LOCATION)
        self.assertAlmostEqual(just_after["phase"]["age_days"], 0.7, delta=0.3)
        # At full moon the age is one new->full interval. That is 14.77 d on
        # average but ranges13.9-15.9 d in reality -- this month's ran 15.33
        # d -- so assert the physical range rather than the mean.
        at_full = sky._moon_events(Time("2026-11-24T14:53:43"), self.LOCATION)
        self.assertGreater(at_full["phase"]["age_days"], 13.5)
        self.assertLess(at_full["phase"]["age_days"], 16.5)
        # Age must increase monotonically across the cycle.
        self.assertLess(just_after["phase"]["age_days"], at_full["phase"]["age_days"])
        self.assertLess(at_full["phase"]["age_days"], at_new["phase"]["age_days"])

    def test_phase_reports_name_illumination_and_age(self):
        phase = sky._moon_events(Time("2026-11-07T04:00:00"), self.LOCATION)["phase"]
        for key in ("name", "illumination_fraction", "waxing", "elongation_deg"):
            self.assertIn(key, phase)
        # Waning crescent: less than half lit, past full, ~2 days from new.
        self.assertEqual(phase["name"], "waning_crescent")
        self.assertFalse(phase["waxing"])
        self.assertLess(phase["illumination_fraction"], 0.5)
        self.assertGreater(phase["age_days"], 27.0)

    def test_distance_is_topocentric_and_position_is_reported(self):
        out = sky._moon_events(Time("2026-11-07T04:00:00"), self.LOCATION)
        # Topocentric: within Earth's radius of the geocentric figure, and
        # never below the Moon's perigee-ish distance.
        self.assertGreater(out["distance_km"], 350000)
        self.assertLess(out["distance_km"], 410000)
        self.assertTrue(-90.0 <= out["altitude_deg"] <= 90.0)
        self.assertTrue(0.0 <= out["azimuth_deg"] < 360.0)
        self.assertEqual(out["time_utc"], "2026-11-07T04:00:00.000")
        self.assertEqual(out["site"]["latitude_deg"], 45.42)

    def test_polar_site_can_report_no_crossing(self):
        # North Pole in the sample window: if the Moon never crosses the
        # horizon the entry is null, never an invented time.
        from astropy import units as u
        from astropy.coordinates import EarthLocation

        pole = EarthLocation(lat=90.0 * u.deg, lon=0.0 * u.deg, height=0 * u.m)
        ev = sky._moon_events(Time("2026-11-07T04:00:00"), pole)["events"]
        self.assertIsNone(ev["moonrise"])
        self.assertIsNone(ev["moonset"])
        # Phase instants are geocentric: they never depend on the site.
        self.assertIsNotNone(ev["new_moon"])
        self.assertIsNotNone(ev["full_moon"])


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
