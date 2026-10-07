import unittest
from datetime import datetime, timedelta

from pydantic import ValidationError

from nina_planner.models.plan import ObservationPlan
from nina_planner.progress import (
    filter_metadata_rows,
    plan_progress,
    plan_with_remaining,
)


def _row(
    image_type="LIGHT",
    filter_name="LP",
    duration="60.0",
    target: str | None = "M31 (plan-abc123-1)",
    exposure_start: str | None = None,
):
    if exposure_start is None:
        exposure_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = {
        "image_type": image_type,
        "filter_name": filter_name,
        "duration": duration,
        "exposure_start": exposure_start,
    }
    if target is not None:
        row["file_path"] = f"C:/NINA/LIGHT/{target}__60.00s_0000.fits"
    return row


def _plan(**overrides):
    data = {
        "plan_id": "plan-abc123",
        "target": "M31",
        "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27}],
        "light": [
            {"filter_name": "LP", "exposure_time_seconds": 60.0, "total_count": 20}
        ],
        "flat": [
            {"filter_name": "LP", "exposure_time_seconds": 5.0, "total_count": 10}
        ],
        "dark": [{"exposure_time_seconds": 60.0, "total_count": 10}],
        "bias": [{"total_count": 10}],
    }
    data.update(overrides)
    return ObservationPlan(**data)


class PlanProgressTest(unittest.TestCase):
    def test_embedded_plan_id_matching(self):
        plan = _plan(plan_id="plan-abc123")
        rows = [
            _row(target="M31 (plan-abc123-1)"),
            _row(target="M31 (plan-abc123-1)"),
            _row(target="M31 (other-id-1)"),  # different plan, ignored
            _row(target="M31"),  # no embedded id, ignored
        ]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 2)
        self.assertEqual(lights["remaining_count"], 18)

    def test_legacy_bracket_token_not_attributed(self):
        plan = _plan(plan_id="plan-abc123")
        rows = [
            _row(target="M31 [plan-abc123-1]"),
            _row(target="M31 (plan-abc123-1)"),
            _row(target="M31 [plan-abc123-2]"),
        ]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 1)
        self.assertEqual(lights["remaining_count"], 19)

    def test_attribution_token_is_parenthesised(self):
        plan = _plan(plan_id="plan-abc123")
        self.assertEqual(plan.attribution_token(1), "(plan-abc123-1)")
        self.assertEqual(plan.attribution_token(2), "(plan-abc123-2)")

    def test_token_matches_in_any_path_segment(self):
        plan = _plan(plan_id="plan-abc123")
        rows = [
            {
                # token in the target directory under LIGHT
                **_row(target=None),
                "file_path": (
                    r"C:\NINA\2026-09-28\LIGHT"
                    r"\M31 (plan-abc123-1)\frame.fits"
                ),
            },
            {
                # token in the filename
                **_row(target=None),
                "file_path": (
                    "/NINA/2026-09-28/LIGHT/M31 (plan-abc123-1)__60.00s_0000.fits"
                ),
            },
            {
                # token in an ancestor directory: FilePattern
                # $$TARGETNAME$$\$$DATEMINUS12$$\$$IMAGETYPE$$\...
                **_row(target=None),
                "file_path": ("/NINA/M31 (plan-abc123-1)/2026-09-28/LIGHT/frame.fits"),
            },
            {
                # no token anywhere
                **_row(target=None),
                "file_path": "/NINA/2026-09-28/LIGHT/M31/frame.fits",
            },
            {
                # a different plan's token only
                **_row(target=None),
                "file_path": "/NINA/NGC 7331 (other-id-1)/2026-09-28/LIGHT/f.fits",
            },
        ]

        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 3)
        self.assertEqual(lights["remaining_count"], 17)

    def test_derived_plan_id_matching(self):
        plan = _plan(plan_id="")
        self.assertEqual(plan.plan_id, "")
        pid = plan._base_plan_id()
        rows = [
            _row(target=f"M31 ({pid}-1)"),
            _row(target=f"M31 ({pid}-1)"),
        ]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 2)
        self.assertEqual(lights["remaining_count"], 18)

    def test_other_target_not_counted(self):
        plan = _plan()
        rows = [_row(target="NGC 7331 (plan-other-1)")]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)
        self.assertEqual(lights["remaining_count"], 20)

    def test_filter_and_exposure_match(self):
        plan = _plan()
        rows = [
            _row(filter_name="SII", target="M31 (plan-abc123-1)"),  # wrong filter
            _row(duration="30.0", target="M31 (plan-abc123-1)"),  # wrong exposure
            _row(),  # match
        ]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 1)

    def test_calibration_counts(self):
        plan = _plan()
        rows = [
            _row(image_type="FLAT", filter_name="LP", duration="5.0", target=None),
            _row(image_type="FLAT", filter_name="LP", duration="5.0", target=None),
            _row(image_type="DARK", duration="60.0", target=None),
            _row(image_type="BIAS", target=None),
        ]
        prog = plan_progress(plan, rows)
        self.assertEqual(prog["flat"][0]["acquired_count"], 2)
        self.assertEqual(prog["dark"][0]["acquired_count"], 1)
        self.assertEqual(prog["bias"][0]["acquired_count"], 1)

    def test_non_light_rows_excluded_from_lights(self):
        plan = _plan()
        rows = [
            _row(image_type="DARK", target="M31 (plan-x-1)"),
            _row(image_type="FLAT", target="M31 (plan-x-1)"),
        ]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)


class QualityFilterTest(unittest.TestCase):
    def _hfr_row(self, hfr, stars=10):
        row = _row()
        row["hfr"] = str(hfr)
        row["detected_stars"] = str(stars)
        return row

    def _guiding_rms_row(self, rms_arcsec, hfr=1.0, stars=10):
        row = _row()
        row["hfr"] = str(hfr)
        row["detected_stars"] = str(stars)
        row["guiding_rms_arc_sec"] = str(rms_arcsec)
        return row

    def test_max_hfr_excludes_blur(self):
        plan = _plan()
        rows = [self._hfr_row(2.0), self._hfr_row(4.0), self._hfr_row(1.5)]
        lights = plan_progress(plan, rows, max_hfr=2.5)["light"][0]
        self.assertEqual(lights["acquired_count"], 2)

    def test_min_detected_stars_excludes_blank(self):
        plan = _plan()
        rows = [self._hfr_row(2.0, stars=3), self._hfr_row(2.0, stars=50)]
        lights = plan_progress(plan, rows, min_detected_stars=10)["light"][0]
        self.assertEqual(lights["acquired_count"], 1)

    def test_missing_quality_field_excluded_when_filtered(self):
        plan = _plan()
        rows = [
            self._hfr_row(1.5),
            _row(),
        ]  # second has no HFR/DetectedStars
        lights = plan_progress(plan, rows, max_hfr=2.5)["light"][0]
        self.assertEqual(lights["acquired_count"], 1)

    def test_no_filter_counts_all(self):
        plan = _plan()
        rows = [self._hfr_row(9.0), _row()]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 2)

    def test_calibration_unaffected_by_quality(self):
        plan = _plan()
        rows = [
            _row(image_type="FLAT", filter_name="LP", duration="5.0", target=None),
            _row(image_type="DARK", duration="60.0", target=None),
            _row(image_type="BIAS", target=None),
        ]
        prog = plan_progress(plan, rows, max_hfr=1.0, min_detected_stars=50)
        self.assertEqual(prog["flat"][0]["acquired_count"], 1)
        self.assertEqual(prog["dark"][0]["acquired_count"], 1)
        self.assertEqual(prog["bias"][0]["acquired_count"], 1)

    def test_max_guiding_rms_excludes_poor(self):
        plan = _plan()
        rows = [
            self._guiding_rms_row(1.0),
            self._guiding_rms_row(3.0),
            self._guiding_rms_row(2.5),
        ]
        lights = plan_progress(plan, rows, max_guiding_rms_arcsec=2.5)["light"][0]
        self.assertEqual(lights["acquired_count"], 2)

    def test_missing_guiding_rms_excluded_when_filtered(self):
        plan = _plan()
        rows = [self._guiding_rms_row(1.5), _row()]
        lights = plan_progress(plan, rows, max_guiding_rms_arcsec=2.5)["light"][0]
        self.assertEqual(lights["acquired_count"], 1)

    def test_guiding_rms_combined_with_hfr(self):
        plan = _plan()
        rows = [
            self._guiding_rms_row(1.0, hfr=1.5),
            self._guiding_rms_row(3.0, hfr=1.5),
            self._guiding_rms_row(2.0, hfr=3.0),
        ]
        lights = plan_progress(plan, rows, max_hfr=2.5, max_guiding_rms_arcsec=2.5)[
            "light"
        ][0]
        self.assertEqual(lights["acquired_count"], 1)

    def test_nan_hfr_string_rejected_when_threshold_set(self):
        plan = _plan()
        rows = [self._hfr_row("NaN")]
        lights = plan_progress(plan, rows, max_hfr=5.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_lowercase_nan_hfr_rejected(self):
        plan = _plan()
        rows = [self._hfr_row("nan")]
        lights = plan_progress(plan, rows, max_hfr=5.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_inf_hfr_rejected(self):
        plan = _plan()
        rows = [self._hfr_row(float("inf"))]
        lights = plan_progress(plan, rows, max_hfr=5.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_neg_inf_hfr_rejected(self):
        plan = _plan()
        rows = [self._hfr_row(float("-inf"))]
        lights = plan_progress(plan, rows, max_hfr=5.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_hfr_above_threshold_rejected(self):
        plan = _plan()
        rows = [self._hfr_row(6.0)]
        lights = plan_progress(plan, rows, max_hfr=5.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_nan_hfr_accepted_when_no_threshold(self):
        plan = _plan()
        rows = [self._hfr_row("NaN")]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 1)

    def test_nan_rms_string_rejected_when_threshold_set(self):
        plan = _plan()
        rows = [self._guiding_rms_row("NaN")]
        lights = plan_progress(plan, rows, max_guiding_rms_arcsec=2.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_lowercase_nan_rms_rejected(self):
        plan = _plan()
        rows = [self._guiding_rms_row("nan")]
        lights = plan_progress(plan, rows, max_guiding_rms_arcsec=2.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_inf_rms_rejected(self):
        plan = _plan()
        rows = [self._guiding_rms_row(float("inf"))]
        lights = plan_progress(plan, rows, max_guiding_rms_arcsec=2.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_neg_inf_rms_rejected(self):
        plan = _plan()
        rows = [self._guiding_rms_row(float("-inf"))]
        lights = plan_progress(plan, rows, max_guiding_rms_arcsec=2.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_rms_above_threshold_rejected(self):
        plan = _plan()
        rows = [self._guiding_rms_row(3.0)]
        lights = plan_progress(plan, rows, max_guiding_rms_arcsec=2.0)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)

    def test_nan_rms_accepted_when_no_threshold(self):
        plan = _plan()
        rows = [self._guiding_rms_row("NaN")]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 1)


class PlanWithRemainingTest(unittest.TestCase):
    def test_reduces_and_drops_completed(self):
        plan = _plan()
        rows = [_row() for _ in range(4)]  # 4 of 20 lights for pointing 1
        progress = plan_progress(plan, rows)
        reduced = plan_with_remaining(plan, progress)

        self.assertEqual(reduced.light[0].total_count, 16)
        # flats/darks/bias untouched (no matching rows)
        self.assertEqual(reduced.flat[0].total_count, 10)
        self.assertEqual(reduced.dark[0].total_count, 10)
        self.assertEqual(reduced.bias[0].total_count, 10)

    def test_drops_fully_acquired_group(self):
        plan = _plan()
        rows = [_row() for _ in range(20)]
        progress = plan_progress(plan, rows)
        reduced = plan_with_remaining(plan, progress)

        self.assertEqual(reduced.light, [])

    def test_partial_group_survives(self):
        plan = _plan(
            light=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5},
                {"filter_name": "SII", "exposure_time_seconds": 60.0, "total_count": 5},
            ]
        )
        rows = [_row(filter_name="SII") for _ in range(3)]
        progress = plan_progress(plan, rows)
        reduced = plan_with_remaining(plan, progress)

        self.assertEqual(len(reduced.light), 2)
        counts = {exp.filter_name: exp.total_count for exp in reduced.light}
        self.assertEqual(counts["L"], 5)
        self.assertEqual(counts["SII"], 2)


class CalibrationAgeTest(unittest.TestCase):
    def test_calibration_window_days_default_and_override(self):
        self.assertEqual(_plan().calibration_window_days, 7)
        self.assertEqual(_plan(calibration_window_days=1).calibration_window_days, 1)
        # 0 is reserved (ge=1), so it is rejected.
        with self.assertRaises(ValidationError):
            _plan(calibration_window_days=0)

    def test_stale_calibration_excluded_from_progress(self):
        plan = _plan()
        now = datetime.now()
        fresh = now.strftime("%Y-%m-%d %H:%M:%S")
        stale = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            _row(image_type="DARK", duration="60.0", target=None, exposure_start=fresh),
            _row(image_type="DARK", duration="60.0", target=None, exposure_start=stale),
        ]
        prog = plan_progress(plan, rows)
        self.assertEqual(prog["dark"][0]["acquired_count"], 1)
        self.assertEqual(prog["dark"][0]["remaining_count"], 9)

    def test_calibration_missing_time_excluded(self):
        plan = _plan()
        row = _row(image_type="BIAS", target=None)
        del row["exposure_start"]
        prog = plan_progress(plan, [row])
        self.assertEqual(prog["bias"][0]["acquired_count"], 0)

    def test_one_day_window_counts_recent_only(self):
        plan = _plan(calibration_window_days=1)
        now = datetime.now()
        recent = now.strftime("%Y-%m-%d %H:%M:%S")
        old = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            _row(
                image_type="FLAT",
                filter_name="LP",
                duration="5.0",
                target=None,
                exposure_start=recent,
            ),
            _row(
                image_type="FLAT",
                filter_name="LP",
                duration="5.0",
                target=None,
                exposure_start=old,
            ),
        ]
        prog = plan_progress(plan, rows)
        self.assertEqual(prog["flat"][0]["acquired_count"], 1)

    def test_calibration_after_lights_counts_without_reacquisition(self):
        # Session-anchored: darks shot *within the window* after the lights
        # stay counted, so the plan does not keep re-acquiring them on later
        # runs (no infinite loop).
        plan = _plan()
        session = datetime(2026, 9, 1, 22, 0, 0)
        light = _row(exposure_start=session.strftime("%Y-%m-%d %H:%M:%S"))
        dark = _row(
            image_type="DARK",
            duration="60.0",
            target=None,
            exposure_start=(session + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        prog = plan_progress(plan, [light, dark], now=session + timedelta(days=4))
        self.assertEqual(prog["light"][0]["acquired_count"], 1)
        self.assertTrue(prog["dark"][0]["window_open"])
        self.assertEqual(prog["dark"][0]["acquired_count"], 1)
        self.assertEqual(prog["dark"][0]["remaining_count"], 9)

    def test_calibration_outside_window_not_attributed(self):
        # Upper bound: a dark shot well after (earliest light + window) is not
        # attributed to the plan at all.
        plan = _plan()
        session = datetime(2026, 9, 1, 22, 0, 0)
        light = _row(exposure_start=session.strftime("%Y-%m-%d %H:%M:%S"))
        dark = _row(
            image_type="DARK",
            duration="60.0",
            target=None,
            exposure_start=(session + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        prog = plan_progress(plan, [light, dark], now=session + timedelta(days=31))
        self.assertEqual(prog["light"][0]["acquired_count"], 1)
        self.assertEqual(prog["dark"][0]["acquired_count"], 0)

    def test_closed_window_reports_zero_remaining(self):
        # Once `now` is outside the window no frame shot now could ever be
        # credited, so remaining is reported as 0 instead of an unsatisfiable
        # deficit — otherwise every later run would re-acquire the darks.
        plan = _plan()
        session = datetime(2026, 9, 1, 22, 0, 0)
        light = _row(exposure_start=session.strftime("%Y-%m-%d %H:%M:%S"))
        prog = plan_progress(plan, [light], now=session + timedelta(days=60))
        for key in ("flat", "dark", "bias"):
            item = prog[key][0]
            self.assertFalse(item["window_open"])
            self.assertEqual(item["remaining_count"], 0)
        # Lights are unaffected by the window.
        self.assertNotIn("window_open", prog["light"][0])
        self.assertEqual(prog["light"][0]["remaining_count"], 19)

    def test_closed_window_keeps_truthful_acquired_count(self):
        # A closed window zeroes the *demand*, not the record: frames shot
        # inside the window are still reported as acquired.
        plan = _plan()
        session = datetime(2026, 9, 1, 22, 0, 0)
        light = _row(exposure_start=session.strftime("%Y-%m-%d %H:%M:%S"))
        dark = _row(
            image_type="DARK",
            duration="60.0",
            target=None,
            exposure_start=(session + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        prog = plan_progress(plan, [light, dark], now=session + timedelta(days=60))
        self.assertFalse(prog["dark"][0]["window_open"])
        self.assertEqual(prog["dark"][0]["acquired_count"], 1)
        self.assertEqual(prog["dark"][0]["remaining_count"], 0)

    def test_window_end_is_anchor_plus_window(self):
        plan = _plan(calibration_window_days=3)
        session = datetime(2026, 9, 1, 22, 0, 0)
        light = _row(exposure_start=session.strftime("%Y-%m-%d %H:%M:%S"))
        prog = plan_progress(plan, [light], now=session)
        self.assertTrue(prog["dark"][0]["window_open"])
        self.assertEqual(
            prog["dark"][0]["window_end"],
            (session + timedelta(days=3)).isoformat(timespec="seconds"),
        )

    def test_window_open_when_plan_has_no_lights(self):
        # With no lights the anchor is `now`, so the window is open and
        # calibration is still demanded.
        plan = _plan()
        now = datetime(2026, 9, 1, 22, 0, 0)
        prog = plan_progress(plan, [], now=now)
        self.assertTrue(prog["dark"][0]["window_open"])
        self.assertEqual(prog["dark"][0]["remaining_count"], 10)

    def test_pre_session_calibration_older_than_window_excluded(self):
        plan = _plan(calibration_window_days=7)
        session = datetime(2026, 9, 1, 22, 0, 0)
        light = _row(exposure_start=session.strftime("%Y-%m-%d %H:%M:%S"))
        old = _row(
            image_type="DARK",
            duration="60.0",
            target=None,
            exposure_start=(session - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        recent = _row(
            image_type="DARK",
            duration="60.0",
            target=None,
            exposure_start=(session - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        prog = plan_progress(plan, [light, old, recent], now=session)
        # Only the pre-session frame inside the window counts.
        self.assertEqual(prog["dark"][0]["acquired_count"], 1)

    def test_no_lights_falls_back_to_now(self):
        plan = _plan()
        now = datetime.now()
        rows = [
            _row(
                image_type="DARK",
                duration="60.0",
                target=None,
                exposure_start=now.strftime("%Y-%m-%d %H:%M:%S"),
            )
        ]
        prog = plan_progress(plan, rows, now=now)
        self.assertEqual(prog["dark"][0]["acquired_count"], 1)


class FilterMetadataRowsTest(unittest.TestCase):
    def _ref(self):
        return datetime.now()

    def test_lights_match_plan_id(self):
        plan = _plan()
        rows = [
            _row(target="M31 (plan-abc123-1)"),
            _row(target="M31 (other-id-1)"),
            _row(target="M31"),
        ]
        out = filter_metadata_rows(plan, rows, "light")
        self.assertEqual(len(out), 1)
        self.assertIn("plan-abc123-1", out[0]["file_path"])

    def test_dark_matches_exposure_and_age(self):
        plan = _plan()
        ref = self._ref()
        fresh = ref.strftime("%Y-%m-%d %H:%M:%S")
        stale = (ref - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            _row(image_type="DARK", duration="60.0", target=None, exposure_start=fresh),
            _row(image_type="DARK", duration="60.0", target=None, exposure_start=stale),
            _row(
                image_type="DARK", duration="300.0", target=None, exposure_start=fresh
            ),
        ]
        out = filter_metadata_rows(plan, rows, "dark", max_age_days=7, now=ref)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["duration"], "60.0")

    def test_flat_matches_filter_and_exposure(self):
        plan = _plan()
        rows = [
            _row(image_type="FLAT", filter_name="LP", duration="5.0", target=None),
            _row(image_type="FLAT", filter_name="SII", duration="5.0", target=None),
            _row(image_type="FLAT", filter_name="LP", duration="10.0", target=None),
        ]
        out = filter_metadata_rows(plan, rows, "flat", max_age_days=7, now=self._ref())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["filter_name"], "LP")

    def test_bias_returns_within_window(self):
        plan = _plan()
        ref = self._ref()
        fresh = ref.strftime("%Y-%m-%d %H:%M:%S")
        stale = (ref - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            _row(image_type="BIAS", target=None, exposure_start=fresh),
            _row(image_type="BIAS", target=None, exposure_start=stale),
        ]
        out = filter_metadata_rows(plan, rows, "bias", max_age_days=1, now=ref)
        self.assertEqual(len(out), 1)

    def test_missing_time_excluded_when_window_set(self):
        plan = _plan()
        row = _row(image_type="BIAS", target=None)
        del row["exposure_start"]
        out = filter_metadata_rows(plan, [row], "bias", max_age_days=7, now=self._ref())
        self.assertEqual(out, [])

    def test_empty_result(self):
        plan = _plan()
        self.assertEqual(filter_metadata_rows(plan, [], "light"), [])
        self.assertEqual(filter_metadata_rows(plan, [], "dark"), [])


class PointingIndexTest(unittest.TestCase):
    def test_effective_plan_id_appends_index(self):
        plan = _plan(plan_id="plan-abc123")
        self.assertEqual(plan.effective_plan_id(1), "plan-abc123-1")
        self.assertEqual(plan.effective_plan_id(2), "plan-abc123-2")

    def test_pointing_index_isolates_progress(self):
        plan = _plan(
            plan_id="plan-abc123",
            pointings=[
                {"ra_hours": 0.71, "dec_deg": 41.27},
                {"ra_hours": 0.72, "dec_deg": 41.28},
            ],
        )
        rows = [
            _row(target="M31 (plan-abc123-1)"),
            _row(target="M31 (plan-abc123-1)"),
            _row(target="M31 (plan-abc123-2)"),
        ]
        lights_p1 = plan_progress(plan, rows, pointing_index=1)["light"][0]
        lights_p2 = plan_progress(plan, rows, pointing_index=2)["light"][0]
        self.assertEqual(lights_p1["acquired_count"], 2)
        self.assertEqual(lights_p1["remaining_count"], 18)
        self.assertEqual(lights_p2["acquired_count"], 1)
        self.assertEqual(lights_p2["remaining_count"], 19)

    def test_out_of_range_pointing_index_excluded_via_filter(self):
        plan = _plan(pointings=[{"ra_hours": 0.71, "dec_deg": 41.27}])
        with self.assertRaises(ValueError):
            from nina_planner.server import _check_pointing_index

            _check_pointing_index(plan, 0)
        with self.assertRaises(ValueError):
            _check_pointing_index(plan, 2)
        # valid index 1 does not raise
        _check_pointing_index(plan, 1)

    def test_labeled_pointing_targets(self):
        plan = _plan(
            plan_id="plan-abc123",
            pointings=[{"label": "Pane 1", "ra_hours": 0.71, "dec_deg": 41.27}],
        )
        self.assertEqual(plan.pointings[0].label, "Pane 1")
        self.assertEqual(plan.effective_plan_id(1), "plan-abc123-1")


class PrivateHelperDefensivePathsTest(unittest.TestCase):
    """Defensive branches in the private helpers of progress.py. Each test
    targets one of the missed lines reported by coverage."""

    def _plan(self, **overrides):
        data = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27}],
            "light": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias": [{"total_count": 1}],
        }
        data.update(overrides)
        return ObservationPlan(**data)

    # ---- _exposure_matches (lines 57, 60-61) ----

    def test_exposure_matches_returns_false_when_duration_empty(self):
        from nina_planner.progress import _exposure_matches

        self.assertFalse(_exposure_matches({"duration": ""}, 60.0))
        self.assertFalse(_exposure_matches({"duration": None}, 60.0))
        self.assertFalse(_exposure_matches({}, 60.0))

    def test_exposure_matches_returns_false_when_duration_invalid(self):
        from nina_planner.progress import _exposure_matches

        self.assertFalse(_exposure_matches({"duration": "abc"}, 60.0))
        self.assertFalse(_exposure_matches({"duration": "not a number"}, 60.0))

    # ---- _light_target_matches (line 72) ----

    def test_light_target_matches_returns_false_when_file_path_empty(self):
        from nina_planner.progress import _light_target_matches

        plan = self._plan()
        self.assertFalse(_light_target_matches(plan, {}))
        self.assertFalse(_light_target_matches(plan, {"file_path": ""}))
        self.assertFalse(_light_target_matches(plan, {"file_path": "   "}))

    # ---- _quality_accepted (lines 88-89, 95, 98-99, 108-109) ----

    def test_quality_hfr_invalid_string_returns_false(self):
        from nina_planner.progress import _quality_accepted

        # HFR="abc" → float() raises ValueError → returns False.
        self.assertFalse(_quality_accepted({"hfr": "abc"}, 5.0, None))

    def test_quality_stars_empty_string_returns_false(self):
        from nina_planner.progress import _quality_accepted

        # detected_stars missing or "" → returns False.
        self.assertFalse(_quality_accepted({}, None, 10))
        self.assertFalse(_quality_accepted({"detected_stars": ""}, None, 10))

    def test_quality_stars_invalid_string_returns_false(self):
        from nina_planner.progress import _quality_accepted

        # detected_stars="abc" → int() raises ValueError → returns False.
        self.assertFalse(_quality_accepted({"detected_stars": "abc"}, None, 10))

    def test_quality_rms_invalid_string_returns_false(self):
        from nina_planner.progress import _quality_accepted

        # guiding_rms_arc_sec="abc" → float() raises ValueError → returns False.
        self.assertFalse(
            _quality_accepted({"guiding_rms_arc_sec": "abc"}, None, None, 2.0)
        )

    # ---- _within_age ----

    def test_within_age_returns_true_when_max_age_days_none(self):
        from nina_planner.progress import _within_age

        self.assertTrue(
            _within_age(
                {"exposure_start": "2026-09-22 10:00:00"},
                None,
                datetime(2026, 9, 22, 12, 0),
            )
        )

    def test_within_age_returns_true_when_session_start_none(self):
        from nina_planner.progress import _within_age

        self.assertTrue(_within_age({"exposure_start": "2026-09-22 10:00:00"}, 7, None))

    def test_within_age_returns_false_when_time_string_invalid(self):
        from nina_planner.progress import _within_age

        ref = datetime(2026, 9, 22, 12, 0)
        self.assertFalse(_within_age({"exposure_start": "garbage"}, 7, ref))
        self.assertFalse(_within_age({"exposure_start": ""}, 7, ref))
        self.assertFalse(_within_age({"exposure_start": None}, 7, ref))

    def test_within_age_counts_from_session_minus_window(self):
        from nina_planner.progress import _within_age

        session = datetime(2026, 9, 22, 12, 0)
        # At or after session - window counts (inclusive)...
        self.assertTrue(
            _within_age({"exposure_start": "2026-09-20 12:00:00"}, 2, session)
        )
        # ...earlier does not...
        self.assertFalse(
            _within_age({"exposure_start": "2026-09-20 11:59:00"}, 2, session)
        )
        # ...the upper bound is inclusive too...
        self.assertTrue(
            _within_age({"exposure_start": "2026-09-24 12:00:00"}, 2, session)
        )
        # ...and anything beyond it does not count.
        self.assertFalse(
            _within_age({"exposure_start": "2026-09-24 12:01:00"}, 2, session)
        )
        self.assertFalse(
            _within_age({"exposure_start": "2026-10-05 00:00:00"}, 2, session)
        )

    def test_within_age_ignores_offset_not_converts(self):
        from nina_planner.progress import _within_age

        # Session start carries N.I.N.A.'s UTC offset; the wall clock is what
        # counts, so this must not be shifted through the host's timezone.
        session = datetime.fromisoformat("2026-09-22T12:00:00-05:00")
        self.assertTrue(
            _within_age({"exposure_start": "2026-09-20 12:00:00"}, 2, session)
        )
        self.assertFalse(
            _within_age({"exposure_start": "2026-09-20 11:59:00"}, 2, session)
        )
        # A shot value that carries an offset keeps its wall clock too.
        self.assertTrue(
            _within_age({"exposure_start": "2026-09-20T12:00:00-05:00"}, 2, session)
        )

    # ---- filter_metadata_rows (line 230) ----

    def test_filter_metadata_returns_empty_for_unknown_image_type(self):
        from nina_planner.progress import filter_metadata_rows

        plan = self._plan()
        rows = [{"image_type": "LIGHT", "duration": "60.0", "filter_name": "L"}]
        self.assertEqual(filter_metadata_rows(plan, rows, "UNKNOWN"), [])
        # Case-insensitive: lowercase form of an unrecognized type.
        self.assertEqual(filter_metadata_rows(plan, rows, "unrecognized"), [])


if __name__ == "__main__":
    unittest.main()
