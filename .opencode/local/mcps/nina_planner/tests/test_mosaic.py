import math
import unittest

from nina_planner.models.profile import OpticalTrainInfo
from nina_planner.mosaic import (
    _spherical_offset,
    grid_size,
    pane_fov_arcmins,
    plan_mosaic,
)

# The rig the profile-derived tests size against: 600mm scope, 3.76um
# pixels, 3000x2000 sensor -> ~64.63' x 43.09' per pane.
TEST_OPTICS = OpticalTrainInfo(
    focal_length_mm=600.0,
    pixel_size_microns=3.76,
    camera_xsize_px=3000,
    camera_ysize_px=2000,
)


class PaneFovTest(unittest.TestCase):
    def test_known_values(self):
        # fov = 2 * atan(sensor_mm / (2 * focal_length)) in arcmins:
        # 11.28mm wide -> 64.63', 7.52mm high -> 43.09'.
        width, height = pane_fov_arcmins(TEST_OPTICS)
        self.assertAlmostEqual(width, 64.63, places=2)
        self.assertAlmostEqual(height, 43.09, places=2)

    def test_missing_optics_raise_naming_the_values(self):
        # No default: a mosaic sized against a guessed FOV would be sized
        # wrong, so every missing value is named instead.
        with self.assertRaises(ValueError) as ctx:
            pane_fov_arcmins(OpticalTrainInfo())
        msg = str(ctx.exception)
        for name in (
            "focal_length_mm",
            "pixel_size_microns",
            "camera_xsize_px",
            "camera_ysize_px",
        ):
            self.assertIn(name, msg)

    def test_zero_pixel_size_is_missing_not_zero_fov(self):
        optics = TEST_OPTICS.model_copy(update={"pixel_size_microns": 0})
        with self.assertRaisesRegex(ValueError, "pixel_size_microns"):
            pane_fov_arcmins(optics)


class GridSizeTest(unittest.TestCase):
    """cols/rows derived from coverage against a 60' x 40' pane, 20% overlap
    (step 48' x 32')."""

    def coverage(self, width, height, overlap=20.0):
        return grid_size(width, height, 60.0, 40.0, overlap)

    def test_coverage_within_one_pane_is_1x1(self):
        self.assertEqual(self.coverage(60.0, 40.0), (1, 1))  # exact fit
        self.assertEqual(self.coverage(12.0, 5.0), (1, 1))  # well inside

    def test_just_over_one_pane_is_two(self):
        self.assertEqual(self.coverage(60.1, 40.1), (2, 2))

    def test_exact_two_pane_span(self):
        # 60 + 48 = 108 across, 40 + 32 = 72 down: exactly two panes.
        self.assertEqual(self.coverage(108.0, 72.0), (2, 2))

    def test_over_cover_never_under_covers(self):
        # The safety direction: a span that needs 2.1 panes gets 3, never 2
        # with a gap at the edge.
        self.assertEqual(self.coverage(108.1, 72.1), (3, 3))

    def test_never_under_covers_any_coverage(self):
        for coverage in [x / 2.0 for x in range(2, 600)]:
            cols, rows = self.coverage(coverage, coverage)
            pane_w, pane_h, step_w, step_h = 60.0, 40.0, 48.0, 32.0
            achieved_w = pane_w + (cols - 1) * step_w
            achieved_h = pane_h + (rows - 1) * step_h
            with self.subTest(coverage=coverage):
                self.assertGreaterEqual(achieved_w, coverage)
                self.assertGreaterEqual(achieved_h, coverage)

    def test_axes_are_independent(self):
        # Wide but short: four columns, one row.
        self.assertEqual(self.coverage(200.0, 10.0), (4, 1))

    def test_small_overlap_still_covers(self):
        # Near-abutting panes (1% overlap -> step 59.4'/39.6'): coverage
        # 121' over a 60' pane needs 3 (60, 119.4, 178.8), never 2 with a
        # 1.6' gap.
        self.assertEqual(self.coverage(121.0, 41.0, overlap=1.0), (3, 2))

    def test_invalid_overlap_rejected(self):
        for overlap in (100.0, 150.0, -1.0, 0.0):
            with self.subTest(overlap=overlap):
                with self.assertRaises(ValueError):
                    self.coverage(100.0, 100.0, overlap=overlap)

    def test_nonpositive_coverage_rejected(self):
        with self.assertRaises(ValueError):
            grid_size(0.0, 40.0, 60.0, 40.0, 20.0)
        with self.assertRaises(ValueError):
            grid_size(60.0, -5.0, 60.0, 40.0, 20.0)

    def test_nonpositive_pane_rejected(self):
        with self.assertRaises(ValueError):
            grid_size(60.0, 40.0, 0.0, 40.0, 20.0)


class SphericalOffsetTest(unittest.TestCase):
    """The SkyOffsetFrame rotation that replaced astropy's
    spherical_offsets_by: exact at the equator, correct across the pole and
    across RA 0/24."""

    def test_due_east_at_equator(self):
        ra, dec = _spherical_offset(0.0, 0.0, 10.0, 0.0)
        self.assertAlmostEqual(math.degrees(ra), 10.0, places=9)
        self.assertAlmostEqual(math.degrees(dec), 0.0, places=9)

    def test_due_north_at_equator(self):
        ra, dec = _spherical_offset(0.0, 0.0, 0.0, 10.0)
        self.assertAlmostEqual(math.degrees(ra), 0.0, places=9)
        self.assertAlmostEqual(math.degrees(dec), 10.0, places=9)

    def test_zero_offset_is_the_center(self):
        ra, dec = _spherical_offset(1.234, 0.5, 0.0, 0.0)
        self.assertAlmostEqual(ra, 1.234, places=12)
        self.assertAlmostEqual(dec, 0.5, places=12)

    def test_ra_wraps_across_midnight(self):
        # 358.5 degrees east by 3 lands at 1.5 degrees — wrapped, not
        # clamped to [0, 360) minus an overshoot, and never negative.
        ra, dec = _spherical_offset(math.radians(358.5), 0.0, 3.0, 0.0)
        self.assertAlmostEqual(math.degrees(ra), 1.5, places=9)
        self.assertGreaterEqual(ra, 0.0)
        self.assertLess(ra, 2.0 * math.pi)

    def test_north_over_the_pole_lands_beyond_it(self):
        # From dec 89.9 due north by 1 degree crosses the pole and comes
        # down the far side at dec 89.1 (a meridian walk adds the offset to
        # dec exactly) with RA flipped 180 degrees.
        ra, dec = _spherical_offset(math.radians(75.0), math.radians(89.9), 0.0, 1.0)
        self.assertAlmostEqual(math.degrees(dec), 89.1, places=6)
        self.assertAlmostEqual(math.degrees(ra), 255.0, places=6)  # 17h

    def test_stays_valid_near_the_pole_for_large_offsets(self):
        # A big grid at a high declination must still produce parseable
        # coordinates: RA in range, dec in range.
        ra, dec = _spherical_offset(math.radians(350.0), math.radians(88.0), 40.0, 40.0)
        self.assertGreaterEqual(ra, 0.0)
        self.assertLess(ra, 2.0 * math.pi)
        self.assertLessEqual(abs(math.degrees(dec)), 90.0)


class PlanMosaicTest(unittest.TestCase):
    def test_single_pane_is_the_center(self):
        [p] = plan_mosaic(10.5, 30.0, 1, 1, 1.0, 1.0, 0.0)
        self.assertAlmostEqual(p.ra_hours, 10.5, places=6)
        self.assertAlmostEqual(p.dec_deg, 30.0, places=6)
        self.assertEqual(p.label, "r1_c1")
        self.assertEqual(p.row, 1)
        self.assertEqual(p.column, 1)
        self.assertEqual(p.position_angle_deg, 0.0)

    def test_count_labels_and_metadata_row_major(self):
        pointings = plan_mosaic(5.0, 10.0, 3, 2, 1.0, 1.0, 0.0)
        self.assertEqual(len(pointings), 6)
        self.assertEqual(
            [p.label for p in pointings],
            ["r1_c1", "r1_c2", "r1_c3", "r2_c1", "r2_c2", "r2_c3"],
        )
        self.assertEqual([p.row for p in pointings], [1, 1, 1, 2, 2, 2])
        self.assertEqual([p.column for p in pointings], [1, 2, 3, 1, 2, 3])

    def test_grid_is_symmetric_about_center(self):
        # At dec 0 an east offset is a pure RA offset, so the three columns
        # of a 3x3 land exactly 1 degree either side of the center, on all
        # three rows.
        pointings = plan_mosaic(8.0, 0.0, 3, 3, 1.0, 1.0, 0.0)
        for p in pointings:
            with self.subTest(label=p.label):
                expected = {1: -1.0, 2: 0.0, 3: 1.0}[p.column]
                self.assertAlmostEqual((p.ra_hours - 8.0) * 15.0, expected, places=4)

    def test_rows_step_south_and_spacing_is_the_step(self):
        # Row 1 is the top (north); on the center column (no east offset)
        # a north/south offset shifts dec by exactly the offset, so the
        # row-to-row dec difference IS the step height.
        pointings = plan_mosaic(3.0, 40.0, 3, 3, 0.5, 1.25, 0.0)
        r1 = next(p for p in pointings if p.row == 1 and p.column == 2)
        r2 = next(p for p in pointings if p.row == 2 and p.column == 2)
        r3 = next(p for p in pointings if p.row == 3 and p.column == 2)
        self.assertGreater(r1.dec_deg, r2.dec_deg)
        self.assertGreater(r2.dec_deg, r3.dec_deg)
        self.assertAlmostEqual(r1.dec_deg - r2.dec_deg, 1.25, places=6)
        self.assertAlmostEqual(r2.dec_deg - r3.dec_deg, 1.25, places=6)

    def test_columns_step_in_ra_at_the_equator(self):
        # Exact at dec 0, then rounded to 6 dp of hours for the file (~0.5"),
        # so compare at 4 dp of degrees.
        pointings = plan_mosaic(8.0, 0.0, 3, 1, 2.0, 1.0, 0.0)
        ras = [p.ra_hours for p in pointings]
        self.assertAlmostEqual((ras[1] - ras[0]) * 15.0, 2.0, places=4)
        self.assertAlmostEqual((ras[2] - ras[1]) * 15.0, 2.0, places=4)

    def test_position_angle_rotates_the_grid(self):
        # At PA 90 the rows axis (north/south) rotates onto RA: with one
        # column the panes separate in RA instead of dec. Run at dec 0 so an
        # east offset is a pure RA offset (away from the equator a
        # great-circle east walk also drifts dec, which this test is not
        # about).
        pointings = plan_mosaic(6.0, 0.0, 1, 2, 1.0, 0.5, 90.0)
        self.assertTrue(all(p.dec_deg == pointings[0].dec_deg for p in pointings))
        self.assertEqual(pointings[0].dec_deg, 0.0)
        dra_hours = pointings[1].ra_hours - pointings[0].ra_hours
        self.assertAlmostEqual(dra_hours * 15.0, 0.5, places=4)
        self.assertTrue(all(p.position_angle_deg == 90.0 for p in pointings))

    def test_zero_pa_leaves_dec_symmetric(self):
        pointings = plan_mosaic(6.0, 20.0, 1, 2, 1.0, 0.5, 0.0)
        decs = sorted(p.dec_deg for p in pointings)
        self.assertAlmostEqual(decs[1] - 20.0, 20.0 - decs[0], places=6)
        self.assertTrue(all(p.position_angle_deg == 0.0 for p in pointings))

    def test_pane_centers_wrap_across_ra_zero(self):
        # A grid straddling 0h: every center must stay in [0, 24) hours —
        # wrapped, not clamped or dropped.
        pointings = plan_mosaic(23.9, 0.0, 4, 1, 1.0, 1.0, 0.0)
        ras = [p.ra_hours for p in pointings]
        self.assertTrue(all(0.0 <= ra < 24.0 for ra in ras), msg=ras)
        self.assertEqual(sorted(ras), [0.0, 23.8, 23.866667, 23.933333])

    def test_zero_grid_rejected(self):
        with self.assertRaises(ValueError):
            plan_mosaic(5.0, 10.0, 0, 2, 1.0, 1.0, 0.0)
        with self.assertRaises(ValueError):
            plan_mosaic(5.0, 10.0, 2, 0, 1.0, 1.0, 0.0)

    def test_pole_adjacent_grid_stays_parseable(self):
        # Big steps at dec 89: some panes cross the pole. Every pointing
        # must still be a valid Pointing (ra in [0,24), dec in [-90,90]).
        pointings = plan_mosaic(2.0, 89.0, 3, 3, 5.0, 5.0, 0.0)
        self.assertEqual(len(pointings), 9)
        for p in pointings:
            self.assertGreaterEqual(p.ra_hours, 0.0)
            self.assertLess(p.ra_hours, 24.0)
            self.assertLessEqual(abs(p.dec_deg), 90.0)


if __name__ == "__main__":
    unittest.main()
