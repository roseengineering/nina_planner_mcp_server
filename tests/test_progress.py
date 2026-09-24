import unittest
from datetime import UTC, datetime, timedelta

from nina_planner.models.plan import ObservationPlan
from nina_planner.progress import filter_metadata_rows, plan_progress, plan_with_remaining


def _row(
    image_type="LIGHT",
    filter_name="LP",
    duration="60.0",
    target: str | None = "M31 [plan-abc123]",
    date: str | None = None,
):
    if date is None:
        date = datetime.now(UTC).date().isoformat()
    row = {
        "image_type": image_type,
        "filter_name": filter_name,
        "duration": duration,
        "date": date,
    }
    if target is not None:
        row["file_path"] = f"C:/NINA/{date}/LIGHT/{target}__60.00s_0000.fits"
    return row


def _plan(**overrides):
    data = {
        "plan_id": "plan-abc123",
        "target": "M31",
        "ra_hours": 0.71,
        "dec_deg": 41.27,
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
            _row(target="M31 [plan-abc123]"),
            _row(target="M31 [plan-abc123]"),
            _row(target="M31 [other-id]"),  # different plan, ignored
            _row(target="M31"),  # no embedded id, ignored
        ]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 2)
        self.assertEqual(lights["remaining_count"], 18)

    def test_derived_plan_id_matching(self):
        plan = _plan(plan_id="")
        self.assertEqual(plan.plan_id, "")
        pid = plan.effective_plan_id()
        rows = [
            _row(target=f"M31 [{pid}]"),
            _row(target=f"M31 [{pid}]"),
        ]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 2)
        self.assertEqual(lights["remaining_count"], 18)

    def test_other_target_not_counted(self):
        plan = _plan()
        rows = [_row(target="NGC 7331")]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 0)
        self.assertEqual(lights["remaining_count"], 20)

    def test_filter_and_exposure_match(self):
        plan = _plan()
        rows = [
            _row(filter_name="SII"),  # wrong filter
            _row(duration="30.0"),  # wrong exposure
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
            _row(image_type="DARK", target="M31 [plan-x]"),
            _row(image_type="FLAT", target="M31 [plan-x]"),
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


class PlanWithRemainingTest(unittest.TestCase):
    def test_reduces_and_drops_completed(self):
        plan = _plan()
        rows = [
            _row(),
            _row(),
            _row(),
            _row(),
        ]  # 4 of 20 lights
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
        counts = {l.filter_name: l.total_count for l in reduced.light}
        self.assertEqual(counts["L"], 5)
        self.assertEqual(counts["SII"], 2)


class CalibrationAgeTest(unittest.TestCase):
    def test_calibration_max_age_days_default_and_override(self):
        self.assertEqual(_plan().calibration_max_age_days, 7)
        self.assertEqual(_plan(calibration_max_age_days=0).calibration_max_age_days, 0)

    def test_stale_calibration_excluded_from_progress(self):
        plan = _plan()
        today = datetime.now(UTC).date().isoformat()
        stale = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
        rows = [
            _row(image_type="DARK", duration="60.0", target=None, date=today),
            _row(image_type="DARK", duration="60.0", target=None, date=stale),
        ]
        prog = plan_progress(plan, rows)
        self.assertEqual(prog["dark"][0]["acquired_count"], 1)
        self.assertEqual(prog["dark"][0]["remaining_count"], 9)

    def test_calibration_missing_date_excluded(self):
        plan = _plan()
        row = _row(image_type="BIAS", target=None)
        del row["date"]
        prog = plan_progress(plan, [row])
        self.assertEqual(prog["bias"][0]["acquired_count"], 0)

    def test_zero_age_window_counts_only_today(self):
        plan = _plan(calibration_max_age_days=0)
        today = datetime.now(UTC).date().isoformat()
        yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        rows = [
            _row(image_type="FLAT", filter_name="LP", duration="5.0", target=None, date=today),
            _row(image_type="FLAT", filter_name="LP", duration="5.0", target=None, date=yesterday),
        ]
        prog = plan_progress(plan, rows)
        self.assertEqual(prog["flat"][0]["acquired_count"], 1)


class FilterMetadataRowsTest(unittest.TestCase):
    def _ref(self):
        return datetime.now(UTC).date()

    def test_lights_match_plan_id(self):
        plan = _plan()
        rows = [
            _row(target="M31 [plan-abc123]"),
            _row(target="M31 [other-id]"),
            _row(target="M31"),
        ]
        out = filter_metadata_rows(plan, rows, "light")
        self.assertEqual(len(out), 1)
        self.assertIn("plan-abc123", out[0]["file_path"])

    def test_dark_matches_exposure_and_age(self):
        plan = _plan()
        ref = self._ref()
        stale = (ref - timedelta(days=30)).isoformat()
        rows = [
            _row(image_type="DARK", duration="60.0", target=None),
            _row(image_type="DARK", duration="60.0", target=None, date=stale),
            _row(image_type="DARK", duration="300.0", target=None),
        ]
        out = filter_metadata_rows(plan, rows, "dark", max_age_days=7, reference_date=ref)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["duration"], "60.0")

    def test_flat_matches_filter_and_exposure(self):
        plan = _plan()
        rows = [
            _row(image_type="FLAT", filter_name="LP", duration="5.0", target=None),
            _row(image_type="FLAT", filter_name="SII", duration="5.0", target=None),
            _row(image_type="FLAT", filter_name="LP", duration="10.0", target=None),
        ]
        out = filter_metadata_rows(
            plan, rows, "flat", max_age_days=7, reference_date=self._ref()
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["filter_name"], "LP")

    def test_bias_returns_within_window(self):
        plan = _plan()
        ref = self._ref()
        stale = (ref - timedelta(days=3)).isoformat()
        rows = [
            _row(image_type="BIAS", target=None),
            _row(image_type="BIAS", target=None, date=stale),
        ]
        out = filter_metadata_rows(plan, rows, "bias", max_age_days=0, reference_date=ref)
        self.assertEqual(len(out), 1)

    def test_missing_date_excluded_when_window_set(self):
        plan = _plan()
        row = _row(image_type="BIAS", target=None)
        del row["date"]
        out = filter_metadata_rows(
            plan, [row], "bias", max_age_days=7, reference_date=self._ref()
        )
        self.assertEqual(out, [])

    def test_empty_result(self):
        plan = _plan()
        self.assertEqual(filter_metadata_rows(plan, [], "light"), [])
        self.assertEqual(filter_metadata_rows(plan, [], "dark"), [])


if __name__ == "__main__":
    unittest.main()
