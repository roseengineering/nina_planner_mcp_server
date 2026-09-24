import unittest

from nina_planner.models.plan import ObservationPlan
from nina_planner.progress import plan_progress, plan_with_remaining


def _row(
    image_type="LIGHT",
    filter_name="LP",
    duration="60.0",
    target: str | None = "M31",
):
    row = {"ImageType": image_type, "FilterName": filter_name, "Duration": duration}
    if target is not None:
        row["TargetName"] = target
    return row


def _plan(**overrides):
    data = {
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
            _row(target="M31"),  # legacy frame, counted by target fallback
        ]
        lights = plan_progress(plan, rows)["light"][0]
        self.assertEqual(lights["acquired_count"], 3)
        self.assertEqual(lights["remaining_count"], 17)

    def test_legacy_target_name_matching(self):
        plan = _plan()
        self.assertEqual(plan.plan_id, "")
        rows = [_row(target="M31"), _row(target="M31")]
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
        row["HFR"] = str(hfr)
        row["DetectedStars"] = str(stars)
        return row

    def _guiding_rms_row(self, rms_arcsec, hfr=1.0, stars=10):
        row = _row()
        row["HFR"] = str(hfr)
        row["DetectedStars"] = str(stars)
        row["GuidingRMSArcSec"] = str(rms_arcsec)
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
            _row(target="M31"),
        ]  # second has no HFR/DetectedStars
        lights = plan_progress(plan, rows, max_hfr=2.5)["light"][0]
        self.assertEqual(lights["acquired_count"], 1)

    def test_no_filter_counts_all(self):
        plan = _plan()
        rows = [self._hfr_row(9.0), _row(target="M31")]
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
        rows = [self._guiding_rms_row(1.5), _row(target="M31")]
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
            _row(target="M31"),
            _row(target="M31"),
            _row(target="M31"),
            _row(target="M31"),
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
        rows = [_row(target="M31") for _ in range(20)]
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
        rows = [_row(filter_name="SII", target="M31") for _ in range(3)]
        progress = plan_progress(plan, rows)
        reduced = plan_with_remaining(plan, progress)

        self.assertEqual(len(reduced.light), 2)
        counts = {l.filter_name: l.total_count for l in reduced.light}
        self.assertEqual(counts["L"], 5)
        self.assertEqual(counts["SII"], 2)


if __name__ == "__main__":
    unittest.main()
