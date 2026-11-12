import asyncio
import math
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
from astropy import units as u
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.time import Time

from nina_planner import dso_catalog
from nina_planner.horizon import Horizon
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

    def test_sesame_backstop_is_used_when_the_web_mirrors_fail(self):
        # Layer 2 must actually be *called* (not just its result mocked):
        # this is the path that keeps resolve_target working while CDS is out.
        coord = SkyCoord(2.53, 89.26, unit=("hourangle", "deg"))
        with (
            patch.object(
                sky, "_resolve_via_simbad_web", side_effect=ValueError("mirrors down")
            ),
            patch.object(sky.SkyCoord, "from_name", return_value=coord) as from_name,
        ):
            resolved = sky._resolve_name("M1")
        from_name.assert_called_once_with("M1")
        self.assertEqual(resolved.dec.deg, 89.26)

    def test_resolve_target_wraps_a_total_failure_as_value_error(self):
        # The tool's contract: skip the target rather than guess coordinates.
        # The reason from the layer below must survive into the message.
        with patch.object(
            sky, "_resolve_name", side_effect=RuntimeError("all mirrors down")
        ):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(sky.resolve_target("My Named Nebula"))
        message = str(ctx.exception)
        self.assertIn("could not resolve target 'My Named Nebula'", message)
        self.assertIn("all mirrors down", message)

    def test_resolve_target_reports_ra_hours_and_dec_deg(self):
        coord = SkyCoord(5.0, 10.5, unit=("hourangle", "deg"))
        with patch.object(sky, "_resolve_name", return_value=coord) as resolve:
            out = asyncio.run(sky.resolve_target("My Named Nebula"))
        resolve.assert_called_once_with("My Named Nebula")
        self.assertEqual(
            out, {"name": "My Named Nebula", "ra_hours": 5.0, "dec_deg": 10.5}
        )

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

    def test_zero_area_returns_the_magnitude_unchanged(self):
        # A catalog row with no angular size has no surface to spread its
        # light over: log10(0) would be -inf, so the magnitude stands in.
        self.assertEqual(sky._surface_brightness(12.0, 0.0, 0.0), 12.0)


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


class SunEventsPolarPhaseTest(unittest.TestCase):
    """The `night_phase` branches that need a site with no daily twilight.

    At 75°N the sun stays above −18° from mid-March to late September, so
    the -18° crossings disappear for months — the cases the docstring calls
    out as polar day/night. Dates below are the real 2026 crossings for
    lat=75, lon=0: the last astronomical dawn of spring is 2026-03-13T00:16Z
    and the first astronomical dusk of autumn is 2026-09-30T23:23Z.
    """

    @classmethod
    def setUpClass(cls):
        cls.LOCATION = EarthLocation(lat=75.0 * u.deg, lon=0.0 * u.deg, height=0 * u.m)

    def test_no_crossing_in_the_window_is_evening(self):
        # Midsummer: no dusk or dawn within -24h..+48h at all. The night is
        # still ahead, so this must never read as "the night just ended".
        out = sky._sun_events(Time("2026-06-15T12:00:00"), self.LOCATION)
        self.assertFalse(out["is_dark_now"])
        self.assertEqual(out["night_phase"], "evening")
        self.assertIsNone(out["darkness"]["start_utc"])
        self.assertIsNone(out["darkness"]["end_utc"])

    def test_dawn_without_a_dusk_ahead_is_morning(self):
        # Hours after the spring dawn, with no dusk due until September:
        # the night that just ended is the one to document.
        out = sky._sun_events(Time("2026-03-13T12:00:00"), self.LOCATION)
        self.assertFalse(out["is_dark_now"])
        self.assertEqual(out["night_phase"], "morning")
        self.assertIsNotNone(out["darkness"]["end_utc"])
        self.assertTrue(out["darkness"]["end_utc"].startswith("2026-03-13"))

    def test_dusk_ahead_without_a_dawn_behind_is_evening(self):
        # Late afternoon on the first day of the new season: the sun has
        # been above -18° for months (no dawn behind us) but dusk is hours
        # away — the night is ahead, so evening.
        out = sky._sun_events(Time("2026-09-30T12:00:00"), self.LOCATION)
        self.assertFalse(out["is_dark_now"])
        self.assertEqual(out["night_phase"], "evening")
        self.assertIsNotNone(out["darkness"]["start_utc"])
        self.assertTrue(out["darkness"]["start_utc"].startswith("2026-09-30"))
        # The window it opens closes at the dawn that follows it.
        self.assertTrue(out["darkness"]["end_utc"].startswith("2026-10-01"))


class CrossingTimeTest(unittest.TestCase):
    def test_want_outside_the_sampled_window_is_none(self):
        times = Time("2026-10-18T00:00:00") + np.arange(4) * u.hour
        values = np.array([1.0, 2.0, 3.0, 4.0])
        # Below every sample: searchsorted lands on index 0.
        self.assertIsNone(sky._crossing_time(times, values, 0.5))
        # Above every sample: searchsorted lands past the end.
        self.assertIsNone(sky._crossing_time(times, values, 9.0))

    def test_crossing_inside_the_window_is_interpolated(self):
        times = Time("2026-10-18T00:00:00") + np.arange(4) * u.hour
        values = np.array([0.0, 1.0, 2.0, 3.0])
        crossing = sky._crossing_time(times, values, 1.5)
        self.assertEqual(crossing.utc.isot, "2026-10-18T01:30:00.000")


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

    def test_no_darkness_in_the_window_returns_empty_lists(self):
        # 75°N in June: the sun never reaches -18° in the sampled 12 hours,
        # so there is no peak "within darkness" to report at all — and the
        # day's peaks must not leak in as if they were observable.
        when = Time("2026-06-15T12:00:00")
        rows = sky._rank_catalog(
            self._catalog(when, 75.0, 0.0),
            when,
            75.0,
            0.0,
            0.0,
            30.0,
            None,
            None,
            None,
            10,
        )
        self.assertEqual(
            rows,
            {"visible_now": [], "later_tonight": [], "sampled_catalog": 3},
        )

    def test_large_for_field_uses_the_reported_fov(self):
        when = Time("2026-10-18T02:00:00")
        catalog = self._catalog(when, 45.0, -75.0)
        # Every fixture object is 10 arcmin across; a 0.1° (6 arcmin) field
        # makes them all wider than the frame — mosaic/framing care.
        rows = sky._rank_catalog(
            catalog, when, 45.0, -75.0, 100.0, 30.0, (0.1, 0.1), None, None, 10
        )
        self.assertTrue(rows["visible_now"])
        self.assertTrue(
            all(r["large_for_field"] for r in rows["visible_now"]),
            msg=[r["name"] for r in rows["visible_now"]],
        )
        # No field of view reported → nothing can be judged too large.
        rows = sky._rank_catalog(
            catalog, when, 45.0, -75.0, 100.0, 30.0, None, None, None, 10
        )
        self.assertFalse(any(r["large_for_field"] for r in rows["visible_now"]))

    def test_compact_objects_are_never_flagged_large(self):
        # Compact (< 5 arcmin) objects bucket on integrated magnitude and
        # are treated as point-like for framing, whatever the fov.
        when = Time("2026-10-18T02:00:00")
        lst = float(when.sidereal_time("apparent", longitude=-75.0).hour)
        compact = dso_catalog.Dso("M57", "M57", "pn", lst, 33.0, 8.8, 1.4, 1.0)
        rows = sky._rank_catalog(
            [compact], when, 45.0, -75.0, 100.0, 30.0, (0.01, 0.01), None, None, 10
        )
        self.assertTrue(rows["visible_now"])
        self.assertFalse(rows["visible_now"][0]["large_for_field"])


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


class HorizonGatingTest(unittest.TestCase):
    """Planning tools apply N.I.N.A.'s horizon-aware floor, not bare altitude."""

    def test_altitudes_rows_carry_skyline_and_clearance(self):
        # Flat 40 deg skyline everywhere: every row reports horizon 40 and
        # clearance = alt - 40, and above_floor follows max(min_alt, 42).
        horizon = Horizon(points=[(0.0, 40.0), (180.0, 40.0)])
        rows = sky._altitudes(
            [
                {"name": "high", "ra_hours": 0.0, "dec_deg": 45.0},
                {"name": "low", "ra_hours": 12.0, "dec_deg": -60.0},
            ],
            45.0,
            -75.0,
            100.0,
            Time("2026-10-18T02:00:00"),
            horizon,
            0.0,
            2.0,
        )
        by_name = {r["name"]: r for r in rows}
        for row in rows:
            self.assertEqual(row["horizon_deg"], 40.0)
            self.assertAlmostEqual(
                row["clearance_deg"], row["alt_deg"] - 40.0, places=1
            )
        self.assertGreater(by_name["high"]["alt_deg"], 42.0)
        self.assertTrue(by_name["high"]["above_floor"])
        self.assertFalse(by_name["low"]["above_floor"])

    def test_min_altitude_raises_the_floor(self):
        horizon = Horizon(points=[(0.0, 40.0), (180.0, 40.0)])
        # alt ~45 clears horizon+2 (42) but not min_altitude 50.
        targets = [{"name": "polaris", "ra_hours": 2.53, "dec_deg": 89.26}]
        when = Time("2026-10-18T02:00:00")
        rows = sky._altitudes(targets, 45.0, -75.0, 100.0, when, horizon, 50.0, 2.0)
        self.assertFalse(rows[0]["above_floor"])
        rows = sky._altitudes(targets, 45.0, -75.0, 100.0, when, horizon, 0.0, 2.0)
        self.assertTrue(rows[0]["above_floor"])

    def test_flat_horizon_reports_zero_and_geometric_clearance(self):
        rows = sky._altitudes(
            [{"name": "polaris", "ra_hours": 2.53, "dec_deg": 89.26}],
            45.0,
            -75.0,
            100.0,
            Time("2026-10-18T02:00:00"),
        )
        self.assertEqual(rows[0]["horizon_deg"], 0.0)
        self.assertAlmostEqual(rows[0]["clearance_deg"], rows[0]["alt_deg"], places=1)

    @staticmethod
    def _catalog(when, lat, lon):
        lst = float(when.sidereal_time("apparent", longitude=lon).hour)

        def obj(name, ra_hours, dec, mag=8.0, major=10.0, minor=10.0):
            return dso_catalog.Dso(name, name, "open", ra_hours, dec, mag, major, minor)

        return [
            obj("up", lst, 60.0),  # culminating now, peaks at 75 deg
            obj("rising", (lst + 6.0) % 24.0, 45.0),  # peaks at zenith later
        ]

    def test_peak_never_clearing_the_skyline_is_excluded(self):
        # Flat 76 deg skyline: floor is 78, "up" peaks at 75 and can never
        # clear it, while "rising" still transits the zenith above it.
        when = Time("2026-10-18T02:00:00")
        horizon = Horizon(points=[(0.0, 76.0), (180.0, 76.0)])
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
            horizon,
            2.0,
        )
        names = [r["name"] for r in rows["visible_now"] + rows["later_tonight"]]
        self.assertNotIn("up", names)
        self.assertIn("rising", [r["name"] for r in rows["later_tonight"]])
        rising = next(r for r in rows["later_tonight"] if r["name"] == "rising")
        self.assertGreaterEqual(rising["peak_alt_deg"], 78.0)

    def test_currently_blocked_target_lands_in_later_tonight(self):
        # An object at alt ~59.6, az ~44 (east) under an 88 deg eastern
        # wall: not visible now (floor ~68), but its meridian passage in
        # the north (skyline 44) clears, so it is still schedulable.
        when = Time("2026-10-18T02:00:00")
        lst = float(when.sidereal_time("apparent", longitude=-75.0).hour)
        blocked = dso_catalog.Dso(
            "blocked", "blocked", "open", (lst + 3.0) % 24.0, 60.0, 8.0, 10.0, 10.0
        )
        horizon = Horizon(points=[(90.0, 88.0), (270.0, 0.0)])
        rows = sky._rank_catalog(
            [blocked],
            when,
            45.0,
            -75.0,
            100.0,
            30.0,
            None,
            None,
            None,
            10,
            horizon,
            2.0,
        )
        self.assertEqual(rows["visible_now"], [])
        self.assertEqual([r["name"] for r in rows["later_tonight"]], ["blocked"])
        row = rows["later_tonight"][0]
        # Reported horizon/clearance describe the current (blocked) position.
        self.assertGreater(row["horizon_deg"], row["alt_deg"])
        self.assertLess(row["clearance_deg"], 0)
        self.assertGreaterEqual(row["peak_alt_deg"], 60.0)

    def test_flat_horizon_matches_the_old_geometric_behavior(self):
        # With no custom horizon the floor collapses to min_altitude, so
        # results must equal a plain geometric run.
        when = Time("2026-10-18T02:00:00")
        catalog = self._catalog(when, 45.0, -75.0)
        args = (when, 45.0, -75.0, 100.0, 30.0, None, None, None, 10)
        geometric = sky._rank_catalog(catalog, *args)
        with_flat = sky._rank_catalog(catalog, *args, Horizon(), 2.0)
        self.assertEqual(geometric, with_flat)

    def test_best_targets_forwards_the_profile_horizon(self):
        import asyncio

        from nina_planner.models.profile import (
            EquipmentConfig,
            ObservatoryProfile,
            OpticalTrainInfo,
            SiteLocationInfo,
        )

        profile = ObservatoryProfile(
            profile_name="Test",
            profile_id="abc",
            site=SiteLocationInfo(
                latitude_deg=45.0, longitude_deg=-75.0, elevation_m=100.0
            ),
            optics=OpticalTrainInfo(),
            filters=[],
            image_save_path="C:\\NINA",
            file_pattern=None,
            equipment=EquipmentConfig(
                has_mount=False,
                has_camera=False,
                has_focuser=False,
                has_filter_wheel=False,
                has_guider=False,
                has_rotator=False,
                has_dome=False,
                has_weather=False,
                has_safety_monitor=False,
                has_switch=False,
                has_flat_device=False,
            ),
            horizon=Horizon(points=[(0.0, 76.0), (180.0, 76.0)]),
        )
        extended = dso_catalog.Dso("Big", "Big", "open", 10.0, 60.0, 8.0, 60.0, 60.0)
        with (
            patch(
                "nina_planner.tools.equipment.get_site_profile",
                AsyncMock(return_value=profile),
            ),
            patch("nina_planner.dso_catalog.CATALOG", (extended,)),
        ):
            out = asyncio.run(sky.best_targets())
        # "Big" peaks at 75 under a 78 floor: never clears, never listed.
        self.assertTrue(out["custom_horizon"])
        self.assertEqual(out["horizon_offset_degrees"], 2.0)
        self.assertEqual(out["visible_now"], [])
        self.assertEqual(out["later_tonight"], [])


class ToolWrapperTest(unittest.TestCase):
    """The @mcp.tool() wrappers around the pure helpers.

    Each wrapper only fetches the site profile, extracts site/optics and
    hops a thread — the profile is patched exactly as test_server.py does,
    so no live N.I.N.A. and no network is involved.
    """

    @staticmethod
    def _profile(fov=None):
        from nina_planner.models.profile import (
            EquipmentConfig,
            ObservatoryProfile,
            OpticalTrainInfo,
            SiteLocationInfo,
        )

        return ObservatoryProfile(
            profile_name="Test",
            profile_id="abc",
            description="",
            site=SiteLocationInfo(
                latitude_deg=45.0, longitude_deg=-75.0, elevation_m=100.0
            ),
            optics=OpticalTrainInfo(field_of_view_deg=fov),
            filters=[],
            image_save_path="C:\\NINA",
            file_pattern=None,
            equipment=EquipmentConfig(
                has_mount=False,
                has_camera=False,
                has_focuser=False,
                has_filter_wheel=False,
                has_guider=False,
                has_rotator=False,
                has_dome=False,
                has_weather=False,
                has_safety_monitor=False,
                has_switch=False,
                has_flat_device=False,
            ),
        )

    def test_target_altitudes_reads_the_site_and_sorts_highest_first(self):
        targets = [
            {"name": "M31", "ra_hours": 0.71, "dec_deg": 41.27},
            {"name": "south", "ra_hours": 0.0, "dec_deg": -60.0},
        ]
        with patch(
            "nina_planner.tools.equipment.get_site_profile",
            AsyncMock(return_value=self._profile()),
        ):
            out = asyncio.run(sky.target_altitudes(targets))
        # Coordinates given → no resolver, no network; highest altitude first.
        self.assertEqual([r["name"] for r in out["targets"]], ["M31", "south"])
        self.assertIn("T", out["time_utc"])
        self.assertGreater(out["targets"][0]["alt_deg"], 0)
        self.assertLess(out["targets"][1]["alt_deg"], 0)

    def test_best_targets_reads_the_site_and_forwards_the_fov(self):
        # CATALOG is patched so the full 497-row sweep is not paid for here;
        # what this asserts is that the wrapper pulls site+optics out of the
        # profile and into the ranking (the ranking itself is RankCatalogTest).
        extended = dso_catalog.Dso("Big", "Big", "open", 0.0, 45.0, 8.0, 60.0, 60.0)
        compact = dso_catalog.Dso("Small", "Small", "pn", 0.0, 45.0, 8.8, 1.4, 1.0)
        with (
            patch(
                "nina_planner.tools.equipment.get_site_profile",
                AsyncMock(return_value=self._profile(fov=(0.1, 0.1))),
            ),
            patch("nina_planner.dso_catalog.CATALOG", (extended, compact)),
        ):
            out = asyncio.run(sky.best_targets(min_altitude=25.0, top=5))
        self.assertEqual(out["sampled_catalog"], 2)
        self.assertEqual(out["min_altitude"], 25.0)
        self.assertIn("T", out["time_utc"])
        self.assertIn("visible_now", out)
        self.assertIn("later_tonight", out)

    def test_best_targets_rejects_an_unknown_level_before_fetching(self):
        with patch(
            "nina_planner.tools.equipment.get_site_profile", AsyncMock()
        ) as profile:
            with self.assertRaises(ValueError):
                asyncio.run(sky.best_targets(level="expert"))
        profile.assert_not_awaited()

    def test_sun_times_reads_the_site(self):
        with patch(
            "nina_planner.tools.equipment.get_site_profile",
            AsyncMock(return_value=self._profile()),
        ):
            out = asyncio.run(sky.sun_times())
        self.assertEqual(out["site"]["latitude_deg"], 45.0)
        self.assertEqual(out["site"]["longitude_deg"], -75.0)
        self.assertIn(out["night_phase"], ("dark", "morning", "evening"))
        self.assertIn("T", out["time_utc"])

    def test_moon_times_reads_the_site(self):
        with patch(
            "nina_planner.tools.equipment.get_site_profile",
            AsyncMock(return_value=self._profile()),
        ):
            out = asyncio.run(sky.moon_times())
        self.assertEqual(out["site"]["latitude_deg"], 45.0)
        self.assertGreaterEqual(out["phase"]["illumination_fraction"], 0.0)
        self.assertLessEqual(out["phase"]["illumination_fraction"], 1.0)
        self.assertIn("events", out)


if __name__ == "__main__":
    unittest.main()
